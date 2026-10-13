"""Opt-in Dense recall beyond public TopK; the production service remains unchanged."""
from time import perf_counter
from uuid import UUID

from app.core.config import Settings
from app.core.exceptions import EmbeddingProviderError, QdrantCollectionConflictError, RetrievalResultError
from app.embedding.base import EmbeddingProvider
from app.embedding.identity import embedding_identity
from app.embedding.validation import validate_embedding_batch
from app.retrieval.candidates import CandidateBatch, CandidateSearchRequest, RetrievalCandidate, SourceMetadata, _revision
from app.retrieval.vector_store import QdrantVectorStore


class DenseCandidateChannel:
    name = "dense"

    def __init__(self, provider: EmbeddingProvider, store: QdrantVectorStore, settings: Settings,
                 *, index_revision: str | None = None) -> None:
        _revision(index_revision)
        self.provider, self.store, self.settings = provider, store, settings
        self.index_revision = index_revision

    async def recall(self, request: CandidateSearchRequest) -> CandidateBatch:
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
        points = await self.store.search_repository_candidates(request.repository, vector, request.candidate_limit)
        candidates = []
        try:
            if len(points) > request.candidate_limit:
                raise ValueError("Too many candidates.")
            for rank, point in enumerate(points, 1):
                payload = point.payload or {}
                chunk_id = str(UUID(str(point.id)))
                if (payload.get("repository") != request.repository or payload.get("chunk_id") != chunk_id
                        or payload.get("embedding_model") != self.provider.model_name
                        or payload.get("schema_version") != identity["schema_version"]):
                    raise ValueError("Invalid candidate identity.")
                candidates.append(RetrievalCandidate(
                    chunk_id=chunk_id, repository=request.repository, index_revision=self.index_revision,
                    source_metadata=SourceMetadata.from_payload(payload), channel_scores={self.name: point.score},
                    channel_ranks={self.name: rank}, matched_queries=(request.query,),
                ))
            return CandidateBatch(repository=request.repository, query=request.query, channel=self.name,
                                  index_revision=self.index_revision, candidate_limit=request.candidate_limit,
                                  candidates=tuple(candidates), retrieval_latency_ms=round((perf_counter() - started) * 1000, 2))
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise RetrievalResultError(
                "Qdrant returned invalid or out-of-scope code candidates. Reindex the repository."
            ) from exc
