import asyncio
from dataclasses import replace
import math

import pytest

from app.retrieval.bm25 import BM25CandidateChannel, BM25Index, BM25Parameters
from app.retrieval.candidates import CandidateSearchRequest, to_retrieval_results
from app.retrieval.snapshot_models import LexicalSnapshot, SnapshotNotReady
from app.retrieval.snapshot_store import SnapshotStore
from app.retrieval.snapshots import SnapshotCoordinator
from app.retrieval.tokenizer import CodeTokenizer
from tests.test_lexical_snapshots import Reader, image, publish


def token_snapshot(rows):
    base = image(files={f"{i}.py": "call()\n" for i in range(len(rows))})
    # Hand-labelled term vectors isolate the mathematical formula in unit tests;
    # channel/persistence tests below use real validated tokenizer output.
    snapshot = LexicalSnapshot.build(base, CodeTokenizer())
    return replace(snapshot, tokens=tuple(tuple(row) for row in rows))


def test_tf_idf_length_formula_and_explanations_match_hand_calculation():
    index = BM25Index(token_snapshot([["refund", "refund"], ["wallet"], ["wallet", "other", "other"]]))
    found = index.search(["refund"])
    expected_idf = math.log1p(2.5 / 1.5)
    expected = expected_idf * 2 * 2.5 / (2 + 1.5 * (1 - .75 + .75 * 2 / 2))
    assert found == [(0, pytest.approx(expected))]
    detail = index.explain(["refund"], index.snapshot.index.chunks[0].chunk_id)[0]
    assert detail["tf"] == 2 and detail["df"] == 1 and detail["idf"] == pytest.approx(expected_idf)
    assert detail["contribution"] == pytest.approx(expected)


def test_tf_saturation_and_b_parameter_effect():
    snapshot = token_snapshot([["x"], ["x", "padding", "padding", "padding"]])
    b0 = BM25Index(snapshot, BM25Parameters(b=0))
    assert [score for _, score in b0.search(["x"])] == pytest.approx([math.log1p(.5 / 2.5)] * 2)
    normalized = BM25Index(snapshot)
    assert normalized.search(["x"])[0][0] == 0
    tf = BM25Index(token_snapshot([["x"], ["x"] * 2, ["x"] * 4]), BM25Parameters(b=0))
    scores = dict(tf.search(["x"]))
    assert scores[2] > scores[1] > scores[0] and scores[2] < 4 * scores[0]


def test_common_terms_stay_positive_query_duplicates_do_not_reweight_and_ties_stable():
    index = BM25Index(token_snapshot([["x"], ["x"], ["x"]]))
    first = index.search(["x"])
    assert first == index.search(["x", "x", "missing"])
    assert all(score > 0 for _, score in first)
    assert [index.snapshot.index.chunks[d].chunk_id for d, _ in first] == sorted(c.chunk_id for c in index.snapshot.index.chunks)
    assert index.search(["absent"]) == [] and index.search([]) == []
    assert BM25Index(token_snapshot([])).search(["x"]) == []


@pytest.mark.parametrize("parameters", [{"k1": 0}, {"k1": True}, {"k1": float("nan")}, {"k1": 101}, {"b": -1}, {"b": 1.1}, {"b": float("inf")}])
def test_invalid_parameters_rejected(parameters):
    with pytest.raises(ValueError): BM25Parameters(**parameters)


@pytest.mark.parametrize("limit", [0, 61, True, "30"])
def test_internal_candidate_budget_kept(limit):
    with pytest.raises(ValueError): BM25Index(token_snapshot([["x"]])).search(["x"], limit)


def test_real_chunk_channel_provenance_isolation_empty_no_match_and_final_adapter(tmp_path):
    async def scenario():
        first = image(files={"WalletService.py": "def credit(amount):\n    raise ValueError('Amount must be positive')\n"})
        other, empty = image("other", {"Foreign.py": "credit credit credit\n"}), image("empty", {})
        store = SnapshotStore(tmp_path / "snapshot.db")
        for value in (first, other, empty): publish(store, value)
        channel = BM25CandidateChannel(SnapshotCoordinator(store, Reader(first, other, empty)))
        result = await channel.recall(CandidateSearchRequest("demo", "Amount must be positive", 30))
        assert len(result.candidates) == 1 and result.index_revision == first.index_revision
        item = result.candidates[0]
        assert set(item.channel_scores) == {"bm25"} and item.channel_ranks == {"bm25": 1}
        assert item.matched_queries == ("Amount must be positive",) and item.source_metadata == first.chunks[0].source
        assert to_retrieval_results(result.candidates, repository="demo", index_revision=first.index_revision, score_channel="bm25")[0].rank == 1
        for repo, query in (("demo", "𠀃𠀄𠀅"), ("demo", "?!💳"), ("empty", "credit")):
            assert (await channel.recall(CandidateSearchRequest(repo, query))).candidates == ()
        original = result.candidates[0].channel_scores["bm25"]
        publish(store, image("other", {"new.py": "wallet wallet wallet\n"}))
        assert (await channel.recall(CandidateSearchRequest("demo", "Amount must be positive"))).candidates[0].channel_scores["bm25"] == original
    asyncio.run(scenario())


def test_cache_invalidation_rebuild_and_nonready_even_for_no_effective_terms(tmp_path):
    async def scenario():
        old = image(files={"old.py": "credit()\n"})
        new = image(files={"new.py": "refund()\n"})
        store, reader = SnapshotStore(tmp_path / "snapshot.db"), Reader(old)
        publish(store, old)
        coordinator = SnapshotCoordinator(store, reader)
        channel = BM25CandidateChannel(coordinator, cache_size=1)
        assert (await channel.recall(CandidateSearchRequest("demo", "credit"))).candidates
        reader.images["demo"] = new
        publish(store, new)
        assert not (await channel.recall(CandidateSearchRequest("demo", "credit"))).candidates
        assert (await channel.recall(CandidateSearchRequest("demo", "refund"))).candidates[0].source_metadata.path == "new.py"
        assert len(channel._cache) == 1
        with store.rebuilding(new.scope, new.source_sha256):
            with pytest.raises(SnapshotNotReady): await channel.recall(CandidateSearchRequest("demo", "?!💳"))
    asyncio.run(scenario())


def test_cached_statistics_are_not_scored_with_old_parameters(tmp_path):
    async def scenario():
        value = image(files={"a.py": "credit() credit()\n", "b.py": "credit()\n"})
        store = SnapshotStore(tmp_path / "snapshot.db")
        publish(store, value)
        channel = BM25CandidateChannel(SnapshotCoordinator(store, Reader(value)))
        first = await channel.recall(CandidateSearchRequest("demo", "credit"))
        channel.parameters = BM25Parameters(b=0)
        second = await channel.recall(CandidateSearchRequest("demo", "credit"))
        assert {c.chunk_id: c.channel_scores["bm25"] for c in first.candidates} != {c.chunk_id: c.channel_scores["bm25"] for c in second.candidates}
        assert len(channel._cache) == 2
    asyncio.run(scenario())
