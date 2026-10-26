"""Opt-in Hybrid retrieval; all channels and fusion share one guarded snapshot."""
from dataclasses import asdict, dataclass, field
import math
from time import perf_counter

from app.retrieval.candidates import CandidateLimits, to_retrieval_results
from app.retrieval.exact import ExactCandidateBatch, ExactMatch, ExactSearchRequest
from app.retrieval.rrf import CHANNELS, RRFParameters, fuse_rrf, merge_candidates
from app.retrieval.scope import ScopedCandidateBatch, ScopedSearchRequest, SearchFilters
from app.retrieval.snapshot_models import SnapshotError


@dataclass(frozen=True)
class HybridSearchRequest:
    repository: str
    query: str
    filters: SearchFilters = field(default_factory=SearchFilters)
    limits: CandidateLimits = field(default_factory=CandidateLimits)
    exact_match: ExactMatch | None = None

    def __post_init__(self):
        if not isinstance(self.limits, CandidateLimits): raise TypeError("Hybrid needs CandidateLimits.")
        ScopedSearchRequest(self.repository, self.query, self.limits.fusion, filters=self.filters)
        if self.exact_match is not None and not isinstance(self.exact_match, ExactMatch): raise TypeError("Invalid Exact selector.")

    def channel_request(self, channel):
        if channel not in CHANNELS: raise ValueError("Unsupported Hybrid channel.")
        args = (self.repository, self.query, getattr(self.limits, channel))
        if channel == "exact" and self.exact_match is not None:
            return ExactSearchRequest(*args, filters=self.filters, match=self.exact_match)
        return ScopedSearchRequest(*args, filters=self.filters)

    def to_dict(self):
        return {"repository": self.repository, "query": self.query, "filters": self.filters.to_dict(),
                "limits": asdict(self.limits), "exact_match": self.exact_match.to_dict() if self.exact_match else None}

    @classmethod
    def from_dict(cls, value):
        return cls(value["repository"], value["query"], SearchFilters(**value["filters"]), CandidateLimits(**value["limits"]),
                   ExactMatch(**value["exact_match"]) if value["exact_match"] is not None else None)


@dataclass(frozen=True)
class HybridResult:
    request: HybridSearchRequest
    parameters: RRFParameters
    channels: tuple[ScopedCandidateBatch, ...]
    fused: ScopedCandidateBatch
    retrieval_latency_ms: float

    def __post_init__(self):
        if not isinstance(self.request, HybridSearchRequest) or not isinstance(self.parameters, RRFParameters):
            raise TypeError("Hybrid result needs validated request and parameters.")
        channels = tuple(self.channels)
        if any(not isinstance(b, ScopedCandidateBatch) for b in channels): raise ValueError("Hybrid needs scoped batches.")
        names = [b.channel for b in channels]
        if names not in (["dense", "bm25"], list(CHANNELS)):
            raise ValueError("Hybrid must contain ordered Dense/BM25 with optional Exact.")
        request, fused = self.request, self.fused
        for batch in (*channels, fused):
            if (not isinstance(batch, ScopedCandidateBatch) or batch.repository != request.repository
                    or batch.query != request.query or batch.filters != request.filters
                    or batch.candidate_limit != getattr(request.limits, "fusion" if batch is fused else batch.channel)):
                raise ValueError("Hybrid batch does not match its request scope or limits.")
        if "exact" in names:
            exact = channels[-1]
            if not isinstance(exact, ExactCandidateBatch) or exact.match != (request.exact_match or ExactMatch.infer(request.query)):
                raise ValueError("Hybrid Exact diagnostics/selector disagree with the request.")
        expected, _ = merge_candidates(channels, self.parameters, request.limits.fusion)
        if (fused.channel != "rrf" or fused.index_revision != channels[0].index_revision
                or fused.candidates != expected):
            raise ValueError("Hybrid fused score, source, order or provenance cannot be recomputed.")
        if type(self.retrieval_latency_ms) not in (int, float) or not math.isfinite(self.retrieval_latency_ms) or self.retrieval_latency_ms < 0:
            raise ValueError("Hybrid latency must be finite and nonnegative.")
        object.__setattr__(self, "channels", channels)

    @property
    def union_count(self):
        return len({(c.repository, c.index_revision, c.chunk_id) for b in self.channels for c in b.candidates})

    def final_results(self):
        return to_retrieval_results(self.fused.candidates, repository=self.request.repository,
            index_revision=self.fused.index_revision, top_k=self.request.limits.top_k, score_channel="rrf")

    def to_dict(self):
        return {"request": self.request.to_dict(), "parameters": self.parameters.to_dict(),
                "channels": {b.channel: b.to_dict() for b in self.channels}, "fused": self.fused.to_dict(),
                "untruncated_union_count": self.union_count, "retrieval_latency_ms": self.retrieval_latency_ms,
                "rrf_contributions": {c.chunk_id: {ch: {"rank": c.channel_ranks[ch], "raw_score": c.channel_scores[ch],
                    "contribution": 1 / (self.parameters.k + c.channel_ranks[ch])} for ch in CHANNELS if ch in c.channel_ranks}
                    for c in self.fused.candidates}, "results": [r.model_dump() for r in self.final_results()]}

    @classmethod
    def from_dict(cls, value):
        request = HybridSearchRequest.from_dict(value["request"])
        parameters = RRFParameters(value["parameters"]["k"])
        if parameters.to_dict() != value["parameters"]: raise ValueError("RRF definition mismatch.")
        channels = tuple(ExactCandidateBatch.from_dict(value["channels"][ch]) if ch == "exact"
                         else ScopedCandidateBatch.from_dict(value["channels"][ch]) for ch in CHANNELS if ch in value["channels"])
        result = cls(request, parameters, channels, ScopedCandidateBatch.from_dict(value["fused"]), value["retrieval_latency_ms"])
        if result.to_dict() != value: raise ValueError("Hybrid serialized provenance, union count or results differ.")
        return result


class HybridRetriever:
    def __init__(self, coordinator, channels, *, parameters=None):
        if (set(channels) not in ({"dense", "bm25"}, set(CHANNELS))
                or any(channel.name != name or channel.coordinator is not coordinator for name, channel in channels.items())):
            raise ValueError("Hybrid channels must belong to the same snapshot coordinator.")
        if parameters is not None and not isinstance(parameters, RRFParameters): raise TypeError("Invalid RRF parameters.")
        self.coordinator, self.channels, self.parameters = coordinator, dict(channels), parameters or RRFParameters()

    async def search(self, request):
        if not isinstance(request, HybridSearchRequest): raise TypeError("Hybrid requires a HybridSearchRequest.")
        started = perf_counter()
        async def retrieve(snapshot):
            batches = []
            indexed = {c.chunk_id: c.source for c in snapshot.index.chunks}
            for name in CHANNELS:
                if name not in self.channels: continue
                batch = await self.channels[name].recall(request.channel_request(name))
                if (not isinstance(batch, ScopedCandidateBatch) or batch.channel != name
                        or batch.index_revision != snapshot.index.index_revision
                        or any(c.chunk_id not in indexed or indexed[c.chunk_id] != c.source_metadata for c in batch.candidates)):
                    raise SnapshotError("Hybrid channel returned code outside the shared indexed snapshot.")
                batches.append(batch)
            fused = fuse_rrf(batches, parameters=self.parameters, candidate_limit=request.limits.fusion)
            return tuple(batches), fused
        channels, fused = await self.coordinator.use(request.repository, retrieve)
        return HybridResult(request, self.parameters, channels, fused, round((perf_counter() - started) * 1000, 3))
