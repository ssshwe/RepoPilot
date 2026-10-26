"""Scoped wrappers preserve legacy channels and filter before candidate truncation."""
import hashlib
import math
from time import perf_counter

from qdrant_client import models

from app.core.exceptions import RetrievalResultError
from app.embedding.identity import embedding_identity
from app.retrieval.bm25 import BM25CandidateChannel
from app.retrieval.candidates import CandidateSearchRequest, RetrievalCandidate
from app.retrieval.dense import DenseCandidateChannel
from app.retrieval.scope import ScopedCandidateBatch, SearchFilters, scoped_request
from app.retrieval.snapshots import QdrantImageReader
from app.retrieval.vector_store import QdrantVectorStore


class SnapshotFilteredStore(QdrantVectorStore):
    """Whitelist snapshot IDs in the Qdrant query filter, before its limit."""
    def __init__(self, original, repository, allowed_ids):
        super().__init__(original._client, original.collection)
        self.repository, self.allowed_ids = repository, tuple(allowed_ids)
        if not self.allowed_ids:
            raise ValueError("Do not query Qdrant for an empty eligible snapshot set.")

    def repository_filter(self, repository):
        if repository != self.repository:
            raise ValueError("Filtered Dense store cannot change repository scope.")
        base = QdrantVectorStore.repository_filter(repository)
        return models.Filter(must=[*base.must, models.HasIdCondition(has_id=list(self.allowed_ids))])


class FilteredDenseChannel:
    name = "dense"

    def __init__(self, provider, settings, coordinator):
        reader = coordinator.reader
        backend = "sha256:" + hashlib.sha256(settings.qdrant_url.strip().rstrip("/").encode()).hexdigest()
        if (not isinstance(reader, QdrantImageReader) or embedding_identity(settings, provider.model_name) != reader.identity
                or backend != reader.backend_id):
            raise ValueError("Filtered Dense must use the coordinated Qdrant backend and embedding identity.")
        self.provider, self.settings, self.coordinator = provider, settings, coordinator

    async def recall(self, request):
        request = scoped_request(request)
        started = perf_counter()
        async def retrieve(snapshot):
            eligible = {chunk.chunk_id: chunk for chunk in snapshot.index.chunks if request.filters.matches(chunk.source)}
            if not eligible:
                return snapshot.index.index_revision, ()
            original = self.coordinator.reader.store
            store = original if request.filters == SearchFilters() else SnapshotFilteredStore(original, request.repository, sorted(eligible))
            base = await DenseCandidateChannel(self.provider, store, self.settings, index_revision=snapshot.index.index_revision).recall(
                CandidateSearchRequest(request.repository, request.query, request.candidate_limit))
            if any(c.chunk_id not in eligible or c.source_metadata != eligible[c.chunk_id].source for c in base.candidates):
                raise RetrievalResultError("Dense returned code outside the filtered snapshot.")
            return snapshot.index.index_revision, base.candidates
        revision, candidates = await self.coordinator.use(request.repository, retrieve)
        return ScopedCandidateBatch(request.repository, request.query, self.name, revision, request.candidate_limit,
                                    candidates, round((perf_counter() - started) * 1000, 3), filters=request.filters)


class FilteredBM25Channel(BM25CandidateChannel):
    """Keep repository-wide IDF/length statistics; restrict eligibility before TopK."""
    async def recall(self, request):
        request = scoped_request(request)
        started = perf_counter()
        words = self.tokenizer.tokenize_query(request.query)
        async def retrieve(snapshot):
            index = self._index(snapshot)
            eligible = {i for i, chunk in enumerate(snapshot.index.chunks) if request.filters.matches(chunk.source)}
            contributions = {}
            for term in sorted(set(words)):
                for document, frequency in index.postings.get(term, ()):
                    if document in eligible:
                        contributions.setdefault(document, []).append(index._contribution(term, document, frequency)["contribution"])
            ranked = [(document, math.fsum(parts)) for document, parts in contributions.items()]
            ranked.sort(key=lambda pair: (-pair[1], snapshot.index.chunks[pair[0]].chunk_id))
            candidates = tuple(RetrievalCandidate(
                snapshot.index.chunks[document].chunk_id, request.repository, snapshot.index.index_revision,
                snapshot.index.chunks[document].source, {self.name: score}, {self.name: rank}, (request.query,),
            ) for rank, (document, score) in enumerate(ranked[:request.candidate_limit], 1))
            return snapshot.index.index_revision, candidates
        revision, candidates = await self.coordinator.use(request.repository, retrieve)
        return ScopedCandidateBatch(request.repository, request.query, self.name, revision, request.candidate_limit,
                                    candidates, round((perf_counter() - started) * 1000, 3), filters=request.filters)
