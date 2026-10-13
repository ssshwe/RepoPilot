from fastapi import APIRouter

from app.context import create_context_builder
from app.core.config import get_settings
from app.embedding.factory import EmbeddingProviderFactory
from app.issues.service import IssueAnalysisService
from app.llm.factory import get_llm_provider
from app.retrieval.service import RetrievalService
from app.retrieval.vector_store import QdrantVectorStore, create_qdrant_client
from app.schemas.issue_analysis import IssueAnalysisRequest, IssueAnalysisResponse


router = APIRouter(prefix="/api", tags=["issue-analysis"])


@router.post("/issue-analysis", response_model=IssueAnalysisResponse)
async def analyze_issue(request: IssueAnalysisRequest) -> IssueAnalysisResponse:
    settings = get_settings()
    builder = create_context_builder()
    embedding = EmbeddingProviderFactory.create(settings)
    try:
        client = create_qdrant_client(settings)
        try:
            retrieval = RetrievalService(embedding, QdrantVectorStore(client, settings.qdrant_collection), settings)
            return await IssueAnalysisService(retrieval, builder, get_llm_provider).analyze(request)
        finally:
            await client.close()
    finally:
        await embedding.aclose()
