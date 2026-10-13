"""Candidate capacity, identity boundaries and legacy Dense compatibility."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from qdrant_client import AsyncQdrantClient, models
from qdrant_client.http.exceptions import UnexpectedResponse

from app.context import ContextBuilder
from app.core.config import Settings
from app.core.exceptions import EmbeddingProviderError, QdrantCollectionConflictError, QdrantCollectionNotFoundError, RetrievalResultError
from app.embedding.identity import embedding_identity
from app.retrieval.candidates import CandidateBatch, CandidateLimits, CandidateSearchRequest, RetrievalCandidate, SourceMetadata, to_retrieval_results
from app.retrieval.dense import DenseCandidateChannel
from app.retrieval.service import RetrievalService
from app.retrieval.vector_store import QdrantVectorStore
from app.schemas.retrieval import RetrievalResult, RetrievalSearchRequest, RetrievalSearchResponse
from tests.test_retrieval import FakeEmbedding, scored_point


REVISION = "sha256:" + "a" * 64


def candidate(rank=1, **updates):
    return RetrievalCandidate(**({"chunk_id": str(uuid4()), "repository": "demo", "index_revision": REVISION,
                                  "source_metadata": SourceMetadata("a.py", "python", rank, rank, f"call_{rank}()\n"),
                                  "channel_scores": {"dense": 1 / rank}, "channel_ranks": {"dense": rank},
                                  "matched_queries": ("退款",)} | updates))


def batch(candidates):
    return CandidateBatch("demo", "退款", "dense", REVISION, 60, tuple(candidates), 1.)


def settings():
    return Settings(_env_file=None, embedding_model="test-embedding", embedding_base_url="http://localhost:11434/v1", qdrant_collection="test_code")


def mock_store():
    store = AsyncMock(spec=QdrantVectorStore)
    store.collection = "test_code"
    store.verify_search_collection.return_value = SimpleNamespace(dimension=3)
    store.search_repository_candidates.return_value = [scored_point()]
    return store


def test_sixty_candidates_adapt_to_public_twenty_and_context_five():
    pool = batch([candidate(rank) for rank in range(1, 61)])
    assert len(CandidateBatch.from_dict(pool.to_dict()).candidates) == 60
    result = to_retrieval_results(pool.candidates, repository="demo", index_revision=REVISION, top_k=20)
    assert len(result) == 20 and [r.rank for r in result] == list(range(1, 21))
    assert [r.chunk_id for r in result] == [c.chunk_id for c in pool.candidates[:20]]
    assert len(ContextBuilder(max_context_chunks=5, max_context_characters=16000).build("demo", result).sources) == 5
    assert "index_revision" not in result[0].model_dump() and "channel_scores" not in result[0].model_dump()


@pytest.mark.parametrize("top_k", [0, 21, True, "5", 2.5])
def test_adapter_does_not_relax_public_topk(top_k):
    with pytest.raises(ValueError):
        to_retrieval_results([candidate()], repository="demo", index_revision=REVISION, top_k=top_k)


@pytest.mark.parametrize("limit", [0, 61, True, "30", 2.5])
def test_internal_limit_validation_precedes_io(limit):
    with pytest.raises(ValueError):
        CandidateSearchRequest("demo", "退款", limit)
    client = AsyncMock()
    with pytest.raises(ValueError):
        asyncio.run(QdrantVectorStore(client, "test").search_repository_candidates("demo", [1., 0.], limit))
    client.query_points.assert_not_awaited()


def test_defaults_are_experiment_limits_not_public_settings():
    assert CandidateLimits() == CandidateLimits(30, 30, 10, 60, 30, 5)
    with pytest.raises(ValueError):
        CandidateLimits(fusion=20)


@pytest.mark.parametrize("revision", ["native-1", "sha256:" + "A" * 64, "a" * 64, True])
def test_unknown_revision_is_explicit_and_fake_revisions_rejected(revision):
    assert candidate(index_revision=None).index_revision is None
    with pytest.raises(ValueError):
        candidate(index_revision=revision)


@pytest.mark.parametrize("change", [
    {"channel_scores": {"dense": float("nan")}}, {"channel_scores": {"dense": True}},
    {"channel_ranks": {"dense": 0}}, {"channel_ranks": {"dense": 61}},
    {"channel_ranks": {"bm25": 1}}, {"matched_queries": ("",)}, {"matched_queries": ("退款", "退款")},
    {"chunk_id": "broken"}, {"repository": "../demo"},
])
def test_invalid_provenance_is_rejected(change):
    with pytest.raises(ValueError):
        candidate(**change)


def test_channel_maps_and_sources_cannot_mutate_after_validation():
    scores, ranks = {"dense": .5, "bm25": 2.}, {"dense": 30, "bm25": 2}
    item = candidate(channel_scores=scores, channel_ranks=ranks, matched_queries=("退款", "refund"))
    scores["dense"] = float("nan")
    ranks["dense"] = -1
    assert item.channel_scores["dense"] == .5 and item.channel_ranks["dense"] == 30
    with pytest.raises(TypeError):
        item.channel_scores["dense"] = .9
    with pytest.raises(AttributeError):
        item.source_metadata.path = "else.py"
    assert RetrievalCandidate.from_dict(item.to_dict()) == item


def test_unvalidated_objects_and_string_query_sequences_are_rejected():
    with pytest.raises(ValueError):
        candidate(matched_queries="refund")
    with pytest.raises(ValueError):
        batch([SimpleNamespace(chunk_id=str(uuid4()))])
    with pytest.raises(RetrievalResultError):
        to_retrieval_results([SimpleNamespace(chunk_id=str(uuid4()))], repository="demo", index_revision=REVISION)


@pytest.mark.parametrize("tail", ["repository", "revision", "unknown_revision", "duplicate", "missing_score"])
def test_adapter_validates_tail_before_returning_top_five(tail):
    pool = [candidate(rank) for rank in range(1, 7)]
    updates = {"repository": {"repository": "other"}, "revision": {"index_revision": "sha256:" + "b" * 64},
               "unknown_revision": {"index_revision": None}, "duplicate": {"chunk_id": pool[0].chunk_id},
               "missing_score": {"channel_scores": {"bm25": 1.}, "channel_ranks": {"bm25": 6}}}[tail]
    pool[-1] = replace(pool[-1], **updates)
    with pytest.raises(RetrievalResultError) as error:
        to_retrieval_results(pool, repository="demo", index_revision=REVISION)
    assert "call_" not in str(error.value)


@pytest.mark.parametrize("change", [{"path": "../secret"}, {"end_line": 2}, {"start_line": True},
                                   {"chunking_strategy": "ast"}, {"symbol": "A.call"}])
def test_candidate_source_uses_existing_coordinate_and_ast_validation(change):
    with pytest.raises(ValueError):
        SourceMetadata(**({"path": "a.py", "language": "python", "start_line": 1, "end_line": 1, "content": "x\n"} | change))


def test_partial_chunking_metadata_does_not_get_legacy_defaults():
    with pytest.raises(ValueError, match="complete"):
        SourceMetadata.from_payload({"path": "a.py", "language": "python", "start_line": 1, "end_line": 1,
                                     "content": "x\n", "symbol": "A.call"})


@pytest.mark.parametrize("bad_batch", ["rank_gap", "query", "count", "duplicate"])
def test_channel_batches_reject_corrupt_ranking(bad_batch):
    pool = [candidate(1), candidate(2)]
    if bad_batch == "rank_gap":
        pool[1] = candidate(3)
    elif bad_batch == "query":
        pool[1] = candidate(2, matched_queries=("other",))
    elif bad_batch == "duplicate":
        pool[1] = candidate(2, chunk_id=pool[0].chunk_id)
    with pytest.raises(ValueError):
        CandidateBatch("demo", "退款", "dense", REVISION, 1 if bad_batch == "count" else 30, tuple(pool), 1.)


def test_internal_store_preserves_query_filter_and_maps_errors():
    client = AsyncMock()
    client.query_points.return_value = SimpleNamespace(points=[])
    store = QdrantVectorStore(client, "test")
    assert asyncio.run(store.search_repository_candidates("demo", [1., 0.], 30)) == []
    options = client.query_points.call_args.kwargs
    assert options["limit"] == 30 and options["query_filter"] == store.repository_filter("demo")
    assert options["with_vectors"] is False and "chunking_version" in options["with_payload"]
    client.query_points.side_effect = UnexpectedResponse(status_code=404, reason_phrase="missing", content=b"private", headers={})
    with pytest.raises(QdrantCollectionNotFoundError):
        asyncio.run(store.search_repository_candidates("demo", [1., 0.], 30))


@pytest.mark.parametrize("failure", ["collection", "vector", "dimension"])
def test_dense_candidate_preconditions_prevent_queries(failure):
    provider, store = FakeEmbedding(), mock_store()
    expected = QdrantCollectionConflictError
    if failure == "collection":
        store.verify_search_collection.side_effect = QdrantCollectionConflictError("incompatible")
    elif failure == "vector":
        provider.output = [[0., 0., 0.]]
        expected = EmbeddingProviderError
    else:
        store.verify_search_collection.return_value = SimpleNamespace(dimension=4)
    with pytest.raises(expected):
        asyncio.run(DenseCandidateChannel(provider, store, settings()).recall(CandidateSearchRequest("demo", "退款")))
    store.search_repository_candidates.assert_not_awaited()
    if failure == "collection":
        assert provider.calls == []


@pytest.mark.parametrize("malformation", ["foreign", "identity", "model", "schema", "content", "duplicate", "too_many", "nan"])
def test_bad_candidate_at_rank_thirty_cannot_hide_after_topk(malformation):
    provider, store = FakeEmbedding(), mock_store()
    points = [scored_point() for _ in range(30)]
    changes = {"foreign": {"repository": "other"}, "identity": {"chunk_id": "bad"},
               "model": {"embedding_model": "different"}, "schema": {"schema_version": "different"},
               "content": {"end_line": 4}}
    if malformation in changes:
        points[-1].payload.update(changes[malformation])
    elif malformation == "duplicate":
        points[-1] = points[0]
    elif malformation == "too_many":
        points.append(scored_point())
    else:
        points[-1].score = float("nan")
    store.search_repository_candidates.return_value = points
    with pytest.raises(RetrievalResultError) as error:
        asyncio.run(DenseCandidateChannel(provider, store, settings()).recall(CandidateSearchRequest("demo", "退款")))
    assert "refund()" not in str(error.value)


def test_real_in_memory_sixty_candidates_foreign_filter_and_legacy_topk_compatibility():
    async def scenario():
        client = AsyncQdrantClient(":memory:")
        try:
            config, provider = settings(), FakeEmbedding()
            store = QdrantVectorStore(client, "test_code")
            await store.prepare_index(3, embedding_identity(config, provider.model_name))
            points = []
            for rank in range(1, 61):
                point = scored_point(path=f"{rank}.py")
                points.append(models.PointStruct(id=point.id, payload=point.payload, vector=[1., rank / 60, 0.]))
            foreign = scored_point(repository="other", content="FOREIGN\n")
            points.append(models.PointStruct(id=foreign.id, payload=foreign.payload, vector=[1., 0., 0.]))
            await store.upsert_points(points)
            before, _ = await client.scroll("test_code", limit=100, with_vectors=True, with_payload=True)
            channel = DenseCandidateChannel(provider, store, config, index_revision=REVISION)
            recalled = await channel.recall(CandidateSearchRequest("demo", "退款", 60))
            assert len(recalled.candidates) == 60
            assert "FOREIGN" not in str(recalled.to_dict())
            for top_k in (1, 5, 20):
                legacy = await RetrievalService(provider, store, config).search(RetrievalSearchRequest(repository="demo", query="退款", top_k=top_k))
                adapted = to_retrieval_results(recalled.candidates, repository="demo", index_revision=REVISION, top_k=top_k)
                assert adapted == legacy.results
                assert RetrievalSearchResponse.model_validate(legacy.model_dump()).results == adapted
            empty = await channel.recall(CandidateSearchRequest("empty", "退款", 30))
            assert empty.candidates == () and to_retrieval_results([], repository="empty", index_revision=REVISION) == []
            after, _ = await client.scroll("test_code", limit=100, with_vectors=True, with_payload=True)
            assert [(p.id, p.payload) for p in after] == [(p.id, p.payload) for p in before]
            # Qdrant's local implementation renormalizes its float32 arrays on
            # query; persistent-server byte preservation is checked separately.
            for old, new in zip(before, after, strict=True):
                assert new.vector == pytest.approx(old.vector, abs=1e-6)
            with pytest.raises(ValueError):
                await store.search_repository("demo", [1., 0., 0.], 30)
            with pytest.raises(ValueError):
                RetrievalResult(rank=30, **{k: v for k, v in adapted[0].model_dump().items() if k != "rank"})
        finally:
            await client.close()
    asyncio.run(scenario())
