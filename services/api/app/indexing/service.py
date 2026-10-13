from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
import logging
from threading import Lock

from qdrant_client import models

from app.core.config import Settings
from app.core.exceptions import (
    ChunkingLimitError, EmbeddingProviderError, IndexingBusyError,
    IndexingLimitError, QdrantUnavailableError,
)
from app.embedding.base import EmbeddingProvider
from app.embedding.identity import embedding_identity
from app.embedding.validation import MAX_EMBEDDING_TEXT_LENGTH, validate_embedding_batch
from app.indexing.chunker import SimpleCodeChunker
from app.indexing.ast_chunker import ASTCodeChunker, AST_CHUNKING_VERSION, FALLBACK_CHUNKING_VERSION
from app.indexing.models import CodeChunk
from app.repository.models import RepositoryScanResult
from app.repository.scanner import RepositoryScanner
from app.retrieval.vector_store import QdrantVectorStore
from app.schemas.indexing import RepositoryIndexResponse
from app.schemas.repositories import SkippedPathInfo


logger = logging.getLogger(__name__)
_INDEXING_LOCK = Lock()


@contextmanager
def indexing_guard() -> Iterator[None]:
    # One API process only. Multiple workers / external writers need coordination.
    if not _INDEXING_LOCK.acquire(blocking=False):
        raise IndexingBusyError("Another indexing request is running; retry after it completes.")
    try:
        yield
    finally:
        _INDEXING_LOCK.release()


@dataclass(frozen=True)
class PreparedRepository:
    scan: RepositoryScanResult
    chunks: list[CodeChunk]
    ast_parsed_files: int
    fallback_files: int
    chunking_version: str
    fallback_chunking_version: str | None
    chunk_lines: int
    overlap_lines: int
    max_chunk_characters: int

    def chunking_metadata(self) -> dict[str, str | int | None]:
        return {
            "chunking_version": self.chunking_version,
            "fallback_chunking_version": self.fallback_chunking_version,
            "chunk_lines": self.chunk_lines, "overlap_lines": self.overlap_lines,
            "max_chunk_characters": self.max_chunk_characters,
        }


def prepare_repository(
    repository: str, scanner: RepositoryScanner, chunker: SimpleCodeChunker | ASTCodeChunker,
    max_text_characters: int,
) -> PreparedRepository:
    scan = scanner.scan(repository)
    chunks: list[CodeChunk] = []
    text_characters = 0
    ast_files = fallback_files = 0
    for file in scan.files:
        try:
            if isinstance(chunker, ASTCodeChunker):
                result = chunker.chunk_file_result(file)
                file_chunks = result.chunks
            else:
                file_chunks = chunker.iter_file_chunks(file)
            for chunk in file_chunks:
                if len(chunks) >= chunker.max_chunks:
                    raise ChunkingLimitError("Chunking exceeds the chunk count limit.")
                if len(chunk.content) > MAX_EMBEDDING_TEXT_LENGTH:
                    raise ChunkingLimitError("A chunk exceeds the embedding text character limit.")
                text_characters += len(chunk.content)
                if text_characters > max_text_characters:
                    raise IndexingLimitError("Indexing exceeds the total chunk text character limit.")
                chunks.append(chunk)
            if isinstance(chunker, ASTCodeChunker):
                ast_files += int(result.strategy == "ast")
                fallback_files += int(result.strategy == "fallback")
        except (ChunkingLimitError, IndexingLimitError):
            logger.info(
                "Index preparation failed repository=%s ast_parsed_files=%s fallback_files=%s failed_files=1",
                repository, ast_files, fallback_files,
            )
            raise
    is_ast = isinstance(chunker, ASTCodeChunker)
    logger.info(
        "Index prepared repository=%s files_scanned=%s ast_parsed_files=%s fallback_files=%s failed_files=0 chunks_created=%s",
        repository, scan.files_scanned, ast_files, fallback_files, len(chunks),
    )
    return PreparedRepository(
        scan=scan, chunks=chunks, ast_parsed_files=ast_files, fallback_files=fallback_files,
        chunking_version=AST_CHUNKING_VERSION if is_ast else "line-window-v1",
        fallback_chunking_version=FALLBACK_CHUNKING_VERSION if is_ast else None,
        chunk_lines=chunker.chunk_lines, overlap_lines=chunker.overlap_lines,
        max_chunk_characters=chunker.max_chunk_characters,
    )


class RepositoryIndexer:
    def __init__(
        self, provider: EmbeddingProvider, store: QdrantVectorStore, settings: Settings,
    ) -> None:
        self.provider, self.store, self.settings = provider, store, settings

    async def index(self, prepared: PreparedRepository) -> RepositoryIndexResponse:
        scan, chunks = prepared.scan, prepared.chunks
        batch_size = self.settings.embedding_batch_size
        vectors: list[list[float]] = []
        dimension: int | None = None
        for start in range(0, len(chunks), batch_size):
            batch = chunks[start:start + batch_size]
            output = await self.provider.embed_texts([chunk.content for chunk in batch])
            try:
                validated = validate_embedding_batch(output, len(batch))
            except (ValueError, TypeError) as exc:
                raise EmbeddingProviderError("Indexing received invalid embedding vectors.") from exc
            current_dimension = len(validated[0])
            if dimension is not None and dimension != current_dimension:
                raise EmbeddingProviderError("Embedding dimensions changed between indexing batches.")
            dimension = current_dimension
            # Bound the complete prepared vector set before any old points are deleted.
            if len(chunks) * dimension > self.settings.indexing_max_vector_values:
                raise IndexingLimitError("Indexing exceeds the prepared vector coordinate limit.")
            vectors.extend(validated)

        identity = embedding_identity(self.settings, self.provider.model_name)
        collection_dimension = await self.store.prepare_index(dimension, identity)
        previous = 0
        indexed = 0
        if collection_dimension is not None:
            previous = await self.store.count_repository(scan.repository)
            try:
                await self.store.delete_repository(scan.repository)
                for start in range(0, len(chunks), batch_size):
                    points = [models.PointStruct(
                        id=chunk.chunk_id, vector=vector,
                        payload={
                            **asdict(chunk), "embedding_model": self.provider.model_name,
                            "schema_version": identity["schema_version"],
                            "chunk_lines": prepared.chunk_lines,
                            "overlap_lines": 0 if chunk.chunking_strategy == "ast" else prepared.overlap_lines,
                            "max_chunk_characters": prepared.max_chunk_characters,
                        },
                    ) for chunk, vector in zip(
                        chunks[start:start + batch_size], vectors[start:start + batch_size],
                        strict=True,
                    )]
                    await self.store.upsert_points(points)
                indexed = await self.store.count_repository(scan.repository)
                if indexed != len(chunks):
                    raise QdrantUnavailableError("The final repository point count does not match.")
                await self.store.record_repository_chunking(scan.repository, prepared.chunking_metadata())
            except QdrantUnavailableError as exc:
                logger.warning(
                    "Index replacement unconfirmed repository=%s collection=%s chunks_created=%s",
                    scan.repository, self.store.collection, len(chunks),
                )
                raise QdrantUnavailableError(
                    "Qdrant replacement could not be confirmed; the repository index may be partial. "
                    "Retry the full indexing request."
                ) from exc

        logger.info(
            "Index complete repository=%s files_scanned=%s chunks_created=%s "
            "embedding_model=%s embedding_dimension=%s vectors_indexed=%s collection=%s "
            "ast_parsed_files=%s fallback_files=%s failed_files=0 chunking_version=%s",
            scan.repository, scan.files_scanned, len(chunks), self.provider.model_name,
            collection_dimension, indexed, self.store.collection,
            prepared.ast_parsed_files, prepared.fallback_files, prepared.chunking_version,
        )
        return RepositoryIndexResponse(
            repository=scan.repository, files_scanned=scan.files_scanned,
            supported_files=len(scan.files), skipped_files=len(scan.skipped),
            chunks_created=len(chunks), vectors_indexed=indexed, previous_vectors=previous,
            collection=self.store.collection, embedding_model=self.provider.model_name,
            embedding_dimension=collection_dimension,
            chunk_lines=prepared.chunk_lines, overlap_lines=prepared.overlap_lines,
            ast_parsed_files=prepared.ast_parsed_files, fallback_files=prepared.fallback_files,
            failed_files=0, chunking_version=prepared.chunking_version,
            fallback_chunking_version=prepared.fallback_chunking_version,
            max_chunk_characters=prepared.max_chunk_characters,
            skipped=[SkippedPathInfo(path=item.path, reason=item.reason) for item in scan.skipped],
            pruned_directories=[SkippedPathInfo(path=item.path, reason=item.reason) for item in scan.pruned_directories],
        )
