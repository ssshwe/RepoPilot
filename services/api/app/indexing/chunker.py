from collections.abc import Iterator
import hashlib
import json
from uuid import NAMESPACE_URL, uuid5

from app.core.config import get_settings
from app.core.exceptions import ChunkingLimitError
from app.indexing.models import CodeChunk
from app.repository.models import RepositoryFile


class SimpleCodeChunker:
    """Fixed line windows with overlap, preserving exact decoded source slices."""

    def __init__(
        self, *, chunk_lines: int = 60, overlap_lines: int = 10,
        max_file_bytes: int = 1_048_576, max_chunk_characters: int = 8_192,
        max_chunks: int = 10_000,
    ) -> None:
        if type(chunk_lines) is not int or not 1 <= chunk_lines <= 200:
            raise ValueError("chunk_lines must be an integer between 1 and 200.")
        if type(overlap_lines) is not int or not 0 <= overlap_lines < chunk_lines:
            raise ValueError("overlap_lines must be nonnegative and smaller than chunk_lines.")
        if any(type(limit) is not int or limit < 1 for limit in (
            max_file_bytes, max_chunk_characters, max_chunks,
        )):
            raise ValueError("Chunking resource limits must be positive integers.")
        self.chunk_lines = chunk_lines
        self.overlap_lines = overlap_lines
        self.max_file_bytes = max_file_bytes
        self.max_chunk_characters = max_chunk_characters
        self.max_chunks = max_chunks

    def iter_file_chunks(self, file: RepositoryFile) -> Iterator[CodeChunk]:
        # Recheck actual text size even when this method is called without Scanner.
        if max(file.size_bytes, len(file.content.encode("utf-8"))) > self.max_file_bytes:
            raise ChunkingLimitError("A source file exceeds the chunking file byte limit.")
        if not file.content.strip():
            return

        lines = file.content.splitlines(keepends=True)
        step = self.chunk_lines - self.overlap_lines
        count = 0
        for start in range(0, len(lines), step):
            end = min(start + self.chunk_lines, len(lines))
            content = "".join(lines[start:end])
            # Blank windows are skipped without renumbering or trimming source lines.
            if content.strip():
                if len(content) > self.max_chunk_characters:
                    raise ChunkingLimitError("A line window exceeds the chunk character limit.")
                count += 1
                if count > self.max_chunks:
                    raise ChunkingLimitError("Chunking exceeds the chunk count limit.")
                yield CodeChunk(
                    chunk_id=self._chunk_id(file, start + 1, end, content),
                    repository=file.repository, path=file.path, language=file.language,
                    start_line=start + 1, end_line=end, content=content,
                )
            # Do not emit a redundant last window containing only the overlap suffix.
            if end == len(lines):
                break

    def chunk_file(self, file: RepositoryFile) -> list[CodeChunk]:
        return list(self.iter_file_chunks(file))

    def _chunk_id(self, file: RepositoryFile, start: int, end: int, content: str) -> str:
        identity = json.dumps({
            "version": "line-window-v1", "repository": file.repository,
            "path": file.path, "language": file.language,
            "start_line": start, "end_line": end,
            "chunk_lines": self.chunk_lines, "overlap_lines": self.overlap_lines,
            "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        }, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return str(uuid5(NAMESPACE_URL, "repopilot:code-chunk:" + identity))


def create_code_chunker(
    *, chunk_lines: int | None = None, overlap_lines: int | None = None,
) -> SimpleCodeChunker:
    settings = get_settings()
    return SimpleCodeChunker(
        chunk_lines=settings.chunk_lines if chunk_lines is None else chunk_lines,
        overlap_lines=settings.chunk_overlap_lines if overlap_lines is None else overlap_lines,
        max_file_bytes=settings.repository_max_file_bytes,
        max_chunk_characters=settings.chunk_max_characters,
        max_chunks=settings.chunk_max_chunks,
    )
