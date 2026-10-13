from fastapi import APIRouter

from app.context import create_context_builder
from app.core.config import get_settings
from app.embedding.factory import EmbeddingProviderFactory
from app.llm.factory import get_llm_provider
from app.rag.service import CodeChatService
from app.retrieval.service import RetrievalService
from app.retrieval.vector_store import QdrantVectorStore, create_qdrant_client
from app.schemas.code_chat import CodeChatRequest, CodeChatResponse


router = APIRouter(prefix="/api", tags=["code-chat"])


@router.post("/code-chat", response_model=CodeChatResponse)
async def code_chat(request: CodeChatRequest) -> CodeChatResponse:
    """Retrieve real indexed context and perform one structured generation."""
    settings = get_settings()
    builder = create_context_builder()
    embedding = EmbeddingProviderFactory.create(settings)
    try:
        client = create_qdrant_client(settings)
        try:
            retrieval = RetrievalService(embedding, QdrantVectorStore(client, settings.qdrant_collection), settings)
            return await CodeChatService(retrieval, builder, get_llm_provider).answer(request)
        finally:
            await client.close()
    finally:
        await embedding.aclose()
