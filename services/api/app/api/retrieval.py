from fastapi import APIRouter

from app.core.config import get_settings
from app.embedding.factory import EmbeddingProviderFactory
from app.retrieval.service import RetrievalService
from app.retrieval.vector_store import QdrantVectorStore, create_qdrant_client
from app.schemas.retrieval import RetrievalSearchRequest, RetrievalSearchResponse


router = APIRouter(prefix="/api/retrieval", tags=["retrieval"])


@router.post("/search", response_model=RetrievalSearchResponse)
async def search(request: RetrievalSearchRequest) -> RetrievalSearchResponse:
    """Return TopK chunks from one repository's compatible existing vector index."""
    settings = get_settings()
    provider = EmbeddingProviderFactory.create(settings)
    try:
        client = create_qdrant_client(settings)
        try:
            store = QdrantVectorStore(client, settings.qdrant_collection)
            return await RetrievalService(provider, store, settings).search(request)
        finally:
            await client.close()
    finally:
        await provider.aclose()
