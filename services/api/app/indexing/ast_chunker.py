"""Offline AST chunk construction. The Stage 2 indexing factory is unchanged."""

from bisect import bisect_right
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
import hashlib
import json
import logging
from typing import Literal
from uuid import NAMESPACE_URL, uuid5

from app.core.exceptions import ChunkingLimitError
from app.core.config import get_settings
from app.embedding.validation import MAX_EMBEDDING_TEXT_LENGTH
from app.indexing.chunker import SimpleCodeChunker
from app.indexing.models import CodeChunk
from app.parsing.base import CodeParsingError, ParsedSource, SourceSyntaxError, SyntaxSymbol, UnsupportedLanguageError
from app.parsing.registry import ParserRegistry, create_parser_registry
from app.repository.models import RepositoryFile


logger = logging.getLogger(__name__)
AST_CHUNKING_VERSION = "ast-v1"
FALLBACK_CHUNKING_VERSION = "ast-fallback-line-v1"
FallbackReason = Literal["unsupported_language", "syntax_error", "parser_error", "shared_line_boundaries"]


@dataclass(frozen=True, slots=True)
class FileChunkingResult:
    strategy: Literal["ast", "fallback"]
    fallback_reason: FallbackReason | None
    chunks: tuple[CodeChunk, ...]


@dataclass(slots=True)
class _Declaration:
    syntax: SyntaxSymbol
    children: list["_Declaration"] = field(default_factory=list)


class _SharedLineBoundaries(CodeParsingError):
    """Whole-line citations cannot isolate adjacent declarations on one line."""


class ASTCodeChunker:
    """Named declarations plus module/class gaps, with bounded exact line slices.

    Named nested declarations own their lines; their parent owns only the gaps.
    Anonymous bodies stay in the containing declaration/module text. AST slices
    have no overlap. Fallback uses the unchanged Stage 2 line-window algorithm.
    """

    def __init__(
        self, *, registry: ParserRegistry | None = None, chunk_lines: int = 60,
        overlap_lines: int = 10, max_file_bytes: int = 1_048_576,
        max_chunk_characters: int = 8_192, max_chunks: int = 10_000,
    ) -> None:
        self._fallback = SimpleCodeChunker(
            chunk_lines=chunk_lines, overlap_lines=overlap_lines,
            max_file_bytes=max_file_bytes, max_chunk_characters=max_chunk_characters,
            max_chunks=max_chunks,
        )
        if max_chunk_characters > MAX_EMBEDDING_TEXT_LENGTH:
            raise ValueError("AST chunks must fit the embedding text character limit.")
        self.registry = registry if registry is not None else create_parser_registry()
        self.chunk_lines = chunk_lines
        self.overlap_lines = overlap_lines
        self.max_file_bytes = max_file_bytes
        self.max_chunk_characters = max_chunk_characters
        self.max_chunks = max_chunks

    def chunk_file(self, file: RepositoryFile) -> list[CodeChunk]:
        return list(self.chunk_file_result(file).chunks)

    def iter_file_chunks(self, file: RepositoryFile) -> Iterator[CodeChunk]:
        # Validate the entire file before yielding so a later limit failure does
        # not expose a partial set of chunks as a successful file result.
        yield from self.chunk_file_result(file).chunks

    def chunk_file_result(self, file: RepositoryFile) -> FileChunkingResult:
        try:
            try:
                encoded = file.content.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise CodeParsingError("Source cannot be encoded as UTF-8.") from exc
            if max(file.size_bytes, len(encoded)) > self.max_file_bytes:
                raise ChunkingLimitError("A source file exceeds the chunking file byte limit.")
            try:
                parsed = self.registry.parse(file)
                chunks = self._ast_chunks(file, encoded, parsed)
                result = FileChunkingResult("ast", None, tuple(chunks))
            except UnsupportedLanguageError:
                result = self._fallback_result(file, "unsupported_language")
            except SourceSyntaxError:
                result = self._fallback_result(file, "syntax_error")
            except _SharedLineBoundaries:
                result = self._fallback_result(file, "shared_line_boundaries")
            except CodeParsingError:
                result = self._fallback_result(file, "parser_error")
        except (ChunkingLimitError, CodeParsingError):
            logger.info("chunk_file repository=%s path=%s status=failed", file.repository, file.path)
            raise
        logger.info(
            "chunk_file repository=%s path=%s strategy=%s fallback_reason=%s chunks=%d",
            file.repository, file.path, result.strategy, result.fallback_reason, len(result.chunks),
        )
        return result

    def _fallback_result(self, file: RepositoryFile, reason: FallbackReason) -> FileChunkingResult:
        chunks = []
        for chunk in self._fallback.iter_file_chunks(file):
            chunk = replace(
                chunk, chunking_strategy="fallback", chunking_version=FALLBACK_CHUNKING_VERSION,
            )
            chunks.append(replace(chunk, chunk_id=self._chunk_id(chunk, None)))
        return FileChunkingResult("fallback", reason, tuple(chunks))

    def _ast_chunks(self, file: RepositoryFile, encoded: bytes, parsed: ParsedSource) -> list[CodeChunk]:
        if (parsed.repository, parsed.path, parsed.language) != (file.repository, file.path, file.language):
            raise CodeParsingError("Parsed metadata does not belong to the selected source.")
        if not parsed.root_node_type:
            raise CodeParsingError("Parsed metadata has no root node type.")
        lines = file.content.splitlines(keepends=True)
        starts = []
        offset = 0
        for line in lines:
            starts.append(offset)
            offset += len(line.encode("utf-8"))
        roots: list[_Declaration] = []
        stack: list[_Declaration] = []
        for syntax in sorted(parsed.symbols, key=lambda item: (item.start_byte, -item.end_byte)):
            if not (
                all(type(value) is int for value in (
                    syntax.start_byte, syntax.end_byte, syntax.start_line, syntax.end_line,
                ))
                and 0 <= syntax.start_byte < syntax.end_byte <= len(encoded)
                and syntax.start_line == bisect_right(starts, syntax.start_byte)
                and syntax.end_line == bisect_right(starts, syntax.end_byte - 1)
                and syntax.symbol and syntax.name and syntax.node_type
                and syntax.symbol == (f"{syntax.parent_symbol}.{syntax.name}" if syntax.parent_symbol else syntax.name)
            ):
                raise CodeParsingError("A declaration has inconsistent source metadata.")
            while stack and syntax.start_byte >= stack[-1].syntax.end_byte:
                stack.pop()
            siblings = stack[-1].children if stack else roots
            if stack and syntax.end_byte > stack[-1].syntax.end_byte:
                raise CodeParsingError("Declaration source ranges cross each other.")
            if stack and (syntax.start_byte, syntax.end_byte) == (
                stack[-1].syntax.start_byte, stack[-1].syntax.end_byte,
            ):
                raise CodeParsingError("Declaration source ranges are duplicated.")
            if siblings and siblings[-1].syntax.end_line >= syntax.start_line:
                raise _SharedLineBoundaries()
            declaration = _Declaration(syntax)
            siblings.append(declaration)
            stack.append(declaration)

        # Iterative traversal also handles deeply nested declarations. All
        # segments are disjoint whole-line ranges, ordered in original source.
        pending = [(1, len(lines), None, roots)]
        segments: list[tuple[int, int, SyntaxSymbol | None]] = []
        while pending:
            start, end, owner, children = pending.pop()
            cursor = start
            for child in children:
                if cursor < child.syntax.start_line:
                    segments.append((cursor, child.syntax.start_line - 1, owner))
                pending.append((child.syntax.start_line, child.syntax.end_line, child.syntax, child.children))
                cursor = child.syntax.end_line + 1
            if cursor <= end:
                segments.append((cursor, end, owner))
        chunks: list[CodeChunk] = []
        for start, end, owner in sorted(segments, key=lambda item: item[0]):
            # Closing braces/separators alone carry no useful retrieval evidence.
            # No meaningful source text is stripped or synthetically prepended.
            if not "".join(lines[start - 1:end]).strip(" \t\r\n{}();,"):
                continue
            cursor = start
            while cursor <= end:
                slice_end = cursor - 1
                length = 0
                while slice_end < end and slice_end - cursor + 1 < self.chunk_lines:
                    line_length = len(lines[slice_end])
                    if line_length > self.max_chunk_characters:
                        raise ChunkingLimitError("A source line exceeds the chunk character limit.")
                    if length + line_length > self.max_chunk_characters:
                        break
                    length += line_length
                    slice_end += 1
                content = "".join(lines[cursor - 1:slice_end])
                if content.strip():
                    if len(chunks) >= self.max_chunks:
                        raise ChunkingLimitError("Chunking exceeds the chunk count limit.")
                    chunk = CodeChunk(
                        chunk_id="", repository=file.repository, path=file.path, language=file.language,
                        start_line=cursor, end_line=slice_end, content=content,
                        symbol=owner.symbol if owner else None,
                        node_type=owner.node_type if owner else parsed.root_node_type,
                        parent_symbol=owner.parent_symbol if owner else None,
                        chunking_strategy="ast", chunking_version=AST_CHUNKING_VERSION,
                    )
                    chunks.append(replace(chunk, chunk_id=self._chunk_id(chunk, owner)))
                cursor = slice_end + 1
        return chunks

    def _chunk_id(self, chunk: CodeChunk, owner: SyntaxSymbol | None) -> str:
        identity = json.dumps({
            "version": chunk.chunking_version, "strategy": chunk.chunking_strategy,
            "repository": chunk.repository, "path": chunk.path, "language": chunk.language,
            "symbol": chunk.symbol, "node_type": chunk.node_type, "parent_symbol": chunk.parent_symbol,
            "declaration_bytes": [owner.start_byte, owner.end_byte] if owner else None,
            "start_line": chunk.start_line, "end_line": chunk.end_line,
            "chunk_lines": self.chunk_lines,
            "overlap_lines": self.overlap_lines if chunk.chunking_strategy == "fallback" else 0,
            "max_chunk_characters": self.max_chunk_characters,
            "content_sha256": hashlib.sha256(chunk.content.encode("utf-8")).hexdigest(),
        }, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return str(uuid5(NAMESPACE_URL, "repopilot:code-chunk:" + identity))


def create_ast_code_chunker() -> ASTCodeChunker:
    settings = get_settings()
    return ASTCodeChunker(
        chunk_lines=settings.chunk_lines, overlap_lines=settings.chunk_overlap_lines,
        max_file_bytes=settings.repository_max_file_bytes,
        max_chunk_characters=min(settings.chunk_max_characters, MAX_EMBEDDING_TEXT_LENGTH),
        max_chunks=settings.chunk_max_chunks,
    )
