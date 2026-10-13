from collections.abc import Sequence
import re
from uuid import UUID

from pydantic import ValidationError

from app.citations.models import Citation
from app.context import BuiltContext, ContextBuilder, ContextSource
from app.core.exceptions import RepositoryPathError
from app.repository.paths import validate_repository_name
from app.schemas.retrieval import RetrievalResult


class CitationValidationError(ValueError):
    """Source-free errors; invalid batches never produce partial citations."""


_SOURCE_ID = re.compile(r"S(?:[1-9]|1[0-9]|20)")
_METADATA = ("chunk_id", "repository", "path", "language", "symbol", "node_type",
             "parent_symbol", "chunking_strategy", "chunking_version")


class CitationValidator:
    """Validate this request's context against its retrieved chunks, then map IDs.

    Pure and bounded: no provider, disk, database or global source-map access.
    Actual emitted line ranges are authoritative, including truncated fragments.
    """

    def validate(
        self, repository: str, source_ids: Sequence[str], *,
        context: BuiltContext, retrieval_results: Sequence[RetrievalResult],
    ) -> tuple[Citation, ...]:
        try:
            validate_repository_name(repository)
            if (not isinstance(source_ids, (list, tuple)) or len(source_ids) > 20
                    or any(type(value) is not str or _SOURCE_ID.fullmatch(value) is None for value in source_ids)
                    or len(set(source_ids)) != len(source_ids)):
                raise ValueError("invalid IDs")
            if (not isinstance(context, BuiltContext) or context.repository != repository
                    or not isinstance(context.sources, tuple)
                    or not isinstance(retrieval_results, (list, tuple)) or len(retrieval_results) > 20):
                raise ValueError("invalid scope or context")
            # Reuse the same public budget bounds, without building new context.
            ContextBuilder(max_context_chunks=context.max_context_chunks,
                           max_context_characters=context.max_context_characters)
            if (len(context.sources) > context.max_context_chunks
                    or type(context.retrieved_chunks) is not int or context.retrieved_chunks != len(retrieval_results)
                    or type(context.characters_used) is not int or not isinstance(context.text, str)
                    or context.characters_used != len(context.text)
                    or context.characters_used > context.max_context_characters):
                raise ValueError("invalid context size")
            originals, previous_rank, indexed_lines = {}, 0, {}
            for item in retrieval_results:
                if not isinstance(item, RetrievalResult):
                    raise ValueError("invalid retrieval type")
                original = RetrievalResult.model_validate(item.model_dump(warnings=False), strict=True)
                if (original.repository != repository or str(UUID(original.chunk_id)) != original.chunk_id
                        or original.rank < previous_rank):
                    raise ValueError("invalid retrieval scope/order")
                previous_rank = original.rank
                previous = originals.setdefault(original.chunk_id, original)
                if original.model_dump(exclude={"rank", "score"}) != previous.model_dump(exclude={"rank", "score"}):
                    raise ValueError("conflicting chunk identity")
                for number, line in enumerate(original.content.splitlines(keepends=True), original.start_line):
                    key = (original.path, number)
                    if key in indexed_lines and indexed_lines[key] != line:
                        raise ValueError("conflicting indexed lines")
                    indexed_lines[key] = line
            projected, covered, previous_rank = {}, set(), 0
            for index, source in enumerate(context.sources, start=1):
                if not isinstance(source, ContextSource) or source.source_id != f"S{index}":
                    raise ValueError("invalid source map identity")
                # Independent strict range/content/metadata validation on the
                # emitted slice, even if a frozen record was bypassed or replaced.
                effective = RetrievalResult.model_validate({
                    **{key: getattr(source, key) for key in _METADATA},
                    "rank": source.retrieval_rank, "score": source.score,
                    "start_line": source.start_line, "end_line": source.end_line, "content": source.content,
                }, strict=True)
                original = originals[effective.chunk_id]
                if (any(getattr(effective, key) != getattr(original, key) for key in _METADATA)
                        or effective.rank != original.rank or effective.score != original.score
                        or effective.rank < previous_rank
                        or type(source.original_start_line) is not int or type(source.original_end_line) is not int
                        or (source.original_start_line, source.original_end_line) != (original.start_line, original.end_line)
                        or not original.start_line <= effective.start_line <= effective.end_line <= original.end_line):
                    raise ValueError("source not from current retrieval")
                previous_rank = effective.rank
                original_lines = original.content.splitlines(keepends=True)
                if effective.content != "".join(original_lines[
                    effective.start_line - original.start_line:effective.end_line - original.start_line + 1
                ]):
                    raise ValueError("source slice does not match original")
                if (type(source.overlap_trimmed) is not bool or type(source.budget_truncated) is not bool
                        or (not source.overlap_trimmed and effective.start_line != original.start_line)
                        or (not source.overlap_trimmed and not source.budget_truncated and effective.end_line != original.end_line)
                        or (source.overlap_trimmed and (effective.start_line, effective.end_line) == (original.start_line, original.end_line))
                        or (source.budget_truncated and effective.end_line == original.end_line)):
                    raise ValueError("inconsistent slice flags")
                source_lines = {(effective.path, number) for number in range(effective.start_line, effective.end_line + 1)}
                if not covered.isdisjoint(source_lines):
                    raise ValueError("overlapping emitted sources")
                covered.update(source_lines)
                projected[source.source_id] = Citation(
                    source_id=source.source_id, repository=original.repository,
                    path=original.path, symbol=original.symbol,
                    start_line=effective.start_line, end_line=effective.end_line,
                )
            # Ensure the authoritative map describes the context actually sent,
            # not a larger original chunk or another request's formatting.
            if context.text != "\n\n".join(ContextBuilder.format_source(source) for source in context.sources):
                raise ValueError("context/map mismatch")
            if any(source_id not in projected for source_id in source_ids):
                raise ValueError("source outside emitted context")
            return tuple(projected[source_id] for source_id in source_ids)
        except (RepositoryPathError, ValueError, TypeError, KeyError, AttributeError, ValidationError):
            raise CitationValidationError("Invalid citation IDs or source metadata for this request.") from None
