import asyncio
from copy import deepcopy
from dataclasses import replace
import math

import pytest

from app.retrieval.candidates import CandidateLimits, RetrievalCandidate
from app.retrieval.hybrid import HybridResult, HybridRetriever, HybridSearchRequest
from app.retrieval.rrf import RRFParameters, fuse_rrf
from app.retrieval.scope import ScopedCandidateBatch, SearchFilters
from app.retrieval.snapshot_models import SnapshotError, SnapshotNotReady
from app.retrieval.snapshot_store import SnapshotStore
from app.retrieval.snapshots import SnapshotCoordinator
from tests.test_exact_and_filters import repo_image, source
from tests.test_lexical_snapshots import Reader, publish

IMAGE = repo_image()
CHUNKS = IMAGE.chunks
LIMITS = CandidateLimits(dense=3, bm25=3, exact=3, fusion=3, rerank=3, top_k=2)


def batch(channel, positions=(0, 1, 2), *, image=IMAGE, filters=None, query="credit", scores=None, limit=3):
    selected = tuple(RetrievalCandidate(image.chunks[i].chunk_id, image.scope.repository, image.index_revision,
        image.chunks[i].source, {channel: (scores[rank-1] if scores else 10. / rank)}, {channel: rank}, (query,))
        for rank, i in enumerate(positions, 1))
    return ScopedCandidateBatch(image.scope.repository, query, channel, image.index_revision, limit, selected, 1., filters=filters or SearchFilters())


@pytest.mark.parametrize("k", [True, 0, -1, 1.5, float("inf"), 10001])
def test_rrf_rejects_invalid_k(k):
    with pytest.raises(ValueError): RRFParameters(k)


def test_rank_one_and_two_contributions_and_raw_scores_are_not_added():
    dense, bm25 = batch("dense", (0, 1), scores=[-.8, -.9]), batch("bm25", (1, 0), scores=[1e12, 1e-12])
    result = fuse_rrf([dense, bm25])
    assert len(result.candidates) == 2
    expected = math.fsum([1/61, 1/62])
    assert all(c.channel_scores["rrf"] == expected for c in result.candidates)
    assert [c.chunk_id for c in result.candidates] == sorted(c.chunk_id for c in dense.candidates)
    for c in result.candidates:
        original = next(d for d in dense.candidates if d.chunk_id == c.chunk_id)
        assert c.channel_scores["dense"] == original.channel_scores["dense"]
        assert c.channel_ranks["dense"] == original.channel_ranks["dense"]


def test_unique_chunk_identity_gets_one_vote_per_channel_and_permutation_is_stable():
    inputs = [batch("dense", (0, 1)), batch("bm25", (1, 2)), batch("exact", (2, 0))]
    one, two = fuse_rrf(inputs), fuse_rrf(inputs[::-1])
    assert one.candidates == two.candidates and len(one.candidates) == 3
    assert all(set(c.channel_ranks) - {"rrf"} in ({"dense", "bm25"}, {"bm25", "exact"}, {"dense", "exact"}) for c in one.candidates)
    assert [c.channel_ranks["rrf"] for c in one.candidates] == [1, 2, 3]


def test_fusion_truncates_after_merging_all_channel_ranks():
    result = fuse_rrf([batch("dense", (0, 2)), batch("bm25", (1, 2))], candidate_limit=1)
    assert result.candidates[0].chunk_id == CHUNKS[2].chunk_id
    assert result.candidates[0].channel_ranks == {"dense": 2, "bm25": 2, "rrf": 1}


@pytest.mark.parametrize("change", ["repository", "revision", "filters", "query", "unknown_revision"])
def test_mixed_scope_revision_and_query_rejected_even_for_empty_batches(change):
    a, b = batch("dense", ()), batch("bm25", ())
    if change == "repository": b = replace(b, repository="other")
    elif change == "revision": b = replace(b, index_revision="sha256:" + "b"*64)
    elif change == "filters": b = replace(b, filters=SearchFilters(language="java"))
    elif change == "query": b = replace(b, query="other")
    else: a, b = replace(a, index_revision=None), replace(b, index_revision=None)
    with pytest.raises(SnapshotError): fuse_rrf([a, b])


@pytest.mark.parametrize("change", ["content", "path", "symbol"])
def test_conflicting_source_identity_rejected_before_truncation(change):
    a, b = batch("dense"), batch("bm25")
    c = b.candidates[-1]
    data = c.source_metadata.to_dict()
    if change == "content": data["content"] = data["content"].replace("credit", "broken")
    elif change == "path": data["path"] = "forged/source.java"
    else: data["symbol"] = "Other.credit"; data["parent_symbol"] = "Other"
    c = replace(c, source_metadata=type(c.source_metadata)(**data))
    b = replace(b, candidates=(*b.candidates[:-1], c))
    with pytest.raises(SnapshotError, match="conflicting"):
        fuse_rrf([a, b], candidate_limit=1)


def test_duplicate_channel_and_nested_fused_votes_rejected():
    a = batch("dense")
    with pytest.raises(ValueError): fuse_rrf([a, a])
    nested = replace(a.candidates[0], channel_scores={"dense": 1., "exact": 100.}, channel_ranks={"dense": 1, "exact": 1})
    with pytest.raises(ValueError): fuse_rrf([replace(a, candidates=(nested,))])
    with pytest.raises(TypeError): fuse_rrf([a], parameters=0)


def test_empty_channels_remain_empty_with_verified_revision():
    result = fuse_rrf([batch("dense", ()), batch("bm25", ())])
    assert not result.candidates and result.index_revision == IMAGE.index_revision


def test_seventy_distinct_channel_candidates_merge_to_bounded_sixty():
    image=repo_image(sources=[source(f"file{i}.java",f"A{i}.credit",start=1) for i in range(70)])
    channels=[batch("dense",tuple(range(30)),image=image,limit=30),
              batch("bm25",tuple(range(30,60)),image=image,limit=30),
              batch("exact",tuple(range(60,70)),image=image,limit=10)]
    fused=fuse_rrf(channels)
    assert len(fused.candidates)==60 and len({c.chunk_id for c in fused.candidates})==60
    assert [c.channel_ranks["rrf"] for c in fused.candidates]==list(range(1,61))
    expected=sorted((c for b in channels for c in b.candidates),key=lambda c:(next(iter(c.channel_ranks.values())),c.chunk_id))[:60]
    assert [c.chunk_id for c in fused.candidates]==[c.chunk_id for c in expected]


def result():
    channels = (batch("dense", (0, 1)), batch("bm25", (1, 2)))
    request = HybridSearchRequest("demo", "credit", limits=LIMITS)
    return HybridResult(request, RRFParameters(), channels, fuse_rrf(channels, candidate_limit=3), 3.)


def test_hybrid_roundtrip_provenance_and_final_rank_numbering():
    value = result()
    assert HybridResult.from_dict(value.to_dict()) == value
    assert len(value.final_results()) == 2 and [r.rank for r in value.final_results()] == [1, 2]
    assert value.union_count == 3
    assert all(r.score == c.channel_scores["rrf"] for r, c in zip(value.final_results(), value.fused.candidates))


@pytest.mark.parametrize("change", ["score", "raw_rank", "contribution", "union", "final_rank", "limits", "parameters"])
def test_hybrid_serialization_tampering_rejected(change):
    data = deepcopy(result().to_dict())
    if change == "score": data["fused"]["candidates"][0]["channel_scores"]["rrf"] += .1
    elif change == "raw_rank": data["fused"]["candidates"][0]["channel_ranks"]["dense"] = 3
    elif change == "contribution": next(iter(next(iter(data["rrf_contributions"].values())).values()))["contribution"] += .1
    elif change == "union": data["untruncated_union_count"] += 1
    elif change == "final_rank": data["results"][0]["rank"] = 2
    elif change == "limits": data["request"]["limits"]["top_k"] = 1
    else: data["parameters"]["rank_start"] = 0
    with pytest.raises(ValueError): HybridResult.from_dict(data)


class Channel:
    def __init__(self, name, coordinator, operation=None):
        self.name, self.coordinator, self.operation, self.calls = name, coordinator, operation, 0
    async def recall(self, request):
        self.calls += 1
        if self.operation: await self.operation()
        return batch(self.name, limit=request.candidate_limit)


def test_hybrid_guarded_runtime_and_nonready_fail_closed(tmp_path):
    async def scenario():
        store=SnapshotStore(tmp_path/'snapshot.db'); publish(store, IMAGE)
        coordinator=SnapshotCoordinator(store, Reader(IMAGE))
        channels={n:Channel(n,coordinator) for n in ("dense","bm25")}
        retriever=HybridRetriever(coordinator,channels)
        value=await retriever.search(HybridSearchRequest("demo","credit",limits=LIMITS))
        assert len(value.final_results())==2 and HybridResult.from_dict(value.to_dict())==value
        with store.rebuilding(IMAGE.scope,IMAGE.source_sha256):
            with pytest.raises(SnapshotNotReady): await retriever.search(value.request)
        assert [c.calls for c in channels.values()]==[1,1]
    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["error", "cancel", "rebuild"])
def test_channel_failure_cancel_or_rebuild_discards_whole_hybrid_result(tmp_path,failure):
    async def scenario():
        store=SnapshotStore(tmp_path/'snapshot.db');publish(store,IMAGE)
        coordinator=SnapshotCoordinator(store,Reader(IMAGE))
        async def interrupt():
            if failure=="error":raise RuntimeError("channel failed")
            if failure=="cancel":raise asyncio.CancelledError()
            publish(store,IMAGE)
        channels={"dense":Channel("dense",coordinator),"bm25":Channel("bm25",coordinator,interrupt)}
        with pytest.raises((RuntimeError,asyncio.CancelledError,SnapshotError)):
            await HybridRetriever(coordinator,channels).search(HybridSearchRequest("demo","credit",limits=LIMITS))
    asyncio.run(scenario())


def test_hybrid_rejects_channels_from_different_coordinators(tmp_path):
    a=SnapshotCoordinator(SnapshotStore(tmp_path/'a.db'),Reader(IMAGE))
    b=SnapshotCoordinator(SnapshotStore(tmp_path/'b.db'),Reader(IMAGE))
    with pytest.raises(ValueError):HybridRetriever(a,{"dense":Channel("dense",a),"bm25":Channel("bm25",b)})
