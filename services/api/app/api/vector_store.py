from typing import Annotated

from fastapi import APIRouter, Depends

from app.retrieval.vector_store import QdrantVectorStore, get_vector_store
from app.schemas.vector_store import (
    CollectionInfoResponse,
    EnsureCollectionRequest,
    EnsureCollectionResponse,
    QdrantHealthResponse,
)


router = APIRouter(tags=["vector-store"])
VectorStoreDependency = Annotated[QdrantVectorStore, Depends(get_vector_store)]


@router.get("/health/qdrant", response_model=QdrantHealthResponse)
async def qdrant_health(store: VectorStoreDependency) -> QdrantHealthResponse:
    return await store.health()


@router.get("/api/vector-store/collection", response_model=CollectionInfoResponse)
async def collection_info(store: VectorStoreDependency) -> CollectionInfoResponse:
    return await store.collection_info()


@router.post("/api/vector-store/collection", response_model=EnsureCollectionResponse)
async def ensure_collection(
    request: EnsureCollectionRequest, store: VectorStoreDependency
) -> EnsureCollectionResponse:
    return await store.ensure_collection(request.dimension)
