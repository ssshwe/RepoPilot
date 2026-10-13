"""Internal recall contracts. Public results and ContextBuilder retain their limits."""
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields
import math
import re
from types import MappingProxyType
from typing import Protocol
from uuid import UUID

from app.core.exceptions import RetrievalResultError
from app.schemas.retrieval import RetrievalResult, RetrievalSearchRequest


MAX_CANDIDATES = 60


def validate_candidate_limit(value: int) -> int:
    if type(value) is not int or not 1 <= value <= MAX_CANDIDATES:
        raise ValueError(f"candidate_limit must be an integer between 1 and {MAX_CANDIDATES}.")
    return value


def _revision(value: str | None) -> None:
    # There is no native index revision yet. A caller may bind a verified snapshot
    # SHA-256, or leave it explicitly unknown; never invent a production revision.
    if value is not None and (not isinstance(value, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None):
        raise ValueError("index_revision must be None or a snapshot sha256 digest.")


def _channel(value: str) -> None:
    if not isinstance(value, str) or re.fullmatch(r"[a-z][a-z0-9_]{0,31}", value) is None:
        raise ValueError("Invalid retrieval channel name.")


@dataclass(frozen=True)
class CandidateLimits:
    dense: int = 30
    bm25: int = 30
    exact: int = 10
    fusion: int = 60
    rerank: int = 30
    top_k: int = 5

    def __post_init__(self) -> None:
        for name in ("dense", "bm25", "exact", "fusion", "rerank"):
            validate_candidate_limit(getattr(self, name))
        if max(self.dense, self.bm25, self.exact, self.rerank) > self.fusion:
            raise ValueError("Channel/rerank limits must fit the fusion candidate limit.")
        RetrievalSearchRequest(repository="validation", query="validation", top_k=self.top_k)


@dataclass(frozen=True)
class CandidateSearchRequest:
    repository: str
    query: str
    candidate_limit: int = 30

    def __post_init__(self) -> None:
        RetrievalSearchRequest(repository=self.repository, query=self.query)
        validate_candidate_limit(self.candidate_limit)


@dataclass(frozen=True)
class SourceMetadata:
    path: str
    language: str
    start_line: int
    end_line: int
    content: str
    symbol: str | None = None
    node_type: str | None = None
    parent_symbol: str | None = None
    chunking_strategy: str = "simple"
    chunking_version: str = "line-window-v1"

    def __post_init__(self) -> None:
        # Share the existing source validation, including inclusive lines and AST
        # ownership. The placeholder rank is not an internal candidate rank.
        RetrievalResult(rank=1, score=0., chunk_id="00000000-0000-0000-0000-000000000000",
                        repository="validation", **self.to_dict())

    def to_dict(self) -> dict:
        return {field.name: getattr(self, field.name) for field in fields(self)}

    @classmethod
    def from_payload(cls, payload: dict) -> "SourceMetadata":
        keys = {field.name for field in fields(cls)}
        # Validate before filling legacy defaults: partial AST metadata is corrupt.
        parsed = RetrievalResult(rank=1, score=0., chunk_id="00000000-0000-0000-0000-000000000000",
                                 repository="validation", **{key: value for key, value in payload.items() if key in keys})
        return cls(**{key: getattr(parsed, key) for key in keys})


@dataclass(frozen=True)
class RetrievalCandidate:
    chunk_id: str
    repository: str
    index_revision: str | None
    source_metadata: SourceMetadata
    channel_scores: Mapping[str, float]
    channel_ranks: Mapping[str, int]
    matched_queries: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.chunk_id, str) or str(UUID(self.chunk_id)) != self.chunk_id:
            raise ValueError("Candidate chunk_id must be a canonical UUID.")
        RetrievalSearchRequest(repository=self.repository, query="validation")
        _revision(self.index_revision)
        if not isinstance(self.source_metadata, SourceMetadata):
            raise ValueError("Candidate needs validated source metadata.")
        scores, ranks = dict(self.channel_scores), dict(self.channel_ranks)
        if not scores or len(scores) > 8 or scores.keys() != ranks.keys():
            raise ValueError("Candidate channel scores and ranks must have matching keys.")
        for name, score in scores.items():
            _channel(name)
            if type(score) not in (int, float) or not math.isfinite(score):
                raise ValueError("Candidate scores must be finite numbers.")
            validate_candidate_limit(ranks[name])
        if not isinstance(self.matched_queries, (tuple, list)):
            raise ValueError("Candidate matched_queries must be a sequence of queries.")
        queries = tuple(self.matched_queries)
        if not queries or len(queries) > MAX_CANDIDATES or len(set(queries)) != len(queries):
            raise ValueError("Candidate needs unique matched queries.")
        for query in queries:
            CandidateSearchRequest(repository=self.repository, query=query)
        object.__setattr__(self, "channel_scores", MappingProxyType(scores))
        object.__setattr__(self, "channel_ranks", MappingProxyType(ranks))
        object.__setattr__(self, "matched_queries", queries)

    def to_dict(self) -> dict:
        return {"chunk_id": self.chunk_id, "repository": self.repository,
                "index_revision": self.index_revision, "source_metadata": self.source_metadata.to_dict(),
                "channel_scores": dict(self.channel_scores), "channel_ranks": dict(self.channel_ranks),
                "matched_queries": list(self.matched_queries)}

    @classmethod
    def from_dict(cls, value: dict) -> "RetrievalCandidate":
        return cls(**(value | {"source_metadata": SourceMetadata.from_payload(value["source_metadata"])}))


@dataclass(frozen=True)
class CandidateBatch:
    repository: str
    query: str
    channel: str
    index_revision: str | None
    candidate_limit: int
    candidates: tuple[RetrievalCandidate, ...]
    retrieval_latency_ms: float

    def __post_init__(self) -> None:
        CandidateSearchRequest(self.repository, self.query, self.candidate_limit)
        _channel(self.channel)
        _revision(self.index_revision)
        candidates = tuple(self.candidates)
        if any(not isinstance(candidate, RetrievalCandidate) for candidate in candidates):
            raise ValueError("Candidate batches need validated candidates.")
        if len(candidates) > self.candidate_limit or len({c.chunk_id for c in candidates}) != len(candidates):
            raise ValueError("Invalid candidate count or duplicate chunk identity.")
        for rank, candidate in enumerate(candidates, 1):
            if (candidate.repository != self.repository or candidate.index_revision != self.index_revision
                    or candidate.channel_ranks.get(self.channel) != rank or self.query not in candidate.matched_queries):
                raise ValueError("Candidate batch contains incompatible scope, revision or rank.")
        if (type(self.retrieval_latency_ms) not in (int, float)
                or not math.isfinite(self.retrieval_latency_ms) or self.retrieval_latency_ms < 0):
            raise ValueError("Candidate latency must be finite and nonnegative.")
        object.__setattr__(self, "candidates", candidates)

    def to_dict(self) -> dict:
        return {"repository": self.repository, "query": self.query, "channel": self.channel,
                "index_revision": self.index_revision, "candidate_limit": self.candidate_limit,
                "retrieval_latency_ms": self.retrieval_latency_ms,
                "candidates": [candidate.to_dict() for candidate in self.candidates]}

    @classmethod
    def from_dict(cls, value: dict) -> "CandidateBatch":
        return cls(**(value | {"candidates": tuple(RetrievalCandidate.from_dict(c) for c in value["candidates"])}))


class RetrievalChannel(Protocol):
    """Recall one ordered channel; fusion and reranking are later tasks."""
    name: str

    async def recall(self, request: CandidateSearchRequest) -> CandidateBatch: ...


def to_retrieval_results(
    candidates: Sequence[RetrievalCandidate], *, repository: str, index_revision: str | None,
    top_k: int = 5, score_channel: str = "dense",
) -> list[RetrievalResult]:
    """Adapt an already ordered candidate pool; validate every candidate before slicing."""
    RetrievalSearchRequest(repository=repository, query="validation", top_k=top_k)
    _revision(index_revision)
    _channel(score_channel)
    try:
        if any(not isinstance(candidate, RetrievalCandidate) for candidate in candidates):
            raise ValueError("Candidate pools need validated candidates.")
        if len(candidates) > MAX_CANDIDATES or len({c.chunk_id for c in candidates}) != len(candidates):
            raise ValueError("Invalid candidate pool.")
        for candidate in candidates:
            if (candidate.repository != repository or candidate.index_revision != index_revision
                    or score_channel not in candidate.channel_scores):
                raise ValueError("Incompatible candidate pool.")
        return [RetrievalResult(rank=rank, chunk_id=c.chunk_id, repository=c.repository,
                                score=c.channel_scores[score_channel], **c.source_metadata.to_dict())
                for rank, c in enumerate(candidates[:top_k], 1)]
    except (ValueError, TypeError, AttributeError) as exc:
        raise RetrievalResultError("Invalid or incompatible internal retrieval candidates.") from exc
