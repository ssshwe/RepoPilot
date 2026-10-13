import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from qdrant_client import AsyncQdrantClient, models
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse

from app.core.config import Settings
from app.core.exceptions import (
    EmbeddingConfigurationError, EmbeddingProviderError, QdrantCollectionConflictError,
    QdrantCollectionNotFoundError, QdrantUnavailableError, RetrievalResultError,
)
from app.embedding.base import EmbeddingProvider
from app.embedding.identity import embedding_identity
from app.main import app
from app.retrieval.service import RetrievalService
from app.retrieval.vector_store import QdrantVectorStore
from app.schemas.retrieval import RetrievalSearchRequest


class FakeEmbedding(EmbeddingProvider):
    def __init__(self):
        self.calls = []
        self.output = [[1., 0., 0.]]
        self.closed = 0

    @property
    def model_name(self):
        return "test-embedding"

    async def embed_texts(self, texts):
        self.calls.append(list(texts))
        return self.output

    async def aclose(self):
        self.closed += 1


@pytest.fixture
def settings():
    return Settings(
        _env_file=None, embedding_model="test-embedding", embedding_api_key="private-api-key",
        embedding_base_url="http://localhost:11434/v1", qdrant_collection="test_code",
    )


def payload(*, repository="demo", path="service/refund.py", content="refund()\n"):
    return {
        "chunk_id": str(uuid4()), "repository": repository, "path": path,
        "language": "python", "start_line": 3, "end_line": 3, "content": content,
        "embedding_model": "test-embedding", "schema_version": "code-points-v1",
    }


def scored_point(**changes):
    data = payload()
    data.update(changes)
    return SimpleNamespace(id=data["chunk_id"], score=0.8, payload=data)


def run(coroutine):
    return asyncio.run(coroutine)


@pytest.mark.parametrize("top_k,expected", [(1, 1), (2, 2), (3, 3), (5, 3), (20, 3)])
def test_cosine_topk_filters_before_limit_and_preserves_source(settings, top_k, expected):
    async def scenario():
        client = AsyncQdrantClient(":memory:")
        store = QdrantVectorStore(client, "test_code")
        await store.prepare_index(3, embedding_identity(settings, "test-embedding"))
        target_ids = []
        points = []
        for index, vector in enumerate(([.8, .6, 0.], [.6, .8, 0.], [-1., 0., 0.])):
            data = payload(path=f"service/{index}.py", content=f"  line_{index}()\n")
            target_ids.append(data["chunk_id"])
            points.append(models.PointStruct(id=data["chunk_id"], vector=vector, payload=data))
        # More similar foreign vectors would displace the target if filtered afterward.
        for repository in ("other", "demo-extra", "Demo"):
            data = payload(repository=repository, content="FOREIGN_SOURCE\n")
            points.append(models.PointStruct(id=data["chunk_id"], vector=[1., 0., 0.], payload=data))
        await store.upsert_points(points)
        before = (await client.get_collection("test_code")).model_dump()
        provider = FakeEmbedding()
        result = await RetrievalService(provider, store, settings).search(
            RetrievalSearchRequest(repository="demo", query="退款逻辑在哪里？", top_k=top_k),
        )
        assert len(result.results) == expected
        assert [item.chunk_id for item in result.results] == target_ids[:expected]
        assert [item.score for item in result.results] == pytest.approx([.8, .6, -1.][:expected])
        assert [item.rank for item in result.results] == list(range(1, expected + 1))
        assert all(item.repository == "demo" for item in result.results)
        assert "FOREIGN_SOURCE" not in result.model_dump_json()
        assert result.results[0].content == "  line_0()\n"
        assert result.results[0].start_line == result.results[0].end_line == 3
        assert result.top_k == top_k and result.embedding_dimension == 3
        assert result.retrieval_latency_ms >= 0
        assert provider.calls == [["退款逻辑在哪里？"]]
        assert (await client.get_collection("test_code")).model_dump() == before
        unknown = await RetrievalService(provider, store, settings).search(
            RetrievalSearchRequest(repository="not-indexed", query="退款"),
        )
        assert unknown.results == []
        await client.close()
    run(scenario())


def test_empty_bound_collection_returns_no_results(settings):
    async def scenario():
        client = AsyncQdrantClient(":memory:")
        store = QdrantVectorStore(client, "test_code")
        await store.prepare_index(3, embedding_identity(settings, "test-embedding"))
        result = await RetrievalService(FakeEmbedding(), store, settings).search(
            RetrievalSearchRequest(repository="demo", query="退款"),
        )
        assert result.results == []
        assert (await client.get_collection("test_code")).points_count == 0
        await client.close()
    run(scenario())


@pytest.mark.parametrize("identity_change", ["unbound", "model", "endpoint", "provider", "schema"])
def test_search_does_not_bind_or_mutate_incompatible_collection(settings, identity_change):
    async def scenario():
        client = AsyncQdrantClient(":memory:")
        identity = embedding_identity(settings, "test-embedding")
        field = {"model": "embedding_model", "endpoint": "endpoint_sha256",
                 "provider": "embedding_provider", "schema": "schema_version"}.get(identity_change)
        if field:
            identity[field] = "different"
        await client.create_collection(
            "test_code", vectors_config=models.VectorParams(size=3, distance=models.Distance.COSINE),
            metadata={} if identity_change == "unbound" else {"repopilot_index": identity},
        )
        before = (await client.get_collection("test_code")).model_dump()
        provider = FakeEmbedding()
        with pytest.raises(QdrantCollectionConflictError):
            await RetrievalService(provider, QdrantVectorStore(client, "test_code"), settings).search(
                RetrievalSearchRequest(repository="demo", query="退款"),
            )
        assert provider.calls == []
        assert (await client.get_collection("test_code")).model_dump() == before
        await client.close()
    run(scenario())


def test_store_passes_repository_limit_and_payload_selection_to_query_points():
    client = AsyncMock()
    client.query_points.return_value = SimpleNamespace(points=[])
    store = QdrantVectorStore(client, "test_code")
    assert run(store.search_repository("demo", [1., 0., 0.], 7)) == []
    options = client.query_points.call_args.kwargs
    assert options["collection_name"] == "test_code"
    assert options["query"] == [1., 0., 0.]
    assert options["query_filter"].must == [models.FieldCondition(key="repository", match=models.MatchValue(value="demo"))]
    assert options["limit"] == 7
    assert options["with_vectors"] is False
    assert {"repository", "path", "language", "start_line", "end_line", "content"} <= set(options["with_payload"])
    assert {"symbol", "node_type", "parent_symbol", "chunking_strategy", "chunking_version"} <= set(options["with_payload"])


@pytest.mark.parametrize("top_k", [0, 21, True, "5", 2.5])
def test_direct_store_topk_validation_precedes_database(top_k):
    client = AsyncMock()
    with pytest.raises(ValueError):
        run(QdrantVectorStore(client, "test_code").search_repository("demo", [1., 0.], top_k))
    client.query_points.assert_not_awaited()


@pytest.mark.parametrize("status,expected", [(404, QdrantCollectionNotFoundError), (401, QdrantUnavailableError), (500, QdrantUnavailableError)])
def test_store_query_maps_http_errors_without_upstream_body(status, expected):
    client = AsyncMock()
    client.query_points.side_effect = UnexpectedResponse(status_code=status, reason_phrase="failure", content=b"private-api-key", headers={})
    with pytest.raises(expected) as error:
        run(QdrantVectorStore(client, "test_code").search_repository("demo", [1., 0.], 5))
    assert "private-api-key" not in str(error.value)


def test_store_query_network_failure_is_safe():
    client = AsyncMock()
    client.query_points.side_effect = ResponseHandlingException(ConnectionError("private-api-key"))
    with pytest.raises(QdrantUnavailableError) as error:
        run(QdrantVectorStore(client, "test_code").search_repository("demo", [1., 0.], 5))
    assert "private-api-key" not in str(error.value)


@pytest.mark.parametrize("output", [[], [[1., 0., 0.]] * 2, [[0., 0., 0.]], [[float("nan"), 1., 0.]], [[True, 1., 0.]], None])
def test_invalid_query_vectors_never_reach_search(settings, output):
    store = AsyncMock(spec=QdrantVectorStore)
    store.verify_search_collection.return_value = SimpleNamespace(dimension=3)
    provider = FakeEmbedding()
    provider.output = output
    with pytest.raises(EmbeddingProviderError):
        run(RetrievalService(provider, store, settings).search(RetrievalSearchRequest(repository="demo", query="退款")))
    store.search_repository.assert_not_awaited()


def test_query_dimension_conflict_never_reaches_search(settings):
    store = AsyncMock(spec=QdrantVectorStore)
    store.verify_search_collection.return_value = SimpleNamespace(dimension=4)
    with pytest.raises(QdrantCollectionConflictError, match="dimension is 3"):
        run(RetrievalService(FakeEmbedding(), store, settings).search(RetrievalSearchRequest(repository="demo", query="退款")))
    store.search_repository.assert_not_awaited()


@pytest.mark.parametrize("change", [
    {"repository": "other"}, {"path": "../secret.py"}, {"path": "C:/secret.py"},
    {"path": "/root/secret.py"}, {"path": "a\\b.py"}, {"path": "a//b.py"},
    {"start_line": True}, {"start_line": "3"}, {"end_line": 2},
    {"end_line": 4}, {"content": "\n"}, {"content": "x" * 8193},
    {"language": None}, {"embedding_model": "other"}, {"schema_version": "other"},
    {"chunk_id": "invalid-id"},
])
def test_invalid_or_foreign_payload_never_returns_partial_source(settings, change):
    store = AsyncMock(spec=QdrantVectorStore)
    store.collection = "test_code"
    store.verify_search_collection.return_value = SimpleNamespace(dimension=3)
    point = scored_point(**change)
    store.search_repository.return_value = [scored_point(), point]
    with pytest.raises(RetrievalResultError) as error:
        run(RetrievalService(FakeEmbedding(), store, settings).search(RetrievalSearchRequest(repository="demo", query="退款")))
    assert "private-api-key" not in str(error.value)
    assert "refund()" not in str(error.value)


@pytest.mark.parametrize("malformation", ["missing_payload", "missing_content", "nan_score", "duplicate_id", "too_many", "id_mismatch"])
def test_malformed_search_response_is_safe(settings, malformation):
    store = AsyncMock(spec=QdrantVectorStore)
    store.collection = "test_code"
    store.verify_search_collection.return_value = SimpleNamespace(dimension=3)
    point = scored_point()
    if malformation == "missing_payload":
        point.payload = None
    elif malformation == "missing_content":
        del point.payload["content"]
    elif malformation == "nan_score":
        point.score = float("nan")
    elif malformation == "id_mismatch":
        point.id = str(uuid4())
    store.search_repository.return_value = [point] * (2 if malformation == "duplicate_id" else 6 if malformation == "too_many" else 1)
    with pytest.raises(RetrievalResultError):
        run(RetrievalService(FakeEmbedding(), store, settings).search(RetrievalSearchRequest(repository="demo", query="退款")))


@pytest.fixture
def api(settings):
    provider = FakeEmbedding()
    client = AsyncMock()
    store = AsyncMock(spec=QdrantVectorStore)
    store.collection = "test_code"
    store.verify_search_collection.return_value = SimpleNamespace(dimension=3)
    store.search_repository.return_value = [scored_point()]
    with (
        patch("app.api.retrieval.get_settings", return_value=settings),
        patch("app.api.retrieval.EmbeddingProviderFactory.create", return_value=provider) as factory,
        patch("app.api.retrieval.create_qdrant_client", return_value=client) as client_factory,
        patch("app.api.retrieval.QdrantVectorStore", return_value=store),
        TestClient(app) as http,
    ):
        yield http, provider, client, store, factory, client_factory


def test_api_returns_ranked_source_and_logs_query_without_source_or_credentials(api, caplog):
    http, provider, client, _, _, _ = api
    query = "退款逻辑\n在哪里？"
    with caplog.at_level("INFO"):
        response = http.post("/api/retrieval/search", json={"repository": "demo", "query": query})
    assert response.status_code == 200
    result = response.json()
    assert result["query"] == query and result["top_k"] == 5
    assert result["embedding_dimension"] == 3 and result["embedding_model"] == "test-embedding"
    assert result["results"][0]["rank"] == 1
    assert result["results"][0]["score"] == .8
    assert result["results"][0]["content"] == "refund()\n"
    assert "vector" not in result["results"][0]
    assert "retrieval_query=\"退款逻辑\\n在哪里？\"" in caplog.text
    assert "top_k=5" in caplog.text and "retrieval_latency_ms=" in caplog.text
    assert "private-api-key" not in caplog.text and "refund()" not in caplog.text
    assert provider.closed == 1
    client.close.assert_awaited_once()


@pytest.mark.parametrize("change", [
    {"query": ""}, {"query": " \n\t"}, {"query": 3}, {"query": None}, {"query": "x" * 8193},
    {"repository": "../other"}, {"repository": "CON"}, {"repository": "a\\b"}, {"repository": "a" * 65},
    {"top_k": 0}, {"top_k": 21}, {"top_k": True}, {"top_k": "5"}, {"top_k": 2.5}, {"top_k": None},
    {"collection": "other"}, {"model": "other"},
])
def test_invalid_api_input_opens_no_clients(api, change):
    http, _, _, _, factory, client_factory = api
    body = {"repository": "demo", "query": "退款", **change}
    assert http.post("/api/retrieval/search", json=body).status_code == 422
    factory.assert_not_called()
    client_factory.assert_not_called()


@pytest.mark.parametrize("body", [{}, {"repository": "demo"}, {"query": "退款"}])
def test_missing_request_fields_are_422(api, body):
    http, _, _, _, factory, _ = api
    assert http.post("/api/retrieval/search", json=body).status_code == 422
    factory.assert_not_called()


@pytest.mark.parametrize("exception,status", [
    (QdrantCollectionNotFoundError("safe missing collection"), 404),
    (QdrantCollectionConflictError("safe model conflict"), 409),
    (QdrantUnavailableError("safe unavailable database"), 503),
])
def test_collection_precheck_error_status_closes_clients_without_embedding(api, exception, status):
    http, provider, client, store, _, _ = api
    store.verify_search_collection.side_effect = exception
    assert http.post("/api/retrieval/search", json={"repository": "demo", "query": "退款"}).status_code == status
    assert provider.calls == [] and provider.closed == 1
    store.search_repository.assert_not_awaited()
    client.close.assert_awaited_once()


def test_embedding_failure_closes_clients_without_search(api):
    http, provider, client, store, _, _ = api
    provider.embed_texts = AsyncMock(side_effect=EmbeddingProviderError("safe embedding error"))
    assert http.post("/api/retrieval/search", json={"repository": "demo", "query": "退款"}).status_code == 502
    assert provider.closed == 1
    store.search_repository.assert_not_awaited()
    client.close.assert_awaited_once()


def test_bad_payload_api_error_contains_no_source_or_upstream_secret(api):
    http, provider, client, store, _, _ = api
    store.search_repository.return_value = [scored_point(repository="other", content="private-api-key\n")]
    response = http.post("/api/retrieval/search", json={"repository": "demo", "query": "退款"})
    assert response.status_code == 502
    assert "private-api-key" not in response.text
    assert provider.closed == 1
    client.close.assert_awaited_once()


def test_missing_embedding_config_opens_no_database(api):
    http, _, _, _, factory, client_factory = api
    factory.side_effect = EmbeddingConfigurationError("EMBEDDING_MODEL is required.")
    assert http.post("/api/retrieval/search", json={"repository": "demo", "query": "退款"}).status_code == 503
    client_factory.assert_not_called()


def test_database_client_construction_failure_still_closes_provider(api):
    http, provider, _, _, _, client_factory = api
    client_factory.side_effect = QdrantUnavailableError("safe connection failure")
    assert http.post("/api/retrieval/search", json={"repository": "demo", "query": "退款"}).status_code == 503
    assert provider.closed == 1


AST_METADATA = dict(symbol="RefundService.refund", node_type="method_declaration",
                    parent_symbol="RefundService", chunking_strategy="ast", chunking_version="ast-v1")
TEXT_METADATA = dict(symbol=None, node_type=None, parent_symbol=None,
                     chunking_strategy="fallback", chunking_version="ast-fallback-line-v1")


def test_mixed_legacy_ast_module_and_fallback_payloads_roundtrip_read_only(settings):
    async def scenario():
        client = AsyncQdrantClient(":memory:")
        store = QdrantVectorStore(client, "test_code")
        await store.prepare_index(3, embedding_identity(settings, "test-embedding"))
        metadatas = [{}, AST_METADATA, {**AST_METADATA, "symbol": None,
                    "parent_symbol": None, "node_type": "module"}, TEXT_METADATA]
        points = []
        for index, metadata in enumerate(metadatas):
            data = {**payload(path=f"service/{index}.py"), **metadata}
            points.append(models.PointStruct(id=data["chunk_id"], vector=[1., index * .2, 0.], payload=data))
        await store.upsert_points(points)
        before = await client.scroll("test_code", with_payload=True, with_vectors=True)
        result = await RetrievalService(FakeEmbedding(), store, settings).search(
            RetrievalSearchRequest(repository="demo", query="退款", top_k=5))
        assert [item.chunk_id for item in result.results] == [p.id for p in points]
        assert [item.chunking_strategy for item in result.results] == ["simple", "ast", "ast", "fallback"]
        assert result.results[0].chunking_version == "line-window-v1"
        assert result.results[0].symbol is None
        for item, expected in zip(result.results[1:], metadatas[1:]):
            assert all(getattr(item, key) == value for key, value in expected.items())
        assert await client.scroll("test_code", with_payload=True, with_vectors=True) == before
        await client.close()
    run(scenario())


@pytest.mark.parametrize("change", [
    {"symbol": ""}, {"symbol": "x\nprivate-api-key"}, {"symbol": "x" * 4097},
    {"symbol": 1}, {"node_type": "method declaration"}, {"node_type": ""},
    {"node_type": None}, {"parent_symbol": "Foreign"}, {"parent_symbol": 1},
    {"symbol": None}, {"chunking_strategy": "hybrid"}, {"chunking_strategy": True},
    {"chunking_strategy": "fallback"}, {"chunking_strategy": "simple"},
    {"chunking_version": "line-window-v1"}, {"chunking_version": None},
])
def test_invalid_ast_metadata_returns_no_partial_source(settings, change):
    store = AsyncMock(spec=QdrantVectorStore)
    store.collection = "test_code"
    store.verify_search_collection.return_value = SimpleNamespace(dimension=3)
    store.search_repository.return_value = [scored_point(), scored_point(**{**AST_METADATA, **change})]
    with pytest.raises(RetrievalResultError) as error:
        run(RetrievalService(FakeEmbedding(), store, settings).search(
            RetrievalSearchRequest(repository="demo", query="退款")))
    assert "private-api-key" not in str(error.value) and "refund()" not in str(error.value)


@pytest.mark.parametrize("missing", list(AST_METADATA))
def test_partial_stage3_metadata_is_not_mislabeled_as_legacy(settings, missing):
    metadata = {key: value for key, value in AST_METADATA.items() if key != missing}
    store = AsyncMock(spec=QdrantVectorStore)
    store.collection = "test_code"
    store.verify_search_collection.return_value = SimpleNamespace(dimension=3)
    store.search_repository.return_value = [scored_point(**metadata)]
    with pytest.raises(RetrievalResultError):
        run(RetrievalService(FakeEmbedding(), store, settings).search(
            RetrievalSearchRequest(repository="demo", query="退款")))


def test_api_projects_ast_and_fallback_metadata_and_safe_partial_failure(api):
    http, _, _, store, _, _ = api
    store.search_repository.return_value = [scored_point(**AST_METADATA), scored_point(**TEXT_METADATA)]
    result = http.post("/api/retrieval/search", json={"repository": "demo", "query": "退款"})
    assert result.status_code == 200
    for item, expected in zip(result.json()["results"], [AST_METADATA, TEXT_METADATA]):
        assert all(item[key] == value for key, value in expected.items())
    point = scored_point(**AST_METADATA)
    del point.payload["node_type"]
    store.search_repository.return_value = [scored_point(), point]
    result = http.post("/api/retrieval/search", json={"repository": "demo", "query": "退款"})
    assert result.status_code == 502
    assert "refund()" not in result.text and "results" not in result.json()
