import asyncio
from copy import deepcopy
from dataclasses import replace

import pytest

from app.retrieval.expanded_retrieval import (ExpandedCandidateBatch, ExpandedRetriever, ExpandedRetrievalResult,
    fuse_channels, merge_channel)
from app.retrieval.expansion import ExpansionPolicy, QueryExpander
from app.retrieval.filtered import FilteredBM25Channel
from app.retrieval.exact import ExactCandidateChannel
from app.retrieval.hybrid import HybridRetriever, HybridSearchRequest
from app.retrieval.snapshot_models import SnapshotError
from app.retrieval.snapshot_store import SnapshotStore
from app.retrieval.snapshots import SnapshotCoordinator
from tests.test_hybrid_rrf import IMAGE, LIMITS, batch
from tests.test_lexical_snapshots import Reader, publish
from tests.test_query_expansion import Generator
from tests.test_rerank import worker


def test_variant_only_match_remains_truthful_and_repeated_match_does_not_add_votes():
    queries = ("credit", "credit wallet", "credit method")
    raw = [batch("dense", (0,1), query=queries[0]), batch("dense", (2,0), query=queries[1]), batch("dense", (2,1), query=queries[2])]
    two = merge_channel(raw[:2], queries[:2], 3)
    three = merge_channel(raw, queries, 3)
    assert [c.chunk_id for c in two.candidates] == [c.chunk_id for c in three.candidates]
    variant = next(c for c in three.candidates if c.chunk_id == IMAGE.chunks[2].chunk_id)
    assert variant.matched_queries == queries[1:] and queries[0] not in variant.matched_queries
    assert ExpandedCandidateBatch.from_dict(three.to_dict()) == three
    bm25_two = merge_channel([batch("bm25", (0,2), query=q) for q in queries[:2]], queries[:2], 3)
    bm25_three = merge_channel([batch("bm25", (0,2), query=q) for q in queries], queries, 3)
    a, b = fuse_channels((two,bm25_two)), fuse_channels((three,bm25_three))
    assert {c.chunk_id:c.channel_scores["rrf"] for c in a.candidates} == {c.chunk_id:c.channel_scores["rrf"] for c in b.candidates}
    assert all(c.channel_scores["rrf"] <= 2/61 for c in b.candidates)
    assert all(len(c.channel_ranks) <= 3 for c in b.candidates)


@pytest.mark.parametrize("change", ["query", "repository", "revision", "filter", "source", "nested", "limit"])
def test_bad_variant_scope_or_source_fails_before_truncation(change):
    from app.retrieval.scope import SearchFilters
    original = batch("dense", query="credit")
    variant = batch("dense", query="credit wallet")
    if change == "query": variant = replace(variant, query="other", candidates=tuple(replace(c,matched_queries=("other",)) for c in variant.candidates))
    if change == "repository": variant = replace(variant, repository="other", candidates=tuple(replace(c,repository="other") for c in variant.candidates))
    if change == "revision": variant = replace(variant, index_revision="sha256:"+"b"*64, candidates=tuple(replace(c,index_revision="sha256:"+"b"*64) for c in variant.candidates))
    if change == "filter": variant = replace(variant, filters=SearchFilters(path_prefix="backend/absent/"), candidates=())
    if change == "source":
        candidate = variant.candidates[-1]
        variant = replace(variant, candidates=(*variant.candidates[:-1], replace(candidate, source_metadata=replace(candidate.source_metadata, content=candidate.source_metadata.content.replace("credit", "broken")))))
    if change == "nested": variant = merge_channel([variant], (variant.query,), 3)
    if change == "limit": variant = replace(variant, candidate_limit=4)
    with pytest.raises((ValueError, SnapshotError)):
        merge_channel([original, variant], ("credit", "credit wallet"), 3)


class Dense:
    name = "dense"
    def __init__(self, coordinator, original, fault=None):
        self.coordinator, self.original, self.fault = coordinator, original, fault
        self.calls, self.cancelled = [], False
    async def recall(self, request):
        self.calls.append(request.query)
        if request.query != self.original and self.fault:
            if self.fault == "error": raise RuntimeError("private backend error")
            if self.fault == "cancel": raise asyncio.CancelledError()
            if self.fault == "timeout":
                try: await asyncio.sleep(60)
                finally: self.cancelled = True
            if self.fault == "drift":
                from tests.test_exact_and_filters import repo_image, source
                self.coordinator.reader.images["demo"] = repo_image(sources=[source(content="void changed() {}\n")])
        return batch("dense", (0,) if request.query == self.original else (2,0), query=request.query, limit=request.candidate_limit)


def runtime(tmp_path, *, original="处理任务", fault=None, generator=None, reranker=None):
    store = SnapshotStore(tmp_path/"snapshot.sqlite3")
    publish(store, IMAGE)
    coordinator = SnapshotCoordinator(store, Reader(IMAGE))
    dense = Dense(coordinator, original, fault)
    channels = {"dense": dense, "bm25": FilteredBM25Channel(coordinator), "exact": ExactCandidateChannel(coordinator)}
    retriever = ExpandedRetriever(coordinator, channels, expander=QueryExpander(generator or Generator([original+" credit"])), reranker=reranker)
    return retriever, dense, HybridSearchRequest("demo", original, limits=LIMITS)


def test_pipeline_reranks_with_original_query_preserving_variant_provenance_and_coordinates(tmp_path):
    async def run():
        adapter = worker(tmp_path)
        try:
            retriever, dense, request = runtime(tmp_path, reranker=adapter)
            result = await retriever.search(request, enabled=True, method="llm", rerank_enabled=True)
            assert result.fused.query == result.plan.original == request.query
            assert result.used_queries == (request.query, request.query+" credit")
            found = next(c for c in result.fused.candidates if c.chunk_id == IMAGE.chunks[2].chunk_id)
            assert found.matched_queries == (request.query+" credit",)
            assert result.reranked.status == "reranked" and result.reranked.fused.query == request.query
            assert ExpandedRetrievalResult.from_dict(result.to_dict()) == result
            indexed = {c.chunk_id:c.source for c in IMAGE.chunks}
            assert all(c.source_metadata == indexed[c.chunk_id] for c in result.reranked.ordered)
            assert result.to_dict()["reserved_candidate_slots"] == 18
            assert dense.calls == list(result.used_queries)
        finally:
            await adapter.aclose()
    asyncio.run(run())


def test_disabled_and_llm_failure_match_original_hybrid_exactly(tmp_path):
    async def run():
        retriever, dense, request = runtime(tmp_path, generator=Generator(raw="bad json"))
        reference = await HybridRetriever(retriever.coordinator, retriever.channels).search(request)
        disabled = await retriever.search(request)
        failed = await retriever.search(request, enabled=True, method="llm")
        assert disabled.plan.reason == "disabled" and failed.plan.reason == "invalid_output"
        for result in (disabled, failed):
            assert result.used_queries == (request.query,)
            assert result.fused.candidates == reference.fused.candidates
            assert result.final_results() == reference.final_results()
        assert len(dense.calls) == 3
    asyncio.run(run())


@pytest.mark.parametrize("fault,reason", [("error", "variant_error"), ("timeout", "variant_timeout"), ("cancel", None), ("drift", None)])
def test_partial_variant_failure_reverts_whole_expansion_but_drift_and_cancel_abort(tmp_path, fault, reason):
    async def run():
        retriever, dense, request = runtime(tmp_path, fault=fault)
        if reason is None:
            with pytest.raises(asyncio.CancelledError if fault == "cancel" else SnapshotError):
                await retriever.search(request, enabled=True, method="llm")
        else:
            result = await retriever.search(request, enabled=True, method="llm", policy=ExpansionPolicy(variant_timeout_seconds=.01 if fault == "timeout" else 60))
            assert result.reason == reason and result.used_queries == (request.query,)
            assert result.attempts[-1]["status"] == ("timeout" if fault == "timeout" else "failed")
            assert len(result.attempts) == 4 and len(result.batches) == 3
            assert ExpandedRetrievalResult.from_dict(result.to_dict()) == result
            if fault == "timeout": assert dense.cancelled
    asyncio.run(run())


def test_candidate_budget_admits_only_complete_query_groups_and_skips_unneeded_model(tmp_path):
    async def run():
        generator = Generator(["处理任务 credit", "处理任务 wallet"])
        retriever, dense, request = runtime(tmp_path, generator=generator)
        result = await retriever.search(request, enabled=True, method="llm", policy=ExpansionPolicy(max_candidate_slots=18))
        assert len(result.plan.queries) == 3 and len(result.used_queries) == 2 and result.reason == "candidate_budget"
        assert len(result.attempts) == 6 and result.to_dict()["reserved_candidate_slots"] == 18
        before = generator.calls
        only = await retriever.search(request, enabled=True, method="llm", policy=ExpansionPolicy(max_candidate_slots=9))
        assert only.reason == "candidate_budget" and len(only.attempts) == 3 and generator.calls == before
        with pytest.raises(ValueError):
            await retriever.search(request, enabled=True, method="llm", policy=ExpansionPolicy(max_candidate_slots=8))
    asyncio.run(run())


@pytest.mark.parametrize("change", ["original", "matched_query", "coordinate", "rank", "score", "fused", "attempt", "cost", "fallback", "missing_batch"])
def test_full_result_provenance_and_cost_tampering_are_rejected(tmp_path, change):
    async def run():
        retriever, _, request = runtime(tmp_path)
        value = deepcopy((await retriever.search(request, enabled=True, method="llm")).to_dict())
        if change == "original": value["request"]["query"] = "other"
        if change == "matched_query": value["fused"]["candidates"][0]["matched_queries"] = ["invented"]
        if change == "coordinate": value["merged"][0]["candidates"][0]["source_metadata"]["start_line"] += 1
        if change == "rank": value["merged"][0]["candidates"][0]["channel_ranks"]["dense"] = 3
        if change == "score": value["fused"]["candidates"][0]["channel_scores"]["rrf"] += .01
        if change == "fused": value["fused"]["candidates"] = []
        if change == "attempt": value["attempts"].append(value["attempts"][-1])
        if change == "cost": value["reserved_candidate_slots"] = 1
        if change == "fallback": value["reason"] = "variant_error"
        if change == "missing_batch": value["batches"].pop()
        with pytest.raises((ValueError, SnapshotError)):
            ExpandedRetrievalResult.from_dict(value)
    asyncio.run(run())
