import asyncio
from dataclasses import asdict
import logging
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from qdrant_client import AsyncQdrantClient, models

from app.core.config import REPOSITORY_ROOT, Settings
from app.core.exceptions import ChunkingLimitError, EmbeddingProviderError, IndexingLimitError, QdrantCollectionConflictError, QdrantUnavailableError
from app.embedding.base import EmbeddingProvider
from app.embedding.identity import embedding_identity
from app.indexing.ast_chunker import ASTCodeChunker, create_ast_code_chunker
from app.indexing.chunker import SimpleCodeChunker
from app.indexing.service import RepositoryIndexer, prepare_repository
from app.main import app
from app.repository.paths import read_bounded_file
from app.repository.scanner import RepositoryScanner
from app.retrieval.service import RetrievalService
from app.retrieval.vector_store import QdrantVectorStore
from app.schemas.retrieval import RetrievalSearchRequest


class TestEmbedding(EmbeddingProvider):
    __test__ = False
    model_name = "test-embedding"

    def __init__(self):
        self.calls = []
        self.closed = 0

    async def embed_texts(self, texts):
        self.calls.append(list(texts))
        return [[1., 2., 3.] for _ in texts]

    async def aclose(self):
        self.closed += 1


@pytest.fixture
def settings():
    return Settings(_env_file=None, embedding_model="test-embedding", embedding_api_key="PRIVATE_KEY",
                    embedding_base_url="http://localhost:11434/v1", qdrant_collection="test_ast_code",
                    embedding_batch_size=2)


@pytest.fixture
def workspace(tmp_path):
    repo = tmp_path / "demo"
    repo.mkdir()
    (repo / "Wallet.java").write_text("class Wallet {\n  int balance;\n  void credit() {}\n  int get() { return balance; }\n}\n", encoding="utf-8")
    (repo / "broken.py").write_text("def broken(:\n", encoding="utf-8")
    (repo / "panel.vue").write_text("<template>refund</template>\n", encoding="utf-8")
    return tmp_path


def prepared(workspace, **limits):
    return prepare_repository("demo", RepositoryScanner(workspace), ASTCodeChunker(**limits), 2_000_000)


@pytest.mark.parametrize("filename", [
    "credentials.json", "CREDENTIALS.YAML", "secrets.yml", "Secrets.JSON",
    ".credentials.json", ".secrets.yaml", "credentials", "secrets",
])
def test_sensitive_basename_is_skipped_before_opening_or_chunking(tmp_path, filename):
    repo = tmp_path / "demo"
    repo.mkdir()
    secret = repo / filename
    secret.write_text("DO_NOT_READ_PRIVATE", encoding="utf-8")
    allowed = repo / "credentials_reader.py"
    allowed.write_text("def read_public():\n    return 1\n", encoding="utf-8")
    with patch("app.repository.scanner.read_bounded_file", wraps=read_bounded_file) as reader:
        scan = RepositoryScanner(tmp_path).scan("demo")
    assert [f.path for f in scan.files] == ["credentials_reader.py"]
    assert scan.skipped[0].path == filename and scan.skipped[0].reason == "ignored_file"
    assert [call.args[0] for call in reader.call_args_list] == [allowed]


@pytest.mark.parametrize("directory", ["Credentials", "SECRETS", ".Credentials", ".Secrets"])
def test_sensitive_directory_is_pruned_without_reading_descendants(tmp_path, directory):
    repo = tmp_path / "demo"
    (repo / directory).mkdir(parents=True)
    (repo / directory / "config.json").write_text('{"DO_NOT_READ":"PRIVATE"}', encoding="utf-8")
    with patch("app.repository.scanner.read_bounded_file") as reader:
        scan = RepositoryScanner(tmp_path).scan("demo")
    assert scan.files == [] and scan.pruned_directories[0].reason == "ignored_directory"
    reader.assert_not_called()


def test_real_demo_preparation_reports_ast_fallback_and_exact_chunk_count(caplog):
    with caplog.at_level(logging.INFO):
        data = prepare_repository("demo-repository", RepositoryScanner(REPOSITORY_ROOT / "workspaces"), ASTCodeChunker(), 2_000_000)
    assert (data.scan.files_scanned, len(data.scan.files), data.ast_parsed_files, data.fallback_files, len(data.chunks)) == (10, 8, 4, 4, 17)
    assert data.chunking_metadata() == {"chunking_version": "ast-v1", "fallback_chunking_version": "ast-fallback-line-v1", "chunk_lines": 60, "overlap_lines": 10, "max_chunk_characters": 8192}
    assert "ast_parsed_files=4 fallback_files=4 failed_files=0 chunks_created=17" in caplog.text
    assert "wallets.credit" not in caplog.text


def test_factory_uses_configured_limits_and_caps_embedding_text_budget(settings):
    configured = settings.model_copy(update={"chunk_lines": 25, "chunk_overlap_lines": 5,
                                           "chunk_max_characters": 65_536, "chunk_max_chunks": 99})
    with patch("app.indexing.ast_chunker.get_settings", return_value=configured):
        chunker = create_ast_code_chunker()
    assert (chunker.chunk_lines, chunker.overlap_lines, chunker.max_chunks, chunker.max_chunk_characters) == (25, 5, 99, 8192)


def test_ast_payload_and_collection_profile_use_actual_chunker_settings(workspace, settings):
    async def scenario():
        client = AsyncQdrantClient(":memory:")
        try:
            store = QdrantVectorStore(client, "test_ast_code")
            data = prepared(workspace, chunk_lines=7, overlap_lines=1, max_chunk_characters=300)
            result = await RepositoryIndexer(TestEmbedding(), store, settings).index(data)
            assert (result.ast_parsed_files, result.fallback_files, result.failed_files, result.chunks_created) == (1, 2, 0, 5)
            assert result.chunking_version == "ast-v1" and result.chunk_lines == 7 and result.overlap_lines == 1
            points, _ = await client.scroll("test_ast_code", limit=100, with_vectors=True)
            expected = {chunk.chunk_id: chunk for chunk in data.chunks}
            assert {str(point.id) for point in points} == set(expected)
            for point in points:
                chunk = expected[str(point.id)]
                assert all(point.payload[key] == value for key, value in asdict(chunk).items())
                assert point.payload["chunk_lines"] == 7 and point.payload["max_chunk_characters"] == 300
                assert point.payload["overlap_lines"] == (0 if chunk.chunking_strategy == "ast" else 1)
                assert len(point.vector) == result.embedding_dimension == 3
            metadata = (await client.get_collection("test_ast_code")).config.metadata
            assert metadata["repopilot_index"] == embedding_identity(settings, "test-embedding")
            assert metadata["repopilot_chunking"]["demo"] == data.chunking_metadata()
        finally:
            await client.close()
    asyncio.run(scenario())


def test_upgrade_replaces_simple_points_and_preserves_other_repository_and_metadata(workspace, settings):
    async def scenario():
        client = AsyncQdrantClient(":memory:")
        try:
            store = QdrantVectorStore(client, "test_ast_code")
            indexer = RepositoryIndexer(TestEmbedding(), store, settings)
            baseline = prepare_repository("demo", RepositoryScanner(workspace), SimpleCodeChunker(), 2_000_000)
            old = await indexer.index(baseline)
            old_ids = {c.chunk_id for c in baseline.chunks}
            foreign = str(uuid4())
            await store.upsert_points([models.PointStruct(id=foreign, vector=[1., 2., 3.], payload={"repository": "other", "marker": "KEEP"})])
            await store.record_repository_chunking("other", {"chunking_version": "line-window-v1"})
            info = await client.get_collection("test_ast_code")
            await client.update_collection("test_ast_code", metadata={**info.config.metadata, "owner": "KEEP_OWNER"})
            ast = prepared(workspace)
            first = await indexer.index(ast)
            assert first.previous_vectors == old.vectors_indexed == 3 and first.vectors_indexed == 5
            points, _ = await client.scroll("test_ast_code", limit=100)
            assert {str(p.id) for p in points} == {c.chunk_id for c in ast.chunks} | {foreign}
            assert old_ids.isdisjoint({str(p.id) for p in points})
            repeated = await indexer.index(prepared(workspace))
            assert repeated.previous_vectors == repeated.vectors_indexed == 5
            metadata = (await client.get_collection("test_ast_code")).config.metadata
            assert metadata["owner"] == "KEEP_OWNER"
            assert metadata["repopilot_chunking"]["other"] == {"chunking_version": "line-window-v1"}
            assert metadata["repopilot_chunking"]["demo"]["chunking_version"] == "ast-v1"
            (workspace / "demo" / "Wallet.java").write_text("class Wallet {\n  int get() { return 2; }\n}\n", encoding="utf-8")
            edited = prepared(workspace)
            result = await indexer.index(edited)
            assert result.previous_vectors == 5 and result.vectors_indexed == 4
            points, _ = await client.scroll("test_ast_code", limit=100)
            assert {str(p.id) for p in points} == {c.chunk_id for c in edited.chunks} | {foreign}
            for file in (workspace / "demo").iterdir():
                file.unlink()
            empty = await indexer.index(prepared(workspace))
            assert empty.previous_vectors == 4 and empty.vectors_indexed == 0
            assert await store.count_repository("other") == 1
        finally:
            await client.close()
    asyncio.run(scenario())


def test_ast_index_keeps_existing_dense_retrieval_contract(workspace, settings):
    async def scenario():
        client = AsyncQdrantClient(":memory:")
        try:
            store = QdrantVectorStore(client, "test_ast_code")
            provider = TestEmbedding()
            await RepositoryIndexer(provider, store, settings).index(prepared(workspace))
            result = await RetrievalService(provider, store, settings).search(
                RetrievalSearchRequest(repository="demo", query="credit", top_k=5),
            )
            assert len(result.results) == 5 and all(hit.repository == "demo" for hit in result.results)
            assert all(hit.content and hit.start_line <= hit.end_line for hit in result.results)
        finally:
            await client.close()
    asyncio.run(scenario())


def test_later_embedding_failure_keeps_old_points_and_profile(workspace, settings):
    async def scenario():
        client = AsyncQdrantClient(":memory:")
        try:
            store = QdrantVectorStore(client, "test_ast_code")
            old = prepare_repository("demo", RepositoryScanner(workspace), SimpleCodeChunker(), 2_000_000)
            await RepositoryIndexer(TestEmbedding(), store, settings).index(old)
            before = (await client.get_collection("test_ast_code")).config.metadata
            bad = TestEmbedding()
            bad.embed_texts = AsyncMock(side_effect=[[[1., 2., 3.]] * 2, EmbeddingProviderError("safe failure")])
            with pytest.raises(EmbeddingProviderError):
                await RepositoryIndexer(bad, store, settings).index(prepared(workspace))
            points, _ = await client.scroll("test_ast_code", limit=100)
            assert {str(p.id) for p in points} == {c.chunk_id for c in old.chunks}
            assert (await client.get_collection("test_ast_code")).config.metadata == before
        finally:
            await client.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["metadata_write", "final_count"])
def test_unconfirmed_index_does_not_return_success_or_commit_profile(workspace, settings, phase):
    store = AsyncMock(spec=QdrantVectorStore)
    store.collection = "test_ast_code"
    store.prepare_index.return_value = 3
    store.count_repository.side_effect = [3, 1 if phase == "final_count" else 5]
    if phase == "metadata_write":
        store.record_repository_chunking.side_effect = QdrantUnavailableError("DO_NOT_EXPOSE_UPSTREAM")
    with pytest.raises(QdrantUnavailableError, match="may be partial") as error:
        asyncio.run(RepositoryIndexer(TestEmbedding(), store, settings).index(prepared(workspace)))
    assert "DO_NOT_EXPOSE_UPSTREAM" not in str(error.value)
    if phase == "final_count":
        store.record_repository_chunking.assert_not_awaited()


@pytest.mark.parametrize("failure", ["unconfirmed", "mismatch"])
def test_metadata_commit_requires_acknowledgement_and_readback(failure):
    metadata = {"repopilot_index": {"model": "KEEP"}, "owner": "KEEP", "repopilot_chunking": {"other": {"chunking_version": "simple"}}}
    info = models.CollectionInfo.model_construct(config=models.CollectionConfig.model_construct(metadata=metadata))
    client = AsyncMock()
    client.update_collection.return_value = failure != "unconfirmed"
    client.get_collection.return_value = info
    store = QdrantVectorStore(client, "test_ast_code")
    with pytest.raises(QdrantUnavailableError):
        asyncio.run(store.record_repository_chunking("demo", {"chunking_version": "ast-v1"}))
    sent = client.update_collection.call_args.kwargs["metadata"]
    assert sent["owner"] == "KEEP" and sent["repopilot_index"] == metadata["repopilot_index"]
    assert sent["repopilot_chunking"]["other"] == {"chunking_version": "simple"}


def test_malformed_chunking_metadata_is_rejected_before_deletion(workspace, settings):
    async def scenario():
        client = AsyncQdrantClient(":memory:")
        try:
            store = QdrantVectorStore(client, "test_ast_code")
            old = prepare_repository("demo", RepositoryScanner(workspace), SimpleCodeChunker(), 2_000_000)
            await RepositoryIndexer(TestEmbedding(), store, settings).index(old)
            info = await client.get_collection("test_ast_code")
            await client.update_collection("test_ast_code", metadata={**info.config.metadata, "repopilot_chunking": "BAD"})
            with pytest.raises(QdrantCollectionConflictError, match="malformed"):
                await RepositoryIndexer(TestEmbedding(), store, settings).index(prepared(workspace))
            assert await store.count_repository("demo") == 3
        finally:
            await client.close()
    asyncio.run(scenario())


def test_actual_api_default_is_ast_and_closes_provider_and_database(settings):
    client = AsyncQdrantClient(":memory:")
    provider = TestEmbedding()
    with (
        patch("app.api.indexing.get_settings", return_value=settings),
        patch("app.indexing.ast_chunker.get_settings", return_value=settings),
        patch("app.api.indexing.create_repository_scanner", return_value=RepositoryScanner(REPOSITORY_ROOT / "workspaces")),
        patch("app.api.indexing.EmbeddingProviderFactory.create", return_value=provider),
        patch("app.api.indexing.create_qdrant_client", return_value=client),
        TestClient(app) as http,
    ):
        response = http.post("/api/indexing/repositories/demo-repository")
    assert response.status_code == 200
    result = response.json()
    assert (result["files_scanned"], result["ast_parsed_files"], result["fallback_files"], result["failed_files"], result["vectors_indexed"]) == (10, 4, 4, 0, 17)
    assert result["chunking_version"] == "ast-v1" and result["embedding_dimension"] == 3
    assert provider.closed == 1 and "PRIVATE_KEY" not in response.text


@pytest.mark.parametrize("limit", ["chunk_count", "text", "line_length"])
def test_repository_preparation_limits_fail_before_embedding_or_database(workspace, settings, caplog, limit):
    chunker = ASTCodeChunker(max_chunks=1) if limit == "chunk_count" else ASTCodeChunker()
    if limit == "line_length":
        (workspace / "demo" / "Wallet.java").write_text("class Wallet {" + "a" * 8200 + "}", encoding="utf-8")
    with (
        patch("app.api.indexing.get_settings", return_value=settings.model_copy(update={"indexing_max_text_characters": 1}) if limit == "text" else settings),
        patch("app.api.indexing.create_ast_code_chunker", return_value=chunker),
        patch("app.api.indexing.create_repository_scanner", return_value=RepositoryScanner(workspace)),
        patch("app.api.indexing.EmbeddingProviderFactory.create") as provider,
        patch("app.api.indexing.create_qdrant_client") as database,
        caplog.at_level(logging.INFO), TestClient(app) as http,
    ):
        response = http.post("/api/indexing/repositories/demo")
    assert response.status_code == 413 and "failed_files=1" in caplog.text
    provider.assert_not_called()
    database.assert_not_called()
