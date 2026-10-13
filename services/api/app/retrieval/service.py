import json
import logging
from time import perf_counter
from uuid import UUID

from pydantic import ValidationError

from app.core.config import Settings
from app.core.exceptions import (
    EmbeddingProviderError, QdrantCollectionConflictError, RetrievalResultError,
)
from app.embedding.base import EmbeddingProvider
from app.embedding.identity import embedding_identity
from app.embedding.validation import validate_embedding_batch
from app.retrieval.vector_store import QdrantVectorStore
from app.schemas.retrieval import RetrievalResult, RetrievalSearchRequest, RetrievalSearchResponse


logger = logging.getLogger(__name__)


class RetrievalService:
    def __init__(
        self, provider: EmbeddingProvider, store: QdrantVectorStore, settings: Settings,
    ) -> None:
        self.provider, self.store, self.settings = provider, store, settings

    async def search(self, request: RetrievalSearchRequest) -> RetrievalSearchResponse:
        started = perf_counter()
        identity = embedding_identity(self.settings, self.provider.model_name)
        info = await self.store.verify_search_collection(identity)
        output = await self.provider.embed_texts([request.query])
        try:
            vector = validate_embedding_batch(output, 1)[0]
        except (ValueError, TypeError) as exc:
            raise EmbeddingProviderError("Search received invalid query embedding vectors.") from exc
        if len(vector) != info.dimension:
            raise QdrantCollectionConflictError(
                f"Query embedding dimension is {len(vector)}; collection dimension is {info.dimension}. "
                "Use a compatible embedding model and collection."
            )
        points = await self.store.search_repository(request.repository, vector, request.top_k)
        results: list[RetrievalResult] = []
        try:
            if len(points) > request.top_k:
                raise ValueError("Too many search points")
            for rank, point in enumerate(points, start=1):
                payload = point.payload or {}
                chunk_id = str(UUID(str(point.id)))
                if (
                    payload.get("repository") != request.repository
                    or payload.get("chunk_id") != chunk_id
                    or payload.get("embedding_model") != self.provider.model_name
                    or payload.get("schema_version") != identity["schema_version"]
                    or any(item.chunk_id == chunk_id for item in results)
                ):
                    raise ValueError("Invalid point identity or repository")
                results.append(RetrievalResult(
                    rank=rank, chunk_id=chunk_id, repository=payload["repository"], score=point.score,
                    path=payload["path"], language=payload["language"],
                    start_line=payload["start_line"], end_line=payload["end_line"],
                    content=payload["content"],
                    **{key: payload[key] for key in (
                        "symbol", "node_type", "parent_symbol", "chunking_strategy", "chunking_version",
                    ) if key in payload},
                ))
        except (ValueError, TypeError, KeyError, ValidationError) as exc:
            # Never expose upstream payloads or partial/out-of-scope code on errors.
            raise RetrievalResultError(
                "Qdrant returned invalid or out-of-scope code points. Reindex the repository."
            ) from exc
        elapsed = round((perf_counter() - started) * 1000, 2)
        logger.info(
            "Retrieval repository=%s retrieval_query=%s top_k=%s retrieval_latency_ms=%s "
            "results=%s embedding_model=%s embedding_dimension=%s collection=%s",
            request.repository, json.dumps(request.query, ensure_ascii=False), request.top_k,
            elapsed, len(results), self.provider.model_name, len(vector), self.store.collection,
        )
        return RetrievalSearchResponse(
            repository=request.repository, query=request.query, top_k=request.top_k,
            collection=self.store.collection, embedding_model=self.provider.model_name,
            embedding_dimension=len(vector), retrieval_latency_ms=elapsed, results=results,
        )
