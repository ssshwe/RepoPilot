"""Read existing Qdrant Chunks and coordinate opt-in lexical/Dense consistency."""
from collections.abc import Awaitable, Callable
from dataclasses import asdict
import hashlib
from typing import Protocol, TypeVar

from qdrant_client import models
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse

from app.embedding.identity import embedding_identity
from app.retrieval.candidates import SourceMetadata
from app.retrieval.snapshot_models import IndexedRepository, LexicalSnapshot, SnapshotError, SnapshotScope, canonical, digest
from app.retrieval.snapshot_store import SnapshotStore
from app.retrieval.tokenizer import CodeTokenizer

T = TypeVar("T")


class RepositoryImageReader(Protocol):
    def scope(self, repository: str) -> SnapshotScope: ...
    async def capture(self, repository: str) -> IndexedRepository: ...


class QdrantImageReader:
    def __init__(self, store, settings):
        self.store, self.settings = store, settings
        self.identity = embedding_identity(settings, settings.embedding_model)
        self.backend_id = "sha256:" + hashlib.sha256(settings.qdrant_url.strip().rstrip("/").encode()).hexdigest()

    def scope(self, repository):
        return SnapshotScope(self.backend_id, self.store.collection, repository)

    async def capture(self, repository):
        scope = self.scope(repository)
        client = self.store._client
        def configuration(info):
            vectors = info.config.params.vectors
            metadata = info.config.metadata or {}
            profiles = metadata.get("repopilot_chunking", {})
            if (not isinstance(vectors, models.VectorParams) or vectors.distance != models.Distance.COSINE
                    or metadata.get("repopilot_index") != self.identity or not isinstance(profiles, dict)):
                raise SnapshotError("Qdrant index identity or vector/chunking configuration is incompatible.")
            return {"dimension": vectors.size, "distance": "Cosine", "embedding": self.identity,
                    "chunking": profiles.get(repository)}
        try:
            before = configuration(await client.get_collection(self.store.collection))
            count = await self.store.count_repository(repository)
            if count > 10000 or count * before["dimension"] > 2_000_000:
                raise SnapshotError("Repository index exceeds snapshot budget.")
            records, offset, seen_offsets = [], None, set()
            while True:
                page, offset = await client.scroll(self.store.collection, scroll_filter=self.store.repository_filter(repository),
                                                   limit=100, offset=offset, with_payload=True, with_vectors=True)
                records.extend(point.model_dump(mode="json") for point in page)
                if len(records) > count:
                    raise SnapshotError("Repository Point count changed while capturing.")
                if offset is None:
                    break
                marker = canonical(offset)
                if marker in seen_offsets:
                    raise SnapshotError("Qdrant repeated a snapshot page offset.")
                seen_offsets.add(marker)
            after = configuration(await client.get_collection(self.store.collection))
            if canonical(before) != canonical(after) or count != len(records) or count != await self.store.count_repository(repository):
                raise SnapshotError("Repository index changed while capturing.")
            return IndexedRepository.from_records(scope, before, records)
        except (UnexpectedResponse, ResponseHandlingException) as exc:
            raise SnapshotError("Qdrant repository snapshot could not be read.") from exc


class SnapshotCoordinator:
    def __init__(self, store: SnapshotStore, reader: RepositoryImageReader, tokenizer=None):
        self.store, self.reader, self.tokenizer = store, reader, tokenizer or CodeTokenizer()

    async def _stable(self, repository):
        first = await self.reader.capture(repository)
        second = await self.reader.capture(repository)
        if (first.scope != self.reader.scope(repository) or second.scope != first.scope
                or first.index_revision != second.index_revision):
            raise SnapshotError("Dense repository image drifted between captures.")
        return second

    async def rebuild(self, repository, *, expected_source_sha256: str,
                      mutate: Callable[[], Awaitable[object]] | None = None):
        scope = self.reader.scope(repository)
        with self.store.rebuilding(scope, expected_source_sha256) as ticket:
            if mutate is not None:
                await mutate()
            image = await self._stable(repository)
            if image.source_sha256 != expected_source_sha256:
                raise SnapshotError("Dense repository does not match the complete intended Chunk inventory.")
            snapshot = LexicalSnapshot.build(image, self.tokenizer)
            after = await self._stable(repository)
            if image.index_revision != after.index_revision:
                raise SnapshotError("Dense index changed before snapshot publication.")
            self.store.publish(ticket, snapshot, self.tokenizer)
        return snapshot

    async def reindex(self, prepared, indexer):
        # Opt-in wrapper around the unchanged production indexer. A future API
        # integration must route all participating writes through this lease.
        if (not isinstance(self.reader, QdrantImageReader) or indexer.store is not self.reader.store
                or embedding_identity(indexer.settings, indexer.provider.model_name) != self.reader.identity):
            raise SnapshotError("Indexer does not belong to the coordinated Dense store/identity.")
        intended = digest([{"chunk_id": c.chunk_id, "source": SourceMetadata.from_payload(asdict(c)).to_dict()}
                           for c in sorted(prepared.chunks, key=lambda c: c.chunk_id)])
        return await self.rebuild(prepared.scan.repository, expected_source_sha256=intended,
                                  mutate=lambda: indexer.index(prepared))

    async def use(self, repository, operation: Callable[[LexicalSnapshot], Awaitable[T]]) -> T:
        """Return only after both sides and the durable epoch still match.

        Consumers compute (but must not stream/publish) their results inside the
        callback. All lexical/Dense channels receive this same frozen snapshot.
        """
        scope = self.reader.scope(repository)
        read = self.store.load_ready(scope, self.tokenizer)
        try:
            before = await self._stable(repository)
        except SnapshotError:
            self.store.invalidate(scope, read.ticket, "dense_verification_failed")
            raise
        if before.index_revision != read.snapshot.index.index_revision:
            self.store.invalidate(scope, read.ticket)
            raise SnapshotError("Lexical snapshot is stale relative to the Dense repository.")
        self.store.assert_current(read)
        result = await operation(read.snapshot)
        try:
            after = await self._stable(repository)
        except SnapshotError:
            self.store.invalidate(scope, read.ticket, "dense_verification_failed")
            raise
        if after.index_revision != before.index_revision:
            self.store.invalidate(scope, read.ticket)
            raise SnapshotError("Dense repository changed during retrieval; discard the result.")
        self.store.assert_current(read)
        return result
