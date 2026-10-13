import asyncio
import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import app
from app.retrieval.vector_store import QdrantVectorStore, create_qdrant_client, get_vector_store


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("REPOPILOT_QDRANT_INTEGRATION") != "1",
    reason="Opt in with REPOPILOT_QDRANT_INTEGRATION=1 and a running Qdrant server.",
)
def test_fastapi_connects_to_real_qdrant_and_manages_collection() -> None:
    settings = Settings(qdrant_collection=f"repopilot_infra_test_{uuid4().hex}")

    async def real_store():
        client = create_qdrant_client(settings)
        try:
            yield QdrantVectorStore(client, settings.qdrant_collection)
        finally:
            await client.close()

    async def cleanup() -> None:
        client = create_qdrant_client(settings)
        try:
            if await client.collection_exists(settings.qdrant_collection):
                await client.delete_collection(settings.qdrant_collection)
        finally:
            await client.close()

    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_vector_store] = real_store
    try:
        with TestClient(app) as http:
            health = http.get("/health/qdrant")
            assert health.status_code == 200
            assert health.json()["collection_exists"] is False
            first = http.post("/api/vector-store/collection", json={"dimension": 3})
            assert first.status_code == 200
            assert first.json()["created"] is True
            assert first.json()["points_count"] == 0
            assert first.json()["dimension"] == 3
            second = http.post("/api/vector-store/collection", json={"dimension": 3})
            assert second.json()["created"] is False
            assert http.post("/api/vector-store/collection", json={"dimension": 4}).status_code == 409
            assert http.get("/api/vector-store/collection").json()["dimension"] == 3
            assert http.get("/health/qdrant").json()["collection_exists"] is True
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
        asyncio.run(cleanup())
