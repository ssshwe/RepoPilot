from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class ContextSource:
    source_id: str
    chunk_id: str
    retrieval_rank: int
    score: float
    repository: str
    path: str
    language: str
    symbol: str | None
    node_type: str | None
    parent_symbol: str | None
    chunking_strategy: Literal["simple", "ast", "fallback"]
    chunking_version: str
    start_line: int
    end_line: int
    original_start_line: int
    original_end_line: int
    content: str
    overlap_trimmed: bool
    budget_truncated: bool


@dataclass(frozen=True, slots=True)
class BuiltContext:
    repository: str
    text: str
    sources: tuple[ContextSource, ...]
    retrieved_chunks: int
    duplicate_chunks: int
    overlap_trimmed_chunks: int
    candidate_sources: int
    omitted_sources: int
    max_context_chunks: int
    max_context_characters: int
    characters_used: int
    budget_limited: bool
    empty_reason: Literal["no_results", "budget_too_small"] | None

    @property
    def source_map(self) -> dict[str, ContextSource]:
        # A fresh mapping prevents callers from mutating another request's map.
        return {source.source_id: source for source in self.sources}
