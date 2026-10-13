import logging
from collections.abc import AsyncIterator
from urllib.parse import urlsplit

from qdrant_client import AsyncQdrantClient, models
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse

from app.core.config import Settings, get_settings
from app.core.exceptions import (
    QdrantCollectionConflictError,
    QdrantCollectionNotFoundError,
    QdrantUnavailableError,
)
from app.schemas.vector_store import (
    CollectionInfoResponse,
    EnsureCollectionResponse,
    QdrantHealthResponse,
)
from app.repository.paths import validate_repository_name
from app.embedding.validation import validate_vector


logger = logging.getLogger(__name__)
_CONNECTION_ERRORS = (UnexpectedResponse, ResponseHandlingException)
_UNAVAILABLE_MESSAGE = "Qdrant is unavailable or rejected the configured credentials."


class QdrantVectorStore:
    """Manage one configured collection, repository writes and filtered search."""

    def __init__(self, client: AsyncQdrantClient, collection: str) -> None:
        self._client = client
        self.collection = collection

    async def health(self) -> QdrantHealthResponse:
        # Unlike /readyz, this authenticated operation also checks API access.
        try:
            collections = await self._client.get_collections()
        except _CONNECTION_ERRORS as exc:
            raise QdrantUnavailableError(_UNAVAILABLE_MESSAGE) from exc
        return QdrantHealthResponse(
            collection=self.collection,
            collection_exists=any(
                item.name == self.collection for item in collections.collections
            ),
        )

    async def _read_collection(self) -> models.CollectionInfo:
        try:
            info = await self._client.get_collection(self.collection)
        except UnexpectedResponse as exc:
            if exc.status_code == 404:
                raise QdrantCollectionNotFoundError(
                    "The configured Qdrant collection does not exist yet."
                ) from exc
            raise QdrantUnavailableError(_UNAVAILABLE_MESSAGE) from exc
        except ResponseHandlingException as exc:
            raise QdrantUnavailableError(_UNAVAILABLE_MESSAGE) from exc
        return info

    def _describe_collection(self, info: models.CollectionInfo) -> CollectionInfoResponse:
        vectors = info.config.params.vectors
        if not isinstance(vectors, models.VectorParams) or vectors.distance != models.Distance.COSINE:
            raise QdrantCollectionConflictError(
                "The configured collection must contain one unnamed Cosine vector."
            )
        return CollectionInfoResponse(
            collection=self.collection,
            dimension=vectors.size,
            distance="Cosine",
            points_count=info.points_count,
            status=info.status.value,
        )

    async def collection_info(self) -> CollectionInfoResponse:
        return self._describe_collection(await self._read_collection())

    async def verify_search_collection(self, identity: dict[str, str]) -> CollectionInfoResponse:
        # Search is read-only: never adopt, create, bind or reset a collection.
        info = await self._read_collection()
        description = self._describe_collection(info)
        if (info.config.metadata or {}).get("repopilot_index") != identity:
            raise QdrantCollectionConflictError(
                "Collection embedding identity is incompatible or missing. "
                "Use the indexing embedding configuration and a compatible indexed collection."
            )
        return description

    async def search_repository(
        self, repository: str, query_vector: list[float], top_k: int,
    ) -> list[models.ScoredPoint]:
        if type(top_k) is not int or not 1 <= top_k <= 20:
            raise ValueError("top_k must be an integer between 1 and 20.")
        return await self._query_repository(repository, query_vector, top_k)

    async def search_repository_candidates(
        self, repository: str, query_vector: list[float], candidate_limit: int,
    ) -> list[models.ScoredPoint]:
        # Separate internal capacity; the public search method still caps TopK at 20.
        from app.retrieval.candidates import validate_candidate_limit
        validate_candidate_limit(candidate_limit)
        return await self._query_repository(repository, query_vector, candidate_limit)

    async def _query_repository(
        self, repository: str, query_vector: list[float], limit: int,
    ) -> list[models.ScoredPoint]:
        vector = validate_vector(query_vector)
        repository_filter = self.repository_filter(repository)
        try:
            response = await self._client.query_points(
                collection_name=self.collection, query=vector,
                query_filter=repository_filter, limit=limit,
                with_payload=[
                    "chunk_id", "repository", "path", "language", "start_line", "end_line",
                    "content", "embedding_model", "schema_version",
                    "symbol", "node_type", "parent_symbol", "chunking_strategy", "chunking_version",
                ],
                with_vectors=False,
            )
        except UnexpectedResponse as exc:
            if exc.status_code == 404:
                raise QdrantCollectionNotFoundError(
                    "The configured Qdrant collection does not exist yet."
                ) from exc
            raise QdrantUnavailableError(_UNAVAILABLE_MESSAGE) from exc
        except ResponseHandlingException as exc:
            raise QdrantUnavailableError(_UNAVAILABLE_MESSAGE) from exc
        return response.points

    async def ensure_collection(self, dimension: int) -> EnsureCollectionResponse:
        # No destructive recreate: a dimension mismatch must be resolved explicitly.
        created = False
        try:
            exists = await self._client.collection_exists(self.collection)
            if not exists:
                try:
                    created = await self._client.create_collection(
                        collection_name=self.collection,
                        vectors_config=models.VectorParams(
                            size=dimension, distance=models.Distance.COSINE
                        ),
                    )
                    if not created:
                        raise QdrantUnavailableError("Qdrant did not confirm collection creation.")
                except UnexpectedResponse as exc:
                    # Another request may have created it after the existence check.
                    if exc.status_code != 409:
                        raise
        except _CONNECTION_ERRORS as exc:
            raise QdrantUnavailableError(_UNAVAILABLE_MESSAGE) from exc

        info = await self.collection_info()
        if info.dimension != dimension:
            raise QdrantCollectionConflictError(
                f"Collection dimension is {info.dimension}; requested {dimension}. "
                "Use a compatible dimension or a different QDRANT_COLLECTION."
            )
        logger.info(
            "Qdrant collection=%s dimension=%s distance=Cosine created=%s",
            self.collection, dimension, created,
        )
        return EnsureCollectionResponse(**info.model_dump(), created=created)

    async def prepare_index(
        self, dimension: int | None, identity: dict[str, str],
    ) -> int | None:
        """Bind an empty collection to a model, or verify its existing ownership."""
        if dimension is not None:
            await self.ensure_collection(dimension)
        else:
            try:
                if not await self._client.collection_exists(self.collection):
                    return None
                await self.collection_info()
            except _CONNECTION_ERRORS as exc:
                raise QdrantUnavailableError(_UNAVAILABLE_MESSAGE) from exc
        try:
            info = await self._client.get_collection(self.collection)
            metadata = info.config.metadata or {}
            if not isinstance(metadata.get("repopilot_chunking", {}), dict):
                raise QdrantCollectionConflictError("Collection chunking metadata is malformed.")
            existing = metadata.get("repopilot_index")
            if existing is not None and existing != identity:
                raise QdrantCollectionConflictError(
                    "Collection embedding identity is incompatible. Use the original "
                    "embedding configuration or a different QDRANT_COLLECTION."
                )
            if existing is None:
                count = await self._client.count(self.collection, exact=True)
                if count.count:
                    raise QdrantCollectionConflictError(
                        "A nonempty collection without RepoPilot indexing metadata cannot be adopted. "
                        "Use a different QDRANT_COLLECTION."
                    )
                confirmed = await self._client.update_collection(
                    self.collection, metadata={**metadata, "repopilot_index": identity},
                )
                if not confirmed:
                    raise QdrantUnavailableError("Qdrant did not confirm indexing metadata.")
                updated = await self._client.get_collection(self.collection)
                if (updated.config.metadata or {}).get("repopilot_index") != identity:
                    raise QdrantUnavailableError("Qdrant indexing metadata could not be verified.")
        except _CONNECTION_ERRORS as exc:
            raise QdrantUnavailableError(_UNAVAILABLE_MESSAGE) from exc
        return (await self.collection_info()).dimension

    async def record_repository_chunking(
        self, repository: str, profile: dict[str, str | int | None],
    ) -> None:
        """Commit a verified repository's profile without relabeling other repos.

        Called only after point writes and the final count have succeeded. The
        shared embedding identity stays separate so legacy dense search works.
        """
        repository = validate_repository_name(repository)
        info = await self._read_collection()
        metadata = info.config.metadata or {}
        existing = metadata.get("repopilot_chunking", {})
        if not isinstance(existing, dict):
            raise QdrantUnavailableError("Collection chunking metadata could not be updated.")
        profiles = {**existing, repository: profile}
        try:
            confirmed = await self._client.update_collection(
                self.collection, metadata={**metadata, "repopilot_chunking": profiles},
            )
        except _CONNECTION_ERRORS as exc:
            raise QdrantUnavailableError(_UNAVAILABLE_MESSAGE) from exc
        if not confirmed:
            raise QdrantUnavailableError("Qdrant did not confirm repository chunking metadata.")
        updated = await self._read_collection()
        if (updated.config.metadata or {}).get("repopilot_chunking") != profiles:
            raise QdrantUnavailableError("Repository chunking metadata could not be verified.")

    @staticmethod
    def repository_filter(repository: str) -> models.Filter:
        return models.Filter(must=[models.FieldCondition(
            key="repository", match=models.MatchValue(value=validate_repository_name(repository)),
        )])

    async def count_repository(self, repository: str) -> int:
        try:
            result = await self._client.count(
                self.collection, count_filter=self.repository_filter(repository), exact=True,
            )
        except _CONNECTION_ERRORS as exc:
            raise QdrantUnavailableError(_UNAVAILABLE_MESSAGE) from exc
        return result.count

    async def delete_repository(self, repository: str) -> None:
        try:
            result = await self._client.delete(
                self.collection,
                points_selector=models.FilterSelector(filter=self.repository_filter(repository)),
                wait=True,
            )
        except _CONNECTION_ERRORS as exc:
            raise QdrantUnavailableError(_UNAVAILABLE_MESSAGE) from exc
        if result.status != models.UpdateStatus.COMPLETED:
            raise QdrantUnavailableError("Qdrant did not confirm repository point deletion.")

    async def upsert_points(self, points: list[models.PointStruct]) -> None:
        if not points:
            return
        try:
            result = await self._client.upsert(self.collection, points=points, wait=True)
        except _CONNECTION_ERRORS as exc:
            raise QdrantUnavailableError(_UNAVAILABLE_MESSAGE) from exc
        if result.status != models.UpdateStatus.COMPLETED:
            raise QdrantUnavailableError("Qdrant did not confirm point upsert.")


def create_qdrant_client(settings: Settings) -> AsyncQdrantClient:
    key = settings.qdrant_api_key.get_secret_value().strip() or None
    options: dict[str, object] = {}
    if urlsplit(settings.qdrant_url).hostname in {"localhost", "127.0.0.1", "::1"}:
        # Match the Stage 1 local-provider boundary: do not proxy loopback traffic.
        options["trust_env"] = False
    return AsyncQdrantClient(
        url=settings.qdrant_url,
        api_key=key,
        timeout=settings.qdrant_timeout_seconds,
        prefer_grpc=False,
        # Health/collection operations verify the server; construction stays offline.
        check_compatibility=False,
        **options,
    )


async def get_vector_store() -> AsyncIterator[QdrantVectorStore]:
    # Request scope keeps async clients tied to the current event loop and closes
    # their HTTP pools even when a request fails. Stage 1 never needs this client.
    settings = get_settings()
    client = create_qdrant_client(settings)
    try:
        yield QdrantVectorStore(client, settings.qdrant_collection)
    finally:
        await client.close()
