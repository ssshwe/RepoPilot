import asyncio
import os
from pathlib import Path
import shutil
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from app.core.config import Settings
from app.indexing.chunker import SimpleCodeChunker
from app.indexing.ast_chunker import ASTCodeChunker
from app.main import app
from app.repository.scanner import RepositoryScanner
from app.retrieval.vector_store import create_qdrant_client


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("REPOPILOT_RETRIEVAL_INTEGRATION") != "1",
    reason="Opt in with REPOPILOT_RETRIEVAL_INTEGRATION=1, configured embeddings and Qdrant.",
)
@pytest.mark.parametrize("strategy", ["simple", "ast"])
def test_real_query_embedding_filtered_topk_and_read_only_search(tmp_path, strategy):
    workspace = tmp_path / "workspaces"
    source = Path(__file__).resolve().parents[3] / "workspaces/demo-repository/backend/service"
    # Both repositories contain identical real fixture code; filtering must select one.
    for name in ("demo", "other"):
        destination = workspace / name / "backend/service"
        destination.mkdir(parents=True)
        for filename in ("RefundService.java", "WalletService.java"):
            shutil.copyfile(source / filename, destination / filename)
    settings = Settings(qdrant_collection=f"repopilot_search_test_{uuid4().hex}")

    async def snapshot():
        client = create_qdrant_client(settings)
        try:
            info = await client.get_collection(settings.qdrant_collection)
            points, _ = await client.scroll(
                settings.qdrant_collection, limit=20, with_payload=True, with_vectors=True,
            )
            return info.config.metadata, sorted([point.model_dump() for point in points], key=lambda point: point["id"])
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
            patch("app.api.retrieval.get_settings", return_value=settings),
            patch("app.api.indexing.create_repository_scanner", return_value=RepositoryScanner(workspace)),
            patch("app.api.indexing.create_ast_code_chunker", return_value=(
                SimpleCodeChunker() if strategy == "simple" else ASTCodeChunker()
            )),
            TestClient(app) as http,
        ):
            for repository in ("demo", "other"):
                indexed = http.post(f"/api/indexing/repositories/{repository}")
                assert indexed.status_code == 200, indexed.text
                assert indexed.json()["vectors_indexed"] == (2 if strategy == "simple" else 8)
            before = asyncio.run(snapshot())
            for repository in ("demo", "other"):
                count = 2 if strategy == "simple" else 8
                for top_k in (1, 5, 20):
                    response = http.post("/api/retrieval/search", json={
                        "repository": repository, "query": "退款逻辑在哪里？", "top_k": top_k,
                    })
                    assert response.status_code == 200, response.text
                    result = response.json()
                    assert result["embedding_dimension"] == indexed.json()["embedding_dimension"]
                    assert len(result["results"]) == min(top_k, count)
                    scores = [item["score"] for item in result["results"]]
                    assert scores == sorted(scores, reverse=True)
                    for rank, item in enumerate(result["results"], start=1):
                        assert item["rank"] == rank and item["repository"] == repository
                        text = (workspace / repository / item["path"]).read_bytes().decode("utf-8-sig")
                        assert item["content"] == "".join(text.splitlines(keepends=True)[
                            item["start_line"] - 1:item["end_line"]
                        ])
                        assert "vector" not in item
                        assert item["chunking_strategy"] == strategy
                        assert item["chunking_version"] == ("line-window-v1" if strategy == "simple" else "ast-v1")
                        if strategy == "ast":
                            assert item["node_type"]
                            if item["parent_symbol"]:
                                assert item["symbol"].startswith(item["parent_symbol"] + ".")
            empty = http.post("/api/retrieval/search", json={"repository": "not-indexed", "query": "退款"})
            assert empty.status_code == 200 and empty.json()["results"] == []
            assert http.post("/api/retrieval/search", json={"repository": "demo", "query": "退款", "top_k": 21}).status_code == 422
            assert asyncio.run(snapshot()) == before
    finally:
        asyncio.run(cleanup())
