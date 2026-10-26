"""Rank-only reciprocal rank fusion over one verified repository/filter scope."""
from dataclasses import dataclass
import math
from time import perf_counter

from app.retrieval.candidates import RetrievalCandidate, validate_candidate_limit
from app.retrieval.scope import ScopedCandidateBatch
from app.retrieval.snapshot_models import SnapshotError

CHANNELS = ("dense", "bm25", "exact")


@dataclass(frozen=True)
class RRFParameters:
    k: int = 60

    def __post_init__(self):
        if type(self.k) is not int or not 1 <= self.k <= 10000:
            raise ValueError("RRF k must be an integer between 1 and 10000.")

    def to_dict(self):
        return {"version": "rrf-v1", "k": self.k, "rank_start": 1,
                "formula": "sum(1 / (k + original_channel_rank))",
                "tie_break": "descending RRF score, then ascending Chunk UUID"}


def merge_candidates(batches, parameters, limit):
    """Validate every input before truncation; return the pool and union count."""
    validate_candidate_limit(limit)
    if not isinstance(parameters, RRFParameters): raise TypeError("RRF needs RRFParameters.")
    batches = tuple(batches)
    if (not 1 <= len(batches) <= len(CHANNELS) or any(not isinstance(b, ScopedCandidateBatch) for b in batches)
            or len({b.channel for b in batches}) != len(batches) or any(b.channel not in CHANNELS for b in batches)):
        raise ValueError("RRF requires distinct scoped Dense/BM25/Exact batches.")
    first = batches[0]
    if first.index_revision is None: raise SnapshotError("Fusion requires a verified index revision, including empty batches.")
    pool = {}
    for batch in sorted(batches, key=lambda b: CHANNELS.index(b.channel)):
        if (batch.repository, batch.index_revision, batch.query, batch.filters) != (
                first.repository, first.index_revision, first.query, first.filters):
            raise SnapshotError("Cannot fuse different repositories, revisions, queries or filters.")
        for candidate in batch.candidates:
            if (set(candidate.channel_scores) != {batch.channel} or set(candidate.channel_ranks) != {batch.channel}
                    or candidate.matched_queries != (first.query,)):
                raise ValueError("Fusion inputs must be original single-channel query candidates.")
            key = (candidate.repository, candidate.index_revision, candidate.chunk_id)
            if key not in pool:
                pool[key] = [candidate.source_metadata, {}, {}]
            source, scores, ranks = pool[key]
            if source != candidate.source_metadata:
                raise SnapshotError("Identical Chunk identity has conflicting indexed source content or metadata.")
            scores[batch.channel] = candidate.channel_scores[batch.channel]
            ranks[batch.channel] = candidate.channel_ranks[batch.channel]
    scored = [(key, source, scores, ranks, math.fsum(1 / (parameters.k + ranks[ch]) for ch in CHANNELS if ch in ranks))
              for key, (source, scores, ranks) in pool.items()]
    scored.sort(key=lambda row: (-row[4], row[0][2]))
    candidates = tuple(RetrievalCandidate(key[2], key[0], key[1], source, scores | {"rrf": score}, ranks | {"rrf": rank}, (first.query,))
                       for rank, (key, source, scores, ranks, score) in enumerate(scored[:limit], 1))
    return candidates, len(pool)


def fuse_rrf(batches, *, parameters=None, candidate_limit=60):
    """Return a bounded pool; latency measures this fusion computation only."""
    started = perf_counter()
    batches = tuple(batches)
    candidates, _ = merge_candidates(batches, parameters if parameters is not None else RRFParameters(), candidate_limit)
    first = batches[0]
    return ScopedCandidateBatch(first.repository, first.query, "rrf", first.index_revision, candidate_limit, candidates,
                                round((perf_counter() - started) * 1000, 3), filters=first.filters)
