"""Diagnostic-only routes. Snapshot sync reads Dense Points; never reindexes."""
from pathlib import Path
from time import perf_counter

from fastapi import APIRouter, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from app.core.config import get_settings
from app.embedding.factory import EmbeddingProviderFactory
from app.indexing.service import indexing_guard
from app.retrieval.debug import DebugRetrievalService
from app.retrieval.snapshot_models import SnapshotError
from app.retrieval.snapshot_store import SnapshotStore
from app.retrieval.snapshots import QdrantImageReader, SnapshotCoordinator
from app.retrieval.vector_store import QdrantVectorStore, create_qdrant_client
from app.schemas.repositories import RepositoryScanRequest
from app.schemas.retrieval_debug import DebugSearchRequest, DebugSearchResponse

class SafeDebugRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()
        async def safe(request):
            try:
                return await original(request)
            except RequestValidationError:
                return JSONResponse(status_code=422, content={"detail":"Invalid retrieval debug request."})
        return safe


router = APIRouter(route_class=SafeDebugRoute)
SNAPSHOT_PATH = Path(__file__).resolve().parents[4] / ".tools/retrieval-debug/snapshots.sqlite3"


def create_coordinator(settings, client):
    return SnapshotCoordinator(SnapshotStore(SNAPSHOT_PATH), QdrantImageReader(QdrantVectorStore(client, settings.qdrant_collection), settings))


@router.post("/debug/snapshot")
async def synchronize(request: RepositoryScanRequest):
    settings = get_settings()
    client = create_qdrant_client(settings)
    started = perf_counter()
    try:
        coordinator = create_coordinator(settings, client)
        # Cooperate with the existing production indexer's whole-repository lock.
        with indexing_guard():
            image = await coordinator.reader.capture(request.repository)
            snapshot = await coordinator.rebuild(request.repository, expected_source_sha256=image.source_sha256)
        return {"repository":request.repository,"collection":image.scope.collection,"index_revision":snapshot.index.index_revision,
                "chunks":len(snapshot.index.chunks),"elapsed_ms":round((perf_counter()-started)*1000,3)}
    except SnapshotError:
        raise HTTPException(status_code=409, detail="Snapshot synchronization failed; check the index and retry.") from None
    except OSError:
        raise HTTPException(status_code=503, detail="Local snapshot storage is unavailable.") from None
    finally:
        await client.close()


@router.post("/debug", response_model=DebugSearchResponse)
async def debug_search(request: DebugSearchRequest):
    settings = get_settings()
    client = create_qdrant_client(settings)
    provider = None
    try:
        coordinator = create_coordinator(settings, client)
        # Fail absent/non-ready snapshots before constructing a provider.
        coordinator.store.load_ready(coordinator.reader.scope(request.repository))
        if request.mode != "bm25": provider = EmbeddingProviderFactory.create(settings)
        return await DebugRetrievalService(coordinator, settings, provider).search(request)
    except SnapshotError:
        raise HTTPException(status_code=409, detail="Snapshot missing, rebuilding or stale; synchronize the retrieval snapshot.") from None
    except ValueError:
        raise HTTPException(status_code=502, detail="Retrieval debug provenance validation failed.") from None
    except OSError:
        raise HTTPException(status_code=503, detail="Local snapshot storage is unavailable.") from None
    finally:
        try:
            if provider is not None: await provider.aclose()
        finally:
            await client.close()
