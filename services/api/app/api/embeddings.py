from fastapi import APIRouter

from app.core.exceptions import EmbeddingProviderError
from app.embedding.factory import get_embedding_provider
from app.embedding.validation import validate_embedding_batch, validate_vector
from app.retrieval.similarity import cosine_similarity
from app.schemas.embeddings import (
    VECTOR_PREVIEW_LENGTH,
    EmbeddingDebugRequest,
    EmbeddingDebugResponse,
    EmbeddingSimilarityRequest,
    EmbeddingSimilarityResponse,
)


router = APIRouter(prefix="/api/embeddings", tags=["embeddings"])


@router.post("/debug", response_model=EmbeddingDebugResponse)
async def embedding_debug(body: EmbeddingDebugRequest) -> EmbeddingDebugResponse:
    async with get_embedding_provider() as provider:
        raw_vector = await provider.embed_text(body.text)
        try:
            vector = validate_vector(raw_vector)
        except (TypeError, ValueError, OverflowError):
            raise EmbeddingProviderError("The embedding provider returned an invalid vector.") from None
        return EmbeddingDebugResponse(
            model=provider.model_name,
            dimension=len(vector),
            preview=vector[:VECTOR_PREVIEW_LENGTH],
        )


@router.post("/similarity", response_model=EmbeddingSimilarityResponse)
async def embedding_similarity(
    body: EmbeddingSimilarityRequest,
) -> EmbeddingSimilarityResponse:
    async with get_embedding_provider() as provider:
        raw_vectors = await provider.embed_texts([body.text_a, body.text_b])
        try:
            vectors = validate_embedding_batch(raw_vectors, expected_count=2)
            score = cosine_similarity(vectors[0], vectors[1])
        except (TypeError, ValueError, OverflowError):
            raise EmbeddingProviderError("The embedding provider returned invalid vectors.") from None
        return EmbeddingSimilarityResponse(
            model=provider.model_name, dimension=len(vectors[0]), similarity=score
        )
