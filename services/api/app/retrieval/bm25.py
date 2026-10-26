"""Repository-scoped Okapi BM25 over the shared immutable code snapshot."""
from collections import Counter, OrderedDict
from dataclasses import dataclass
import math
from time import perf_counter
from types import MappingProxyType

from app.retrieval.candidates import CandidateBatch, CandidateSearchRequest, RetrievalCandidate, validate_candidate_limit
from app.retrieval.snapshot_models import LexicalSnapshot, SnapshotError


@dataclass(frozen=True)
class BM25Parameters:
    k1: float = 1.5
    b: float = 0.75

    def __post_init__(self):
        if (type(self.k1) not in (int, float) or not math.isfinite(self.k1) or not 0 < self.k1 <= 100
                or type(self.b) not in (int, float) or not math.isfinite(self.b) or not 0 <= self.b <= 1):
            raise ValueError("BM25 requires finite 0 < k1 <= 100 and 0 <= b <= 1.")

    def to_dict(self):
        return {"version": "bm25-okapi-v1", "k1": self.k1, "b": self.b,
                "idf": "log(1 + (N - df + 0.5) / (df + 0.5))", "query_tf": "one contribution per unique query token",
                "tie_break": "descending score, then ascending Chunk UUID", "zero_overlap": "return no candidate"}


class BM25Index:
    """One repository's statistics, bounded by its validated snapshot limits."""
    def __init__(self, snapshot: LexicalSnapshot, parameters=None):
        if parameters is not None and not isinstance(parameters, BM25Parameters):
            raise TypeError("BM25 parameters must be BM25Parameters.")
        self.snapshot, self.parameters = snapshot, parameters or BM25Parameters()
        self.lengths = tuple(len(words) for words in snapshot.tokens)
        self.document_count = len(self.lengths)
        self.average_length = math.fsum(self.lengths) / self.document_count if self.document_count else 0.
        postings = {}
        for document, words in enumerate(snapshot.tokens):
            for term, frequency in Counter(words).items():
                postings.setdefault(term, []).append((document, frequency))
        self.postings = MappingProxyType({term: tuple(items) for term, items in postings.items()})

    def _contribution(self, term, document, frequency):
        df = len(self.postings.get(term, ()))
        if not df or not self.average_length:
            return None
        idf = math.log1p((self.document_count - df + 0.5) / (df + 0.5))
        normalization = 1 - self.parameters.b + self.parameters.b * self.lengths[document] / self.average_length
        score = idf * frequency * (self.parameters.k1 + 1) / (frequency + self.parameters.k1 * normalization)
        return {"term": term, "tf": frequency, "df": df, "idf": idf, "document_length": self.lengths[document],
                "average_document_length": self.average_length, "length_normalization": normalization, "contribution": score}

    def search(self, query_tokens, limit=30):
        validate_candidate_limit(limit)
        contributions = {}
        for term in sorted(set(query_tokens)):
            for document, frequency in self.postings.get(term, ()):
                contributions.setdefault(document, []).append(self._contribution(term, document, frequency)["contribution"])
        ranked = [(document, math.fsum(parts)) for document, parts in contributions.items()]
        ranked = [(document, score) for document, score in ranked if math.isfinite(score) and score > 0]
        ranked.sort(key=lambda pair: (-pair[1], self.snapshot.index.chunks[pair[0]].chunk_id))
        return ranked[:limit]

    def explain(self, query_tokens, chunk_id):
        document = next((i for i, chunk in enumerate(self.snapshot.index.chunks) if chunk.chunk_id == chunk_id), None)
        if document is None:
            raise ValueError("Chunk is absent from this repository's BM25 snapshot.")
        result = []
        for term in sorted(set(query_tokens)):
            frequency = next((frequency for index, frequency in self.postings.get(term, ()) if index == document), 0)
            if frequency:
                result.append(self._contribution(term, document, frequency))
        return result


class BM25CandidateChannel:
    name = "bm25"

    def __init__(self, coordinator, parameters=None, *, cache_size=8):
        if type(cache_size) is not int or not 1 <= cache_size <= 64:
            raise ValueError("BM25 cache_size must be between 1 and 64.")
        if parameters is not None and not isinstance(parameters, BM25Parameters):
            raise TypeError("BM25 parameters must be BM25Parameters.")
        self.coordinator, self.parameters = coordinator, parameters or BM25Parameters()
        self.tokenizer = coordinator.tokenizer
        self.cache_size, self._cache = cache_size, OrderedDict()

    def _index(self, snapshot):
        # Snapshot revision includes backend/repo/config/code/Tokenizer identity;
        # a new generation can never reuse another corpus's statistics.
        key = (snapshot.revision, self.parameters.k1, self.parameters.b)
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        index = BM25Index(snapshot, self.parameters)
        self._cache[key] = index
        if len(self._cache) > self.cache_size:
            self._cache.popitem(last=False)
        return index

    async def recall(self, request: CandidateSearchRequest) -> CandidateBatch:
        started = perf_counter()
        query_tokens = self.tokenizer.tokenize_query(request.query)
        async def retrieve(snapshot):
            if snapshot.index.scope.repository != request.repository:
                raise SnapshotError("BM25 snapshot repository differs from the query.")
            index = self._index(snapshot)
            candidates = tuple(RetrievalCandidate(
                chunk_id=snapshot.index.chunks[document].chunk_id, repository=request.repository,
                index_revision=snapshot.index.index_revision, source_metadata=snapshot.index.chunks[document].source,
                channel_scores={self.name: score}, channel_ranks={self.name: rank}, matched_queries=(request.query,),
            ) for rank, (document, score) in enumerate(index.search(query_tokens, request.candidate_limit), 1))
            return snapshot.index.index_revision, candidates
        revision, candidates = await self.coordinator.use(request.repository, retrieve)
        # Include post-retrieval consistency checks in the measured channel time.
        return CandidateBatch(request.repository, request.query, self.name, revision, request.candidate_limit,
                              candidates, round((perf_counter() - started) * 1000, 3))
