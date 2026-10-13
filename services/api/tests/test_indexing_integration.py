import asyncio
import os
from pathlib import Path
import shutil
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from app.core.config import Settings
from app.indexing.ast_chunker import ASTCodeChunker
from app.main import app
from app.repository.scanner import RepositoryScanner
from app.retrieval.vector_store import QdrantVectorStore, create_qdrant_client


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("REPOPILOT_INDEXING_INTEGRATION") != "1",
    reason="Opt in with REPOPILOT_INDEXING_INTEGRATION=1, configured embeddings and Qdrant.",
)
def test_real_embedding_and_qdrant_index_repeat_edit_and_empty(tmp_path):
    # Copy fixture source; only this test's unique collection and temp files change.
    source = Path(__file__).resolve().parents[3] / "workspaces" / "demo-repository"
    workspace = tmp_path / "workspaces"
    shutil.copytree(source, workspace / "demo")
    settings = Settings(
        qdrant_collection=f"repopilot_index_test_{uuid4().hex}",
        chunk_lines=10, chunk_overlap_lines=2, embedding_batch_size=16,
    )

    async def inspect_points(expected):
        client = create_qdrant_client(settings)
        try:
            store = QdrantVectorStore(client, settings.qdrant_collection)
            count = await store.count_repository("demo")
            assert count == expected
            points, _ = await client.scroll(
                settings.qdrant_collection, limit=100, with_payload=True, with_vectors=True,
                scroll_filter=store.repository_filter("demo"),
            )
            assert len(points) == expected
            for point in points:
                payload = point.payload
                assert payload["repository"] == "demo"
                text = (workspace / "demo" / payload["path"]).read_bytes().decode("utf-8-sig")
                assert payload["content"] == "".join(text.splitlines(keepends=True)[
                    payload["start_line"] - 1:payload["end_line"]
                ])
                assert len(point.vector) == (await store.collection_info()).dimension
                assert payload["embedding_model"] == settings.embedding_model
                assert payload["chunking_strategy"] in {"ast", "fallback"}
                assert all(field in payload for field in (
                    "symbol", "node_type", "parent_symbol", "chunking_version",
                ))
            return {str(point.id) for point in points}
        finally:
            await client.close()

    async def cleanup():
        client = create_qdrant_client(settings)
        try:
            if await client.collection_exists(settings.qdrant_collection):
                await client.delete_collection(settings.qdrant_collection)
        finally:
            await client.close()

    try:
        with (
            patch("app.api.indexing.get_settings", return_value=settings),
            patch("app.api.indexing.create_repository_scanner", return_value=RepositoryScanner(workspace)),
            patch("app.api.indexing.create_ast_code_chunker", return_value=ASTCodeChunker(chunk_lines=10, overlap_lines=2)),
            TestClient(app) as http,
        ):
            first = http.post("/api/indexing/repositories/demo")
            assert first.status_code == 200, first.text
            assert first.json()["files_scanned"] == 10
            assert first.json()["chunks_created"] == first.json()["vectors_indexed"] == 22
            assert first.json()["ast_parsed_files"] == first.json()["fallback_files"] == 4
            assert first.json()["chunking_version"] == "ast-v1"
            assert first.json()["embedding_dimension"] > 0
            ids = asyncio.run(inspect_points(22))
            repeat = http.post("/api/indexing/repositories/demo")
            assert repeat.status_code == 200, repeat.text
            assert repeat.json()["previous_vectors"] == repeat.json()["vectors_indexed"] == 22
            assert asyncio.run(inspect_points(22)) == ids
            (workspace / "demo" / "README.md").unlink()
            edited = http.post("/api/indexing/repositories/demo")
            assert edited.status_code == 200, edited.text
            assert edited.json()["previous_vectors"] == 22
            assert edited.json()["vectors_indexed"] == 19
            edited_ids = asyncio.run(inspect_points(19))
            assert edited_ids < ids
            for path in (workspace / "demo").rglob("*"):
                if path.is_file():
                    path.unlink()
            empty = http.post("/api/indexing/repositories/demo")
            assert empty.status_code == 200, empty.text
            assert empty.json()["previous_vectors"] == 19
            assert empty.json()["vectors_indexed"] == 0
            assert asyncio.run(inspect_points(0)) == set()
    finally:
        asyncio.run(cleanup())
