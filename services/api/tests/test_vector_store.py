import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from qdrant_client import AsyncQdrantClient, models
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse

from app.core.config import Settings
from app.core.exceptions import QdrantCollectionConflictError, QdrantUnavailableError
from app.main import app
from app.retrieval.vector_store import (
    QdrantVectorStore,
    create_qdrant_client,
    get_vector_store,
)


def collection_info(dimension: int = 3) -> SimpleNamespace:
    return SimpleNamespace(
        config=SimpleNamespace(params=SimpleNamespace(
            vectors=models.VectorParams(size=dimension, distance=models.Distance.COSINE)
        )),
        points_count=0,
        status=models.CollectionStatus.GREEN,
    )


def upstream_error(status: int, content: bytes = b"upstream detail") -> UnexpectedResponse:
    return UnexpectedResponse(status_code=status, reason_phrase="failure", content=content, headers={})


@pytest.mark.parametrize("url", [
    "", ":memory:", "file:///tmp/db", "ftp://localhost:6333",
    "http://user:password@localhost:6333", "http://localhost:6333?key=secret",
    "http://localhost:6333#fragment", "http://localhost:99999",
])
def test_qdrant_url_requires_http_without_embedded_credentials(url: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, qdrant_url=url)


def test_qdrant_settings_redact_api_key_and_normalize_url() -> None:
    settings = Settings(
        _env_file=None, qdrant_url=" http://localhost:6333/ ",
        qdrant_api_key="qdrant-test-secret",
    )
    assert settings.qdrant_url == "http://localhost:6333"
    assert "qdrant-test-secret" not in repr(settings)


@pytest.mark.parametrize("collection", ["", "../other", "with space", "a" * 129])
def test_collection_name_validation(collection: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, qdrant_collection=collection)


@pytest.mark.parametrize("timeout", [0, -1, 61])
def test_qdrant_timeout_is_bounded(timeout: float) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, qdrant_timeout_seconds=timeout)


@pytest.mark.parametrize("url,loopback", [
    ("http://127.0.0.1:6333", True),
    ("https://qdrant.example.test", False),
])
def test_client_uses_settings_and_does_not_probe_during_construction(
    url: str, loopback: bool
) -> None:
    settings = Settings(_env_file=None, qdrant_url=url, qdrant_api_key="", qdrant_timeout_seconds=7)
    with patch("app.retrieval.vector_store.AsyncQdrantClient") as factory:
        create_qdrant_client(settings)
    options = factory.call_args.kwargs
    assert options["url"] == url
    assert options["api_key"] is None
    assert options["timeout"] == 7
    assert options["check_compatibility"] is False
    assert options.get("trust_env") is (False if loopback else None)


def test_client_receives_configured_key_without_exposing_it() -> None:
    settings = Settings(_env_file=None, qdrant_api_key="fake-qdrant-key")
    with patch("app.retrieval.vector_store.AsyncQdrantClient") as factory:
        create_qdrant_client(settings)
    assert factory.call_args.kwargs["api_key"] == "fake-qdrant-key"


def test_collection_creation_is_idempotent_and_never_recreates_on_mismatch() -> None:
    async def exercise() -> None:
        client = AsyncQdrantClient(location=":memory:")
        store = QdrantVectorStore(client, "test_code")
        try:
            first = await store.ensure_collection(3)
            second = await store.ensure_collection(3)
            assert first.created is True
            assert second.created is False
            assert first.dimension == 3
            assert first.distance == "Cosine"
            assert first.points_count == 0
            with pytest.raises(QdrantCollectionConflictError, match="requested 4"):
                await store.ensure_collection(4)
            assert (await store.collection_info()).dimension == 3
        finally:
            await client.close()
    asyncio.run(exercise())


@pytest.mark.parametrize("vectors", [
    models.VectorParams(size=3, distance=models.Distance.DOT),
    {"named": models.VectorParams(size=3, distance=models.Distance.COSINE)},
])
def test_rejects_incompatible_distance_or_named_vector_collection(vectors: object) -> None:
    async def exercise() -> None:
        client = AsyncQdrantClient(location=":memory:")
        try:
            await client.create_collection("test_code", vectors_config=vectors)
            with pytest.raises(QdrantCollectionConflictError):
                await QdrantVectorStore(client, "test_code").ensure_collection(3)
        finally:
            await client.close()
    asyncio.run(exercise())


def test_concurrent_creation_checks_existing_collection_after_conflict() -> None:
    client = AsyncMock()
    client.collection_exists.return_value = False
    client.create_collection.side_effect = upstream_error(409)
    client.get_collection.return_value = collection_info()
    result = asyncio.run(QdrantVectorStore(client, "test_code").ensure_collection(3))
    assert result.created is False
    assert result.dimension == 3


def test_failed_creation_is_not_reported_as_success() -> None:
    client = AsyncMock()
    client.collection_exists.return_value = False
    client.create_collection.return_value = False
    with pytest.raises(QdrantUnavailableError, match="did not confirm"):
        asyncio.run(QdrantVectorStore(client, "test_code").ensure_collection(3))


@pytest.fixture
def api_store():
    client = AsyncMock()
    client.get_collections.return_value = models.CollectionsResponse(
        collections=[models.CollectionDescription(name="test_code")]
    )
    client.get_collection.return_value = collection_info()
    client.collection_exists.return_value = False
    client.create_collection.return_value = True
    store = QdrantVectorStore(client, "test_code")
    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_vector_store] = lambda: store
    try:
        with TestClient(app) as http:
            yield http, client
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


def test_health_checks_authenticated_qdrant_access_and_collection(api_store) -> None:
    http, client = api_store
    response = http.get("/health/qdrant")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "collection": "test_code", "collection_exists": True}
    client.get_collections.assert_awaited_once()


def test_connected_health_does_not_require_collection_to_exist(api_store) -> None:
    http, client = api_store
    client.get_collections.return_value = models.CollectionsResponse(collections=[])
    response = http.get("/health/qdrant")
    assert response.status_code == 200
    assert response.json()["collection_exists"] is False


@pytest.mark.parametrize("failure", [
    ResponseHandlingException(ConnectionError("private-api-key")),
    upstream_error(401, b"private-api-key"),
])
def test_unavailable_health_is_safe_503_and_does_not_break_process_health(api_store, failure) -> None:
    http, client = api_store
    client.get_collections.side_effect = failure
    response = http.get("/health/qdrant")
    assert response.status_code == 503
    assert "private-api-key" not in response.text
    assert http.get("/health").json() == {"status": "ok"}


def test_collection_api_creates_and_returns_real_metadata(api_store) -> None:
    http, client = api_store
    response = http.post("/api/vector-store/collection", json={"dimension": 3})
    assert response.status_code == 200
    assert response.json() == {
        "collection": "test_code", "dimension": 3, "distance": "Cosine",
        "points_count": 0, "status": "green", "created": True,
    }
    assert http.get("/api/vector-store/collection").json()["dimension"] == 3
    params = client.create_collection.call_args.kwargs["vectors_config"]
    assert params.size == 3
    assert params.distance == models.Distance.COSINE


@pytest.mark.parametrize("body", [
    {}, {"dimension": 0}, {"dimension": -1}, {"dimension": 65_537},
    {"dimension": True}, {"dimension": "3"}, {"dimension": 3.5},
    {"dimension": 3, "collection": "arbitrary"},
])
def test_invalid_dimension_or_extra_fields_never_call_qdrant(api_store, body) -> None:
    http, client = api_store
    assert http.post("/api/vector-store/collection", json=body).status_code == 422
    client.collection_exists.assert_not_awaited()
    client.create_collection.assert_not_awaited()


def test_api_dimension_conflict_returns_409(api_store) -> None:
    http, client = api_store
    client.collection_exists.return_value = True
    response = http.post("/api/vector-store/collection", json={"dimension": 4})
    assert response.status_code == 409
    client.create_collection.assert_not_awaited()


@pytest.mark.parametrize("upstream_status,expected", [(404, 404), (401, 503), (500, 503)])
def test_collection_info_maps_missing_or_unavailable_errors(api_store, upstream_status, expected) -> None:
    http, client = api_store
    client.get_collection.side_effect = upstream_error(upstream_status, b"private-api-key")
    response = http.get("/api/vector-store/collection")
    assert response.status_code == expected
    assert "private-api-key" not in response.text


def test_request_dependency_closes_client_after_failure() -> None:
    client = AsyncMock()
    client.get_collections.side_effect = ResponseHandlingException(ConnectionError("offline"))
    settings = Settings(_env_file=None, qdrant_collection="test_code")
    with (
        patch("app.retrieval.vector_store.create_qdrant_client", return_value=client),
        patch("app.retrieval.vector_store.get_settings", return_value=settings),
        TestClient(app) as http,
    ):
        assert http.get("/health/qdrant").status_code == 503
    client.close.assert_awaited_once()
