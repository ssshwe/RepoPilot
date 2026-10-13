from collections.abc import Sequence
from dataclasses import dataclass
import json
from uuid import UUID

from pydantic import ValidationError

from app.context.models import BuiltContext, ContextSource
from app.core.config import get_settings
from app.core.exceptions import RepositoryPathError
from app.repository.paths import validate_repository_name
from app.schemas.retrieval import RetrievalResult


class ContextBuilderError(ValueError):
    """Invalid context input; messages never contain code or upstream payloads."""


@dataclass(frozen=True, slots=True)
class _Candidate:
    result: RetrievalResult
    start_line: int
    lines: tuple[str, ...]
    overlap_trimmed: bool


class ContextBuilder:
    """Keep ranked source fragments within a serialized character/source budget.

    Validate the ENTIRE batch first, remove already-covered lines per path, then
    take a ranked prefix. Never sort, read files, call a provider, or rerank.
    Partial sources consist only of complete original lines. Headers, JSON
    escaping, source boundaries and separators all count against the budget.
    """

    def __init__(self, *, max_context_chunks: int = 5, max_context_characters: int = 16_000):
        if type(max_context_chunks) is not int or not 1 <= max_context_chunks <= 20:
            raise ValueError("MAX_CONTEXT_CHUNKS must be an integer in 1..20.")
        if type(max_context_characters) is not int or not 1 <= max_context_characters <= 200_000:
            raise ValueError("MAX_CONTEXT_CHARACTERS must be an integer in 1..200000.")
        self.max_context_chunks = max_context_chunks
        self.max_context_characters = max_context_characters

    def build(self, repository: str, results: Sequence[RetrievalResult]) -> BuiltContext:
        validated = self._validate(repository, results)
        candidates, duplicates, trimmed = self._deduplicate(validated)
        sources, blocks = [], []
        used = 0
        for candidate in candidates:
            if len(sources) == self.max_context_chunks:
                break
            source_id = f"S{len(sources) + 1}"
            separator_cost = 2 if blocks else 0
            available = self.max_context_characters - used - separator_cost
            source = self._source(candidate, source_id, len(candidate.lines))
            block = self.format_source(source)
            if len(block) > available:
                source = self._fit_prefix(candidate, source_id, available)
                if source is not None:
                    block = self.format_source(source)
                    sources.append(source)
                    blocks.append(block)
                    used += separator_cost + len(block)
                # A lower-ranked smaller source must not displace this source.
                break
            sources.append(source)
            blocks.append(block)
            used += separator_cost + len(block)
        text = "\n\n".join(blocks)
        omitted = len(candidates) - len(sources)
        return BuiltContext(
            repository=repository, text=text, sources=tuple(sources),
            retrieved_chunks=len(validated), duplicate_chunks=duplicates,
            overlap_trimmed_chunks=trimmed, candidate_sources=len(candidates),
            omitted_sources=omitted, max_context_chunks=self.max_context_chunks,
            max_context_characters=self.max_context_characters, characters_used=len(text),
            budget_limited=bool(omitted or any(s.budget_truncated for s in sources)),
            empty_reason=("no_results" if not validated else "budget_too_small") if not sources else None,
        )

    @staticmethod
    def _validate(repository: str, results: Sequence[RetrievalResult]) -> list[RetrievalResult]:
        try:
            validate_repository_name(repository)
        except RepositoryPathError:
            raise ContextBuilderError("Context requires a valid repository name.") from None
        if not isinstance(results, (list, tuple)) or len(results) > 20:
            raise ContextBuilderError("Context requires a list or tuple of at most 20 retrieval results.")
        validated, identities, source_lines = [], {}, {}
        previous_rank = 0
        for item in results:
            if not isinstance(item, RetrievalResult):
                raise ContextBuilderError("Context requires validated retrieval result objects.")
            try:
                # Also catches invalid objects created with model_construct or
                # mutated after retrieval. No input model is retained or changed.
                result = RetrievalResult.model_validate(item.model_dump(warnings=False), strict=True)
                if result.repository != repository or str(UUID(result.chunk_id)) != result.chunk_id:
                    raise ValueError("scope or identity")
                if result.rank < previous_rank:
                    raise ValueError("rank order")
                # Indexed UTF-8 text cannot contain unpaired Unicode surrogates.
                json.dumps(result.model_dump(), ensure_ascii=False, allow_nan=False).encode("utf-8")
                identity = result.model_dump(exclude={"rank", "score"})
                if result.chunk_id in identities and identities[result.chunk_id] != identity:
                    raise ValueError("inconsistent chunk identity")
                identities[result.chunk_id] = identity
                for number, line in enumerate(result.content.splitlines(keepends=True), result.start_line):
                    key = (result.path, number)
                    if key in source_lines and source_lines[key] != line:
                        raise ValueError("conflicting source lines")
                    source_lines[key] = line
            except (ValueError, TypeError, UnicodeError, ValidationError):
                raise ContextBuilderError(
                    "Context received invalid, conflicting, unordered or out-of-scope retrieval results."
                ) from None
            previous_rank = result.rank
            validated.append(result)
        return validated

    @staticmethod
    def _deduplicate(results: list[RetrievalResult]) -> tuple[list[_Candidate], int, int]:
        covered: dict[str, set[int]] = {}
        candidates, duplicates, trimmed = [], 0, 0
        for result in results:
            lines = tuple(result.content.splitlines(keepends=True))
            seen = covered.setdefault(result.path, set())
            runs, run_start = [], None
            for index in range(len(lines) + 1):
                fresh = index < len(lines) and result.start_line + index not in seen
                if fresh and run_start is None:
                    run_start = index
                elif not fresh and run_start is not None:
                    if "".join(lines[run_start:index]).strip():
                        runs.append((run_start, index))
                    run_start = None
            seen.update(range(result.start_line, result.end_line + 1))
            if not runs:
                duplicates += 1
                continue
            overlap_trimmed = runs != [(0, len(lines))]
            trimmed += int(overlap_trimmed)
            for start, end in runs:
                candidates.append(_Candidate(result, result.start_line + start,
                                              lines[start:end], overlap_trimmed))
        return candidates, duplicates, trimmed

    @staticmethod
    def _source(candidate: _Candidate, source_id: str, line_count: int) -> ContextSource:
        item = candidate.result
        return ContextSource(
            source_id=source_id, chunk_id=item.chunk_id, retrieval_rank=item.rank,
            score=item.score, repository=item.repository, path=item.path, language=item.language,
            symbol=item.symbol, node_type=item.node_type, parent_symbol=item.parent_symbol,
            chunking_strategy=item.chunking_strategy, chunking_version=item.chunking_version,
            start_line=candidate.start_line, end_line=candidate.start_line + line_count - 1,
            original_start_line=item.start_line, original_end_line=item.end_line,
            content="".join(candidate.lines[:line_count]), overlap_trimmed=candidate.overlap_trimmed,
            budget_truncated=line_count < len(candidate.lines),
        )

    def _fit_prefix(self, candidate: _Candidate, source_id: str, available: int) -> ContextSource | None:
        # Every partial candidate has budget_truncated=true, so its serialized
        # size increases monotonically with complete-line prefixes.
        low, high, best = 1, len(candidate.lines) - 1, None
        while low <= high:
            count = (low + high) // 2
            source = self._source(candidate, source_id, count)
            if len(self.format_source(source)) <= available:
                best = source
                low = count + 1
            else:
                high = count - 1
        return best if best is not None and best.content.strip() else None

    @staticmethod
    def format_source(source: ContextSource) -> str:
        body = json.dumps({
            "repository": source.repository, "path": source.path,
            "language": source.language, "symbol": source.symbol,
            "node_type": source.node_type, "parent_symbol": source.parent_symbol,
            "lines": {"start": source.start_line, "end": source.end_line},
            "original_lines": {"start": source.original_start_line, "end": source.original_end_line},
            "chunking_strategy": source.chunking_strategy, "chunking_version": source.chunking_version,
            "overlap_trimmed": source.overlap_trimmed, "budget_truncated": source.budget_truncated,
            "content": source.content,
        }, ensure_ascii=False, allow_nan=False, indent=2)
        # JSON handles C0 controls/quotes/backslashes. Escape the remaining
        # Unicode line separators too: repository data cannot forge a new
        # standalone SOURCE boundary. This is framing, not an injection cure.
        for character, escaped in (("\x85", "\\u0085"), ("\u2028", "\\u2028"), ("\u2029", "\\u2029")):
            body = body.replace(character, escaped)
        return f"[SOURCE {source.source_id}]\n{body}\n[/SOURCE {source.source_id}]"


def create_context_builder(
    *, max_context_chunks: int | None = None, max_context_characters: int | None = None,
) -> ContextBuilder:
    settings = get_settings()
    return ContextBuilder(
        max_context_chunks=settings.max_context_chunks if max_context_chunks is None else max_context_chunks,
        max_context_characters=settings.max_context_characters if max_context_characters is None else max_context_characters,
    )
