import asyncio
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from qdrant_client import AsyncQdrantClient, models
from qdrant_client.http.exceptions import ResponseHandlingException

from app.core.config import Settings
from app.core.exceptions import (
    ChunkingLimitError, EmbeddingConfigurationError, EmbeddingProviderError,
    IndexingBusyError, IndexingLimitError, QdrantCollectionConflictError,
    QdrantUnavailableError,
)
from app.embedding.base import EmbeddingProvider
from app.indexing.chunker import SimpleCodeChunker
from app.indexing.service import (
    RepositoryIndexer, embedding_identity, indexing_guard, prepare_repository,
)
from app.main import app
from app.repository.scanner import RepositoryScanner
from app.retrieval.vector_store import QdrantVectorStore


class FakeEmbedding(EmbeddingProvider):
    def __init__(self, *, model="test-embedding", dimension=3):
        self._model = model
        self.dimension = dimension
        self.calls = []
        self.closed = 0

    @property
    def model_name(self):
        return self._model

    async def embed_texts(self, texts):
        self.calls.append(list(texts))
        return [[float(i + 1) for i in range(self.dimension)] for _ in texts]

    async def aclose(self):
        self.closed += 1


@pytest.fixture
def settings():
    return Settings(
        _env_file=None, embedding_model="test-embedding", embedding_api_key="private-key",
        embedding_base_url="http://localhost:11434/v1", embedding_batch_size=2,
        qdrant_collection="test_code", chunk_lines=3, chunk_overlap_lines=1,
    )


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "workspaces"
    repo = root / "demo"
    repo.mkdir(parents=True)
    (repo / "main.py").write_text("first\nsecond\nthird\nfourth\nfifth\nsixth\n", encoding="utf-8")
    (repo / "other.md").write_text("hello\n", encoding="utf-8")
    (repo / "blank.py").write_text("  \n", encoding="utf-8")
    (repo / ".env").write_text("SECRET=value", encoding="utf-8")
    return root


def prepare(workspace, repository="demo", **options):
    return prepare_repository(
        repository, RepositoryScanner(workspace),
        SimpleCodeChunker(chunk_lines=3, overlap_lines=1, **options), 10000,
    )


def run(coroutine):
    return asyncio.run(coroutine)


def test_real_in_memory_points_have_vectors_exact_payload_and_source_ranges(workspace, settings, caplog):
    async def scenario():
        client = AsyncQdrantClient(":memory:")
        store = QdrantVectorStore(client, settings.qdrant_collection)
        provider = FakeEmbedding()
        prepared = prepare(workspace)
        with caplog.at_level("INFO"):
            response = await RepositoryIndexer(provider, store, settings).index(prepared)
        assert response.files_scanned == 4
        assert response.supported_files == 3
        assert response.skipped_files == 1
        assert response.chunks_created == response.vectors_indexed == 4
        assert response.previous_vectors == 0
        assert response.embedding_dimension == 3
        assert [len(call) for call in provider.calls] == [2, 2]
        assert [text for call in provider.calls for text in call] == [c.content for c in prepared.chunks]
        points, _ = await client.scroll(settings.qdrant_collection, limit=20, with_vectors=True)
        assert {str(point.id) for point in points} == {chunk.chunk_id for chunk in prepared.chunks}
        by_id = {str(point.id): point for point in points}
        for chunk in prepared.chunks:
            point = by_id[chunk.chunk_id]
            assert len(point.vector) == 3
            for field in ("repository", "path", "language", "start_line", "end_line", "content"):
                assert point.payload[field] == getattr(chunk, field)
            assert point.payload["embedding_model"] == "test-embedding"
        info = await client.get_collection(settings.qdrant_collection)
        assert info.config.metadata["repopilot_index"] == embedding_identity(settings, provider.model_name)
        assert "private-key" not in str(info.config.metadata)
        await client.close()
    run(scenario())
    assert "files_scanned=4" in caplog.text
    assert "chunks_created=4" in caplog.text
    assert "vectors_indexed=4" in caplog.text
    assert "embedding_dimension=3" in caplog.text
    assert "private-key" not in caplog.text
    assert "first\nsecond" not in caplog.text


def test_repeat_and_edit_remove_stale_points_and_preserve_other_repository(workspace, settings):
    async def scenario():
        client = AsyncQdrantClient(":memory:")
        store = QdrantVectorStore(client, settings.qdrant_collection)
        indexer = RepositoryIndexer(FakeEmbedding(), store, settings)
        prepared = prepare(workspace)
        await indexer.index(prepared)
        foreign = str(uuid4())
        await store.upsert_points([models.PointStruct(id=foreign, vector=[1., 2., 3.], payload={"repository": "other"})])
        repeated = await indexer.index(prepared)
        assert repeated.previous_vectors == repeated.vectors_indexed == 4
        (workspace / "demo" / "main.py").write_text("changed\n", encoding="utf-8")
        (workspace / "demo" / "other.md").unlink()
        edited = prepare(workspace)
        response = await indexer.index(edited)
        assert response.previous_vectors == 4 and response.vectors_indexed == 1
        points, _ = await client.scroll(settings.qdrant_collection, limit=20)
        assert {str(point.id) for point in points} == {foreign, edited.chunks[0].chunk_id}
        assert await store.count_repository("other") == 1
        (workspace / "demo" / "main.py").write_text("\n", encoding="utf-8")
        empty = await indexer.index(prepare(workspace))
        assert empty.previous_vectors == 1 and empty.vectors_indexed == empty.chunks_created == 0
        assert empty.embedding_dimension == 3
        assert await store.count_repository("other") == 1
        await client.close()
    run(scenario())


def test_empty_repository_does_not_embed_or_create_collection(workspace, settings):
    for path in (workspace / "demo").iterdir():
        path.unlink()
    async def scenario():
        client = AsyncQdrantClient(":memory:")
        provider = FakeEmbedding()
        result = await RepositoryIndexer(provider, QdrantVectorStore(client, "test_code"), settings).index(prepare(workspace))
        assert result.vectors_indexed == result.chunks_created == result.previous_vectors == 0
        assert result.embedding_dimension is None
        assert provider.calls == []
        assert not await client.collection_exists("test_code")
        await client.close()
    run(scenario())


@pytest.mark.parametrize("failure", ["upstream", "count", "dimension", "nan", "zero"])
def test_all_embedding_batches_validated_before_old_points_are_changed(workspace, settings, failure):
    async def scenario():
        client = AsyncQdrantClient(":memory:")
        store = QdrantVectorStore(client, "test_code")
        prepared = prepare(workspace)
        await RepositoryIndexer(FakeEmbedding(), store, settings).index(prepared)
        bad = FakeEmbedding()
        if failure == "upstream":
            error = EmbeddingProviderError("The embedding service is unavailable.")
            bad.embed_texts = AsyncMock(side_effect=[[[1., 2., 3.]] * 2, error])
        else:
            output = {
                "count": [[1., 2., 3.]], "dimension": [[1., 2.]] * 2,
                "nan": [[float("nan"), 1., 2.]] * 2, "zero": [[0., 0., 0.]] * 2,
            }[failure]
            bad.embed_texts = AsyncMock(side_effect=[[[1., 2., 3.]] * 2, output])
        with pytest.raises(EmbeddingProviderError):
            await RepositoryIndexer(bad, store, settings).index(prepared)
        assert await store.count_repository("demo") == 4
        points, _ = await client.scroll("test_code", limit=20)
        assert {str(p.id) for p in points} == {c.chunk_id for c in prepared.chunks}
        await client.close()
    run(scenario())


@pytest.mark.parametrize("change", ["dimension", "model", "endpoint", "schema"])
def test_collection_compatibility_conflicts_preserve_points(workspace, settings, change):
    async def scenario():
        client = AsyncQdrantClient(":memory:")
        store = QdrantVectorStore(client, "test_code")
        prepared = prepare(workspace)
        await RepositoryIndexer(FakeEmbedding(), store, settings).index(prepared)
        provider, changed = FakeEmbedding(), settings
        if change == "dimension":
            provider.dimension = 4
        elif change == "model":
            provider._model = "different-model-same-dimension"
        elif change == "endpoint":
            changed = settings.model_copy(update={"embedding_base_url": "http://localhost:9999/v1"})
        else:
            metadata = {"repopilot_index": {"schema_version": "unknown"}}
            await client.update_collection("test_code", metadata=metadata)
        with pytest.raises(QdrantCollectionConflictError):
            await RepositoryIndexer(provider, store, changed).index(prepared)
        assert await store.count_repository("demo") == 4
        await client.close()
    run(scenario())


def test_nonempty_unowned_collection_is_not_adopted(workspace, settings):
    async def scenario():
        client = AsyncQdrantClient(":memory:")
        store = QdrantVectorStore(client, "test_code")
        await store.ensure_collection(3)
        old_id = str(uuid4())
        await store.upsert_points([models.PointStruct(id=old_id, vector=[1., 2., 3.], payload={"repository": "demo"})])
        with pytest.raises(QdrantCollectionConflictError, match="cannot be adopted"):
            await RepositoryIndexer(FakeEmbedding(), store, settings).index(prepare(workspace))
        assert len(await client.retrieve("test_code", [old_id])) == 1
        await client.close()
    run(scenario())


def test_adopting_empty_collection_preserves_unrelated_metadata(settings):
    async def scenario():
        client = AsyncQdrantClient(":memory:")
        await client.create_collection("test_code", vectors_config=models.VectorParams(size=3, distance=models.Distance.COSINE), metadata={"owner": "test"})
        store = QdrantVectorStore(client, "test_code")
        assert await store.prepare_index(3, embedding_identity(settings, "test-embedding")) == 3
        assert (await client.get_collection("test_code")).config.metadata["owner"] == "test"
        await client.close()
    run(scenario())


def test_vector_memory_limit_precedes_any_collection_write(workspace, settings):
    store = AsyncMock(spec=QdrantVectorStore)
    constrained = settings.model_copy(update={"indexing_max_vector_values": 11})
    with pytest.raises(IndexingLimitError):
        run(RepositoryIndexer(FakeEmbedding(), store, constrained).index(prepare(workspace)))
    store.prepare_index.assert_not_awaited()
    store.delete_repository.assert_not_awaited()


def test_chunk_text_memory_limit_and_whole_repository_count(workspace):
    with pytest.raises(IndexingLimitError):
        prepare_repository("demo", RepositoryScanner(workspace), SimpleCodeChunker(chunk_lines=3, overlap_lines=1), 1)
    with pytest.raises(ChunkingLimitError):
        prepare(workspace, max_chunks=3)


def test_all_chunks_must_fit_embedding_limit_even_with_larger_chunk_configuration(workspace):
    (workspace / "demo" / "main.py").write_text("a" * 8193, encoding="utf-8")
    with pytest.raises(ChunkingLimitError, match="embedding text"):
        prepare(workspace, max_chunk_characters=10000)


@pytest.mark.parametrize("operation", ["delete", "upsert", "final_count"])
def test_write_failure_is_safe_partial_error_and_never_success(workspace, settings, operation):
    store = AsyncMock(spec=QdrantVectorStore)
    store.collection = "test_code"
    store.prepare_index.return_value = 3
    store.count_repository.side_effect = [4, 1] if operation == "final_count" else [4, 4]
    if operation != "final_count":
        getattr(store, "delete_repository" if operation == "delete" else "upsert_points").side_effect = QdrantUnavailableError("private-upstream-body")
    with pytest.raises(QdrantUnavailableError, match="may be partial") as exc:
        run(RepositoryIndexer(FakeEmbedding(), store, settings).index(prepare(workspace)))
    assert "private-upstream-body" not in str(exc.value)


@pytest.mark.parametrize("operation", ["delete", "upsert"])
@pytest.mark.parametrize("failure", ["network", "acknowledged"])
def test_store_requires_completed_wait_and_hides_upstream(operation, failure):
    client = AsyncMock()
    method = getattr(client, operation)
    if failure == "network":
        method.side_effect = ResponseHandlingException(ConnectionError("private-key"))
    else:
        method.return_value = models.UpdateResult(status=models.UpdateStatus.ACKNOWLEDGED, operation_id=1)
    store = QdrantVectorStore(client, "test_code")
    with pytest.raises(QdrantUnavailableError) as exc:
        run(store.delete_repository("demo") if operation == "delete" else store.upsert_points([
            models.PointStruct(id=str(uuid4()), vector=[1., 2., 3.], payload={"repository": "demo"}),
        ]))
    assert "private-key" not in str(exc.value)
    assert method.call_args.kwargs["wait"] is True
    if operation == "delete":
        assert method.call_args.kwargs["points_selector"].filter.must[0].match.value == "demo"


def test_guard_rejects_parallel_index_and_releases_after_exception():
    with pytest.raises(RuntimeError):
        with indexing_guard():
            with pytest.raises(IndexingBusyError):
                with indexing_guard():
                    pytest.fail("Must not acquire twice")
            raise RuntimeError("test")
    with indexing_guard():
        pass


@pytest.fixture
def api(workspace, settings):
    provider = FakeEmbedding()
    client = AsyncMock()
    store = AsyncMock(spec=QdrantVectorStore)
    store.collection = "test_code"
    store.prepare_index.return_value = 3
    store.count_repository.side_effect = [0, 4]
    with (
        patch("app.api.indexing.get_settings", return_value=settings),
        patch("app.api.indexing.create_repository_scanner", return_value=RepositoryScanner(workspace)),
        patch("app.api.indexing.create_ast_code_chunker", return_value=SimpleCodeChunker(chunk_lines=3, overlap_lines=1)),
        patch("app.api.indexing.EmbeddingProviderFactory.create", return_value=provider) as factory,
        patch("app.api.indexing.create_qdrant_client", return_value=client) as client_factory,
        patch("app.api.indexing.QdrantVectorStore", return_value=store),
        TestClient(app) as http,
    ):
        yield http, provider, client, store, factory, client_factory


def test_index_api_returns_actual_counts_and_closes_clients(api):
    http, provider, client, store, _, _ = api
    response = http.post("/api/indexing/repositories/demo")
    assert response.status_code == 200
    result = response.json()
    assert result["repository"] == "demo"
    assert result["files_scanned"] == 4
    assert result["chunks_created"] == result["vectors_indexed"] == 4
    assert result["collection"] == "test_code"
    assert result["strategy"] == "replace_repository"
    assert result["skipped"] == [{"path": ".env", "reason": "ignored_file"}]
    assert "content" not in result
    assert "private-key" not in response.text
    assert provider.closed == 1
    client.close.assert_awaited_once()


@pytest.mark.parametrize("name", ["CON", "demo..", "bad%5Cpath", "bad%3Apath", "a" * 65])
def test_invalid_api_name_precedes_provider_and_database(api, name):
    http, _, _, _, factory, client_factory = api
    assert http.post(f"/api/indexing/repositories/{name}").status_code == 422
    factory.assert_not_called()
    client_factory.assert_not_called()


def test_missing_repository_precedes_provider_and_database(api):
    http, _, _, _, factory, client_factory = api
    assert http.post("/api/indexing/repositories/missing").status_code == 404
    factory.assert_not_called()
    client_factory.assert_not_called()


@pytest.mark.parametrize("exception,status", [
    (EmbeddingProviderError("safe embedding failure"), 502),
    (QdrantUnavailableError("safe database failure"), 503),
    (QdrantCollectionConflictError("safe conflict"), 409),
    (IndexingLimitError("safe limit"), 413),
])
def test_api_error_status_and_client_cleanup(api, exception, status):
    http, provider, client, store, _, _ = api
    store.prepare_index.side_effect = exception
    assert http.post("/api/indexing/repositories/demo").status_code == status
    store.delete_repository.assert_not_awaited()
    assert provider.closed == 1
    client.close.assert_awaited_once()


def test_missing_embedding_configuration_never_opens_database(api):
    http, _, _, _, factory, client_factory = api
    factory.side_effect = EmbeddingConfigurationError("EMBEDDING_MODEL is required.")
    assert http.post("/api/indexing/repositories/demo").status_code == 503
    client_factory.assert_not_called()


def test_busy_api_does_not_block_health_or_open_clients(api):
    http, _, _, _, factory, client_factory = api
    with indexing_guard():
        assert http.post("/api/indexing/repositories/demo").status_code == 409
        assert http.get("/health").status_code == 200
    factory.assert_not_called()
    client_factory.assert_not_called()


@pytest.mark.parametrize("setting", ["indexing_max_text_characters", "indexing_max_vector_values"])
@pytest.mark.parametrize("value", [0, 20_000_001])
def test_index_resource_settings_are_bounded(setting, value):
    with pytest.raises(ValueError):
        Settings(_env_file=None, **{setting: value})
