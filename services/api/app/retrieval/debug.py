"""Read-only indexed-code debug pipeline with explicit snapshot synchronization."""
from dataclasses import asdict
from time import perf_counter

from app.core.exceptions import RetrievalResultError
from app.retrieval.candidates import CandidateLimits, to_retrieval_results
from app.retrieval.exact import ExactCandidateChannel
from app.retrieval.filtered import FilteredBM25Channel, FilteredDenseChannel
from app.retrieval.hybrid import HybridRetriever, HybridSearchRequest
from app.retrieval.scope import ScopedSearchRequest, SearchFilters
from app.retrieval.snapshot_models import SnapshotError
from app.schemas.retrieval_debug import DebugSearchResponse


class DebugRetrievalService:
    def __init__(self, coordinator, settings, provider=None):
        self.coordinator, self.settings, self.provider = coordinator, settings, provider

    async def search(self, request):
        started = perf_counter()
        snapshot = self.coordinator.store.load_ready(self.coordinator.reader.scope(request.repository)).snapshot
        filters = SearchFilters(**request.filters.model_dump())
        limits = CandidateLimits(top_k=request.top_k)
        if request.mode != "bm25" and self.provider is None:
            raise RetrievalResultError("Dense debug requires a configured embedding provider.")
        if request.mode == "hybrid":
            result = await HybridRetriever(self.coordinator, {
                "dense": FilteredDenseChannel(self.provider, self.settings, self.coordinator),
                "bm25": FilteredBM25Channel(self.coordinator), "exact": ExactCandidateChannel(self.coordinator),
            }).search(HybridSearchRequest(request.repository, request.query, filters, limits))
            details = result.to_dict()
            channels, fused = details["channels"], details["fused"]
            final, revision, score_type = result.final_results(), result.fused.index_revision, "rrf"
            contributions, union = details["rrf_contributions"], result.union_count
        else:
            channel = FilteredBM25Channel(self.coordinator) if request.mode == "bm25" else FilteredDenseChannel(self.provider, self.settings, self.coordinator)
            batch = await channel.recall(ScopedSearchRequest(request.repository, request.query, getattr(limits, request.mode), filters=filters))
            channels, fused = {batch.channel: batch.to_dict()}, None
            final = to_retrieval_results(batch.candidates, repository=request.repository, index_revision=batch.index_revision,
                                        top_k=request.top_k, score_channel=request.mode)
            revision, score_type = batch.index_revision, "bm25" if request.mode == "bm25" else "cosine_similarity"
            contributions, union = {}, len(batch.candidates)
        if revision != snapshot.index.index_revision: raise SnapshotError("Snapshot changed while preparing debug metadata.")
        total = round((perf_counter() - started) * 1000, 3)
        channel_ms = {name: body["retrieval_latency_ms"] for name, body in channels.items()}
        fusion_ms = fused["retrieval_latency_ms"] if fused else 0.
        return DebugSearchResponse(repository=request.repository, query=request.query, top_k=request.top_k,
            collection=snapshot.index.scope.collection, embedding_model=snapshot.index.configuration["embedding"]["embedding_model"],
            embedding_dimension=snapshot.index.configuration["dimension"], retrieval_latency_ms=total,
            results=final, mode=request.mode, filters=request.filters, index_revision=revision, score_type=score_type,
            limits=asdict(limits), channels=channels, fused=fused, rrf_k=60 if fused is not None else None,
            rrf_contributions=contributions, untruncated_union_count=union,
            timings={"channel_ms":channel_ms, "fusion_ms":fusion_ms, "total_ms":total,
                     "overhead_ms":round(max(0.,total-sum(channel_ms.values())-fusion_ms),3)})
