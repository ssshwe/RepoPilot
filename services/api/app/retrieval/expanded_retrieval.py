"""Merge query variants within channels, then give each channel one RRF vote."""
import asyncio
from dataclasses import dataclass, field, replace
import math
from time import perf_counter

from app.retrieval.candidates import RetrievalCandidate, validate_candidate_limit, to_retrieval_results
from app.retrieval.exact import ExactCandidateBatch, ExactMatch
from app.retrieval.expansion import ExpansionPlan, ExpansionPolicy, QueryExpander, normalized
from app.retrieval.hybrid import HybridSearchRequest
from app.retrieval.rerank import RerankPolicy, RerankResult, rerank
from app.retrieval.rrf import CHANNELS, RRFParameters
from app.retrieval.scope import ScopedCandidateBatch, ScopedSearchRequest, SearchFilters
from app.retrieval.snapshot_models import SnapshotError

MERGE_VERSION = "best-query-rank-v1"


@dataclass(frozen=True)
class ExpandedCandidateBatch(ScopedCandidateBatch):
    """An original request context with truthful per-candidate matched queries.

    A candidate found only by a variant must NOT claim to match the original.
    This explicit subtype relaxes that one legacy batch assumption; its query
    remains the unchanged question used for Context and Rerank.
    """
    queries: tuple[str, ...] = field(default=(), kw_only=True)

    def __post_init__(self):
        queries = tuple(self.queries)
        if not 1 <= len(queries) <= 3 or queries[0] != self.query or len(set(queries)) != len(queries):
            raise ValueError("Expanded batch needs the original and at most two unique variants.")
        # Reuse all old structural/source/scope validation using a temporary
        # request-context projection. It is never exposed or stored as evidence.
        ScopedCandidateBatch(self.repository, self.query, self.channel, self.index_revision, self.candidate_limit,
            tuple(replace(c, matched_queries=(self.query,)) for c in self.candidates), self.retrieval_latency_ms, filters=self.filters)
        if self.index_revision is None or self.channel not in {*CHANNELS, "rrf"}:
            raise ValueError("Expanded batches need a verified supported channel.")
        for candidate in self.candidates:
            if candidate.matched_queries != tuple(q for q in queries if q in candidate.matched_queries):
                raise ValueError("Expanded provenance contains an unknown/reordered query.")
        object.__setattr__(self, "queries", queries)

    def to_dict(self):
        return super().to_dict() | {"queries": list(self.queries), "merge_version": MERGE_VERSION}

    @classmethod
    def from_dict(cls, value):
        fields = dict(value)
        queries = tuple(fields.pop("queries"))
        version = fields.pop("merge_version")
        fingerprint = fields.pop("scope_fingerprint")
        filters = SearchFilters(**fields.pop("filters"))
        candidates = tuple(RetrievalCandidate.from_dict(c) for c in fields.pop("candidates"))
        result = cls(**fields, candidates=candidates, filters=filters, queries=queries)
        if version != MERGE_VERSION or fingerprint != filters.fingerprint(result.repository) or result.to_dict() != value:
            raise ValueError("Expanded serialized scope or merge contract differs.")
        return result


def parse_batch(value):
    return ExactCandidateBatch.from_dict(value) if value["channel"] == "exact" else ScopedCandidateBatch.from_dict(value)


def merge_channel(batches, queries, candidate_limit):
    validate_candidate_limit(candidate_limit)
    batches, queries = tuple(batches), tuple(queries)
    if not batches or len(batches) != len(queries):
        raise ValueError("A merged channel needs one complete batch per active query.")
    first = batches[0]
    if first.channel not in CHANNELS or first.index_revision is None:
        raise ValueError("Invalid expanded channel input.")
    pool = {}
    for query_index, (query, batch) in enumerate(zip(queries, batches, strict=True)):
        if (not isinstance(batch, ScopedCandidateBatch) or isinstance(batch, ExpandedCandidateBatch)
                or (batch.repository, batch.index_revision, batch.filters, batch.channel, batch.query, batch.candidate_limit) !=
                   (first.repository, first.index_revision, first.filters, first.channel, query, candidate_limit)):
            raise SnapshotError("Variant channels have incompatible request/index/filter scope.")
        for candidate in batch.candidates:
            if (set(candidate.channel_scores) != {first.channel} or set(candidate.channel_ranks) != {first.channel}
                    or candidate.matched_queries != (query,)):
                raise ValueError("Within-channel merge needs original per-query channel ranks.")
            if candidate.chunk_id not in pool:
                pool[candidate.chunk_id] = []
            supports = pool[candidate.chunk_id]
            if supports and supports[0][1].source_metadata != candidate.source_metadata:
                raise SnapshotError("Variant Chunk identity has conflicting code or coordinates.")
            supports.append((query_index, candidate))
    ranked = []
    for identity, supports in pool.items():
        winner = min(supports, key=lambda row: (row[1].channel_ranks[first.channel], row[0]))
        ranked.append((winner[1].channel_ranks[first.channel], winner[0], identity, winner[1], supports))
    ranked.sort(key=lambda row: row[:3])
    candidates = tuple(replace(winner, channel_ranks={first.channel: rank},
        matched_queries=tuple(q for q in queries if any(c.matched_queries == (q,) for _,c in supports)))
        for rank, (_,_,_,winner,supports) in enumerate(ranked[:candidate_limit], 1))
    return ExpandedCandidateBatch(first.repository, queries[0], first.channel, first.index_revision, candidate_limit, candidates,
        sum(b.retrieval_latency_ms for b in batches), filters=first.filters, queries=queries)


def fuse_channels(channels, *, parameters=None, candidate_limit=60):
    parameters = parameters or RRFParameters()
    validate_candidate_limit(candidate_limit)
    channels = tuple(channels)
    if (not isinstance(parameters, RRFParameters) or not channels
            or tuple(b.channel for b in channels) not in (CHANNELS[:2], CHANNELS)
            or any(not isinstance(b, ExpandedCandidateBatch) for b in channels)):
        raise ValueError("Expanded fusion needs one merged batch per channel, in canonical order.")
    first, pool = channels[0], {}
    for batch in channels:
        if (batch.repository, batch.index_revision, batch.query, batch.filters, batch.queries) != (
                first.repository, first.index_revision, first.query, first.filters, first.queries):
            raise SnapshotError("Expanded fusion cannot combine request/index/filter scopes.")
        for candidate in batch.candidates:
            if set(candidate.channel_scores) != {batch.channel} or set(candidate.channel_ranks) != {batch.channel}:
                raise ValueError("Duplicate or already fused channel votes are forbidden.")
            if candidate.chunk_id not in pool:
                pool[candidate.chunk_id] = [candidate.source_metadata, {}, {}, set()]
            source, scores, ranks, queries = pool[candidate.chunk_id]
            if source != candidate.source_metadata:
                raise SnapshotError("Expanded fusion Chunk identity has conflicting source metadata.")
            scores[batch.channel], ranks[batch.channel] = candidate.channel_scores[batch.channel], candidate.channel_ranks[batch.channel]
            queries.update(candidate.matched_queries)
    rows = [(math.fsum(1/(parameters.k+ranks[ch]) for ch in CHANNELS if ch in ranks), identity, source, scores, ranks, queries)
            for identity,(source,scores,ranks,queries) in pool.items()]
    rows.sort(key=lambda row: (-row[0], row[1]))
    candidates = tuple(RetrievalCandidate(identity, first.repository, first.index_revision, source, scores|{"rrf": score}, ranks|{"rrf": rank},
        tuple(q for q in first.queries if q in queries)) for rank,(score,identity,source,scores,ranks,queries) in enumerate(rows[:candidate_limit],1))
    return ExpandedCandidateBatch(first.repository, first.query, "rrf", first.index_revision, candidate_limit, candidates, 0., filters=first.filters, queries=first.queries)


def parse_rerank(value):
    result = RerankResult(ExpandedCandidateBatch.from_dict(value["fused"]), RerankPolicy(**value["policy"]), value["top_k"],
        value["status"], value["reason"], value["latency_ms"], value["reply"])
    if result.to_dict() != value:
        raise ValueError("Expanded rerank identities, original question, scores or coordinates differ.")
    return result


@dataclass(frozen=True)
class ExpandedRetrievalResult:
    request: HybridSearchRequest
    plan: ExpansionPlan
    used_queries: tuple
    attempts: tuple
    batches: tuple
    reason: str | None
    merged: tuple
    fused: ExpandedCandidateBatch
    reranked: RerankResult
    retrieval_latency_ms: float

    def __post_init__(self):
        if not isinstance(self.request, HybridSearchRequest) or not isinstance(self.plan, ExpansionPlan) or self.plan.original != self.request.query:
            raise ValueError("Expanded result needs the unchanged request/question.")
        names = tuple(b.channel for b in self.merged)
        if names not in (CHANNELS[:2], CHANNELS):
            raise ValueError("Expanded result channel inventory differs.")
        unit = sum(getattr(self.request.limits, n) for n in names)
        allowed = self.plan.queries[:max(1, self.plan.policy.max_candidate_slots//unit)]
        if self.used_queries != allowed and not (self.reason in {"variant_error", "variant_timeout"} and self.used_queries == (self.request.query,)):
            raise ValueError("Executed variant queries differ from the declared budget/fallback.")
        if self.reason not in {None, "candidate_budget", "empty_scope", "variant_error", "variant_timeout"}:
            raise ValueError("Unknown retrieval expansion reason.")
        if unit > self.plan.policy.max_candidate_slots or len(self.attempts) > len(allowed)*len(names):
            raise ValueError("Expanded recall call/candidate reservation budget exceeded.")
        expected_calls = [(q,n) for q in allowed for n in names]
        if self.reason == "empty_scope": expected_calls = expected_calls[:len(names)]
        if [(a["query"],a["channel"]) for a in self.attempts] != expected_calls[:len(self.attempts)]:
            raise ValueError("Recall attempts were repeated, reordered or associated with another query.")
        if self.reason in {"variant_error", "variant_timeout"}:
            expected_status = "failed" if self.reason == "variant_error" else "timeout"
            if (len(self.attempts) <= len(names) or self.attempts[-1]["status"] != expected_status
                    or any(a["status"] != "completed" for a in self.attempts[:-1])):
                raise ValueError("Variant fallback lacks its failed/interrupted call receipt.")
        elif len(self.attempts) != len(expected_calls) or any(a["status"] != "completed" for a in self.attempts):
            raise ValueError("Successful expansion must include every admitted call exactly once.")
        successful = [a for a in self.attempts if a["status"] == "completed"]
        if len(successful) != len(self.batches) or any(a["status"] not in {"completed", "failed", "timeout"} for a in self.attempts):
            raise ValueError("Raw recall attempt accounting differs.")
        for batch, attempt in zip(self.batches, successful, strict=True):
            if ((batch.repository, batch.query, batch.channel, batch.filters, batch.candidate_limit) !=
                    (self.request.repository, attempt["query"], attempt["channel"], self.request.filters, getattr(self.request.limits, batch.channel))
                    or batch.index_revision != self.fused.index_revision):
                raise SnapshotError("Raw expanded batch scope differs.")
            if batch.channel == "exact" and (not isinstance(batch, ExactCandidateBatch) or batch.match != (self.request.exact_match or ExactMatch.infer(batch.query))):
                raise ValueError("Exact selector changed while expanding the question.")
        selected = tuple(b for b in self.batches if b.query in self.used_queries)
        if len(selected) != len(self.used_queries)*len(names):
            raise ValueError("An active variant is missing a complete channel group.")
        expected = tuple(merge_channel([b for b in selected if b.channel == n], self.used_queries, getattr(self.request.limits,n)) for n in names)
        if self.merged != expected or self.fused.to_dict() != fuse_channels(expected, candidate_limit=self.request.limits.fusion).to_dict():
            raise ValueError("Within-channel merge or one-vote RRF cannot be recomputed.")
        if self.reranked.fused != self.fused or self.reranked.top_k != self.request.limits.top_k:
            raise ValueError("Final rerank is not bound to the unchanged original question and expanded pool.")
        if type(self.retrieval_latency_ms) not in (int,float) or not math.isfinite(self.retrieval_latency_ms) or self.retrieval_latency_ms < 0:
            raise ValueError("Invalid expanded retrieval latency.")

    def final_results(self):
        return self.reranked.final_results()

    def to_dict(self):
        return {"request": self.request.to_dict(), "plan": self.plan.to_dict(), "used_queries": list(self.used_queries),
            "attempts": list(self.attempts), "batches": [b.to_dict() for b in self.batches], "reason": self.reason,
            "merged": [b.to_dict() for b in self.merged], "fused": self.fused.to_dict(), "reranked": self.reranked.to_dict(),
            "retrieval_latency_ms": self.retrieval_latency_ms,
            "reserved_candidate_slots": len({a['query'] for a in self.attempts})*sum(getattr(self.request.limits,n) for n in (b.channel for b in self.merged)),
            "returned_candidate_slots": sum(len(b.candidates) for b in self.batches), "results": [r.model_dump() for r in self.final_results()]}

    @classmethod
    def from_dict(cls, value):
        result = cls(HybridSearchRequest.from_dict(value["request"]), ExpansionPlan.from_dict(value["plan"]), tuple(value["used_queries"]),
            tuple(value["attempts"]), tuple(parse_batch(b) for b in value["batches"]), value["reason"],
            tuple(ExpandedCandidateBatch.from_dict(b) for b in value["merged"]), ExpandedCandidateBatch.from_dict(value["fused"]),
            parse_rerank(value["reranked"]), value["retrieval_latency_ms"])
        if result.to_dict() != value:
            raise ValueError("Expanded serialized results/costs/provenance differ.")
        return result


class ExpandedRetriever:
    def __init__(self, coordinator, channels, *, expander=None, reranker=None):
        if set(channels) not in ({"dense", "bm25"}, set(CHANNELS)) or any(c.name != n or c.coordinator is not coordinator for n,c in channels.items()):
            raise ValueError("Expansion channels must share one snapshot coordinator.")
        self.coordinator, self.channels = coordinator, dict(channels)
        self.expander, self.reranker = expander or QueryExpander(), reranker

    async def search(self, request, *, enabled=False, method="deterministic", policy=None, rerank_enabled=False, rerank_policy=None):
        if not isinstance(request, HybridSearchRequest):
            raise TypeError("Expansion retrieval requires HybridSearchRequest.")
        policy = policy or ExpansionPolicy()
        names = tuple(n for n in CHANNELS if n in self.channels)
        unit = sum(getattr(request.limits,n) for n in names)
        if not isinstance(policy, ExpansionPolicy) or unit > policy.max_candidate_slots:
            raise ValueError("Candidate budget must fit one complete original-query group.")
        if rerank_enabled and self.reranker is None:
            raise ValueError("Explicit reranking requires an adapter.")
        started = perf_counter()
        async def retrieve(snapshot):
            indexed = {c.chunk_id:c.source for c in snapshot.index.chunks}
            batches, attempts = [], []
            async def recall(query):
                for name in names:
                    attempt = {"query": query, "channel": name, "status": "pending"}
                    attempts.append(attempt)
                    try:
                        variant = replace(request, query=query)
                        batch = await self.channels[name].recall(variant.channel_request(name))
                        if (not isinstance(batch, ScopedCandidateBatch) or batch.channel != name or batch.query != query
                                or batch.repository != request.repository or batch.filters != request.filters
                                or batch.index_revision != snapshot.index.index_revision
                                or any(indexed.get(c.chunk_id) != c.source_metadata for c in batch.candidates)):
                            raise SnapshotError("Variant retrieval returned code outside the guarded indexed snapshot.")
                        batches.append(batch)
                        attempt["status"] = "completed"
                    except asyncio.CancelledError:
                        attempt["status"] = "timeout"
                        raise
                    except BaseException:
                        attempt["status"] = "failed"
                        raise
            await recall(request.query)
            eligible = any(request.filters.matches(c.source) for c in snapshot.index.chunks)
            expand = enabled and eligible and policy.max_candidate_slots//unit > 1
            plan = await self.expander.expand(request.query, enabled=expand, method=method, policy=policy)
            allowed = plan.queries[:policy.max_candidate_slots//unit]
            reason = "empty_scope" if enabled and not eligible else "candidate_budget" if enabled and not expand and eligible else None
            if len(allowed) < len(plan.queries): reason = "candidate_budget"
            try:
                async with asyncio.timeout(policy.variant_timeout_seconds):
                    for query in allowed[1:]:
                        await recall(query)
                used = allowed
            except SnapshotError:
                raise
            except asyncio.CancelledError:
                raise
            except TimeoutError:
                used, reason = (request.query,), "variant_timeout"
            except Exception:
                used, reason = (request.query,), "variant_error"
            merged = tuple(merge_channel([b for b in batches if b.channel == n and b.query in used], used, getattr(request.limits,n)) for n in names)
            fused = fuse_channels(merged, candidate_limit=request.limits.fusion)
            ranked = await rerank(fused, self.reranker, enabled=rerank_enabled,
                policy=rerank_policy or RerankPolicy(candidate_limit=request.limits.rerank), top_k=request.limits.top_k)
            return plan, used, tuple(attempts), tuple(batches), reason, merged, fused, ranked
        values = await self.coordinator.use(request.repository, retrieve)
        return ExpandedRetrievalResult(request, *values, round((perf_counter()-started)*1000,3))
