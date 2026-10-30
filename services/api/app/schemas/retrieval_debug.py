"""Additive debug contracts; legacy retrieval/RAG schemas remain unchanged."""
from dataclasses import asdict
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.retrieval.candidates import CandidateLimits, to_retrieval_results
from app.retrieval.exact import ExactCandidateBatch, ExactMatch
from app.retrieval.rrf import RRFParameters, fuse_rrf
from app.retrieval.scope import ScopedCandidateBatch, SearchFilters
from app.schemas.retrieval import RetrievalSearchRequest, RetrievalSearchResponse


class DebugFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")
    language: str | None = Field(default=None, strict=True, max_length=32)
    path_prefix: str | None = Field(default=None, strict=True, max_length=4096)

    @model_validator(mode="after")
    def valid_scope(self):
        SearchFilters(self.language, self.path_prefix)
        return self


class DebugSearchRequest(RetrievalSearchRequest):
    mode: Literal["dense", "bm25", "hybrid"] = "dense"
    filters: DebugFilters = Field(default_factory=DebugFilters)
    rerank: bool = Field(default=False, strict=True)
    query_expansion: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def supported_features(self):
        if self.rerank or self.query_expansion:
            raise ValueError("Rerank and Query Expansion are not implemented yet.")
        return self


class DebugTimings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    channel_ms: dict[str, float]
    fusion_ms: float = Field(ge=0, allow_inf_nan=False)
    overhead_ms: float = Field(ge=0, allow_inf_nan=False)
    total_ms: float = Field(ge=0, allow_inf_nan=False)


class DebugSearchResponse(RetrievalSearchResponse):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["dense", "bm25", "hybrid"]
    filters: DebugFilters
    index_revision: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    score_type: Literal["cosine_similarity", "bm25", "rrf"]
    limits: dict[str, int]
    channels: dict[str, dict[str, Any]]
    fused: dict[str, Any] | None
    rrf_k: int | None
    untruncated_union_count: int = Field(strict=True, ge=0, le=180)
    rrf_contributions: dict[str, dict[str, dict[str, float | int]]]
    timings: DebugTimings
    rerank: Literal[False] = False
    query_expansion: Literal[False] = False

    @model_validator(mode="after")
    def consistent_pipeline(self):
        limits = CandidateLimits(top_k=self.top_k)
        names = ["dense", "bm25", "exact"] if self.mode == "hybrid" else [self.mode]
        if self.limits != asdict(limits) or set(self.channels) != set(names):
            raise ValueError("Debug channel inventory/limits differ from the request.")
        batches = []
        for name in names:
            batch = ExactCandidateBatch.from_dict(self.channels[name]) if name == "exact" else ScopedCandidateBatch.from_dict(self.channels[name])
            if (batch.channel != name or batch.repository != self.repository or batch.query != self.query
                    or batch.index_revision != self.index_revision or batch.filters != SearchFilters(**self.filters.model_dump())
                    or batch.candidate_limit != getattr(limits, name)
                    or any(set(c.channel_scores) != {name} or c.matched_queries != (self.query,) for c in batch.candidates)):
                raise ValueError("Debug channel source scope, revision or query differs.")
            batches.append(batch)
        if self.mode == "hybrid" and batches[-1].match != ExactMatch.infer(self.query):
            raise ValueError("Debug Exact selector differs from the original query.")
        if self.mode == "hybrid":
            if self.rrf_k != 60 or self.fused is None: raise ValueError("Missing fixed RRF configuration.")
            fused = ScopedCandidateBatch.from_dict(self.fused)
            expected = fuse_rrf(batches, parameters=RRFParameters())
            if (fused.channel != "rrf" or fused.repository != self.repository or fused.query != self.query
                    or fused.index_revision != self.index_revision or fused.filters != batches[0].filters
                    or fused.candidate_limit != limits.fusion or fused.candidates != expected.candidates):
                raise ValueError("Debug fusion cannot be recomputed.")
            selected, channel = fused, "rrf"
            contributions = {c.chunk_id: {name: {"rank": c.channel_ranks[name], "raw_score": c.channel_scores[name],
                "contribution": 1 / (60 + c.channel_ranks[name])} for name in names if name in c.channel_ranks} for c in fused.candidates}
            fusion_ms = fused.retrieval_latency_ms
        else:
            if self.fused is not None or self.rrf_k is not None: raise ValueError("Unexpected fusion in single-channel mode.")
            selected, channel, contributions, fusion_ms = batches[0], self.mode, {}, 0.
        expected_results = to_retrieval_results(selected.candidates, repository=self.repository,
            index_revision=self.index_revision, top_k=self.top_k, score_channel=channel)
        if ([r.model_dump() for r in self.results] != [r.model_dump() for r in expected_results]
                or self.score_type != {"dense": "cosine_similarity", "bm25": "bm25", "hybrid": "rrf"}[self.mode]
                or self.rrf_contributions != contributions
                or self.untruncated_union_count != len({c.chunk_id for b in batches for c in b.candidates})):
            raise ValueError("Debug final results/provenance differ from their candidate pool.")
        if (self.timings.channel_ms != {b.channel: b.retrieval_latency_ms for b in batches}
                or self.timings.fusion_ms != fusion_ms or self.timings.total_ms != self.retrieval_latency_ms
                or abs(self.timings.total_ms - sum(self.timings.channel_ms.values()) - fusion_ms - self.timings.overhead_ms) > .02):
            raise ValueError("Debug timing boundaries disagree.")
        # Project validated source shapes, never preserve extra upstream Payload.
        self.channels = {b.channel: b.to_dict() for b in batches}
        return self
