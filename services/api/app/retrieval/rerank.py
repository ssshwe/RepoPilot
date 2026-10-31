"""Optional, bounded reranking of an existing RRF pool; no recall or generation."""
import asyncio
from dataclasses import asdict, dataclass, replace
import hashlib
import math
from time import perf_counter
from typing import Protocol

from app.retrieval.candidates import to_retrieval_results
from app.retrieval.scope import ScopedCandidateBatch
from app.retrieval.snapshot_models import valid_digest

DOCUMENT_VERSION = "path-symbol-content-v1"
MODEL_ID = "BAAI/bge-reranker-v2-m3"
MODEL_REVISION = "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e"
REASONS = frozenset({"disabled", "empty_pool", "busy", "timeout", "model_unavailable", "worker_failed", "invalid_response", "input_limit", "closed", "out_of_memory"})


def text_sha(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class RerankPolicy:
    candidate_limit: int = 30
    batch_size: int = 4
    max_length: int = 512
    max_query_tokens: int = 256
    max_document_characters: int = 16000
    timeout_seconds: float = 30.0

    def __post_init__(self):
        for name, low, high in (("candidate_limit", 1, 60), ("batch_size", 1, 8), ("max_length", 64, 1024),
                                ("max_query_tokens", 1, 512), ("max_document_characters", 128, 20000)):
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"Invalid rerank {name}.")
        if self.max_query_tokens > self.max_length - 4:
            raise ValueError("The complete original query must leave room for the passage.")
        if type(self.timeout_seconds) not in (int, float) or not math.isfinite(self.timeout_seconds) or not 0.001 <= self.timeout_seconds <= 120:
            raise ValueError("Invalid rerank timeout.")

    def to_dict(self):
        return asdict(self)


def render_document(candidate):
    source = candidate.source_metadata
    return f"Path: {source.path}\nSymbol: {source.symbol or ''}\nContent:\n{source.content}"


class RerankFailure(Exception):
    def __init__(self, reason):
        if reason not in REASONS:
            raise ValueError("Unknown safe degradation reason.")
        self.reason = reason
        super().__init__(reason)


class RerankerAdapter(Protocol):
    async def score(self, query: str, documents: tuple[str, ...], policy: RerankPolicy) -> dict: ...


def validate_reply(reply, query, documents, policy):
    """Validate the complete response before accepting a single new rank."""
    if not isinstance(reply, dict) or set(reply) != {"model", "revision", "manifest_sha256", "query_sha256", "policy", "scores", "inputs", "performance"}:
        raise ValueError("Invalid reranker response fields.")
    if (reply["model"] != MODEL_ID or reply["revision"] != MODEL_REVISION or reply["query_sha256"] != text_sha(query)
            or reply["policy"] != policy.to_dict()):
        raise ValueError("Reranker request/model association differs.")
    valid_digest("sha256:" + reply["manifest_sha256"])
    scores, inputs = reply["scores"], reply["inputs"]
    if not isinstance(scores, list) or not isinstance(inputs, list) or len(scores) != len(documents) or len(inputs) != len(documents):
        raise ValueError("Reranker lost or added a candidate.")
    if any(type(s) not in (int, float) or not math.isfinite(s) for s in scores):
        raise ValueError("Reranker scores must be finite logits.")
    for document, item in zip(documents, inputs, strict=True):
        capped = document[:policy.max_document_characters]
        if set(item) != {"document_sha256", "capped_sha256", "original_characters", "capped_characters", "query_tokens", "document_tokens", "used_document_tokens", "input_ids"}:
            raise ValueError("Invalid reranker input trace fields.")
        if (item["document_sha256"] != text_sha(document) or item["capped_sha256"] != text_sha(capped)
                or item["original_characters"] != len(document) or item["capped_characters"] != len(capped)):
            raise ValueError("Reranker passage association differs.")
        for name in ("query_tokens", "document_tokens", "used_document_tokens"):
            if type(item[name]) is not int or item[name] < 0:
                raise ValueError("Invalid reranker token counts.")
        ids = item["input_ids"]
        if (not 1 <= item["query_tokens"] <= policy.max_query_tokens or item["used_document_tokens"] > item["document_tokens"]
                or not isinstance(ids, list) or not 4 <= len(ids) <= policy.max_length
                or len(ids) != item["query_tokens"] + item["used_document_tokens"] + 4
                or any(type(i) is not int or not 0 <= i <= 250001 for i in ids)):
            raise ValueError("Invalid bounded pair token trace.")
        q = item["query_tokens"]
        if ids[0] != 0 or ids[q+1:q+3] != [2, 2] or ids[-1] != 2:
            raise ValueError("Invalid XLM-R pair boundaries.")
        if inputs[0]["input_ids"][1:1+inputs[0]["query_tokens"]] != ids[1:1+q]:
            raise ValueError("The original query tokens changed across candidates.")
    perf = reply["performance"]
    required = {"inference_ms", "tokenization_ms", "rss_bytes", "cuda_peak_allocated_bytes", "cuda_peak_reserved_bytes", "forward_calls", "pairs", "device", "dtype"}
    if set(perf) != required or perf["device"] not in {"cpu", "cuda"} or perf["dtype"] not in {"float32", "float16"}:
        raise ValueError("Invalid performance trace.")
    for key in required - {"device", "dtype"}:
        if type(perf[key]) not in (int, float) or not math.isfinite(perf[key]) or perf[key] < 0:
            raise ValueError("Invalid performance value.")
    if perf["pairs"] != len(documents) or perf["forward_calls"] != math.ceil(len(documents)/policy.batch_size):
        raise ValueError("Invalid model call accounting.")


@dataclass(frozen=True)
class RerankResult:
    fused: ScopedCandidateBatch
    policy: RerankPolicy
    top_k: int
    status: str
    reason: str | None
    latency_ms: float
    reply: dict | None

    def __post_init__(self):
        if not isinstance(self.fused, ScopedCandidateBatch) or self.fused.channel != "rrf" or self.fused.index_revision is None:
            raise ValueError("Rerank requires an existing versioned scoped RRF pool.")
        if not isinstance(self.policy, RerankPolicy) or type(self.top_k) is not int or not 1 <= self.top_k <= min(20, self.policy.candidate_limit):
            raise ValueError("TopK must fit the scored candidate budget.")
        if type(self.latency_ms) not in (int, float) or not math.isfinite(self.latency_ms) or self.latency_ms < 0:
            raise ValueError("Invalid rerank latency.")
        if self.status == "reranked":
            if self.reason is not None or not self.fused.candidates:
                raise ValueError("Invalid successful rerank.")
            validate_reply(self.reply, self.fused.query, self.documents, self.policy)
        elif self.status == "fallback":
            if self.reason not in REASONS or self.reply is not None:
                raise ValueError("Fallback must contain a safe explicit reason, with no partial scores.")
            if (self.reason == "empty_pool") != (not self.fused.candidates):
                raise ValueError("Empty pool status differs.")
        else:
            raise ValueError("Unknown rerank status.")

    @property
    def documents(self):
        return tuple(render_document(c) for c in self.fused.candidates[:self.policy.candidate_limit])

    @property
    def ordered(self):
        if self.status == "fallback":
            return self.fused.candidates
        selected = self.fused.candidates[:self.policy.candidate_limit]
        # Equal logits retain fusion order, then UUID. No mixing of incomparable
        # raw RRF and cross-encoder scores; the unscored tail stays separate.
        order = sorted(range(len(selected)), key=lambda i: (-self.reply["scores"][i], i, selected[i].chunk_id))
        return tuple(replace(selected[i], channel_scores={**selected[i].channel_scores, "rerank": self.reply["scores"][i]},
                             channel_ranks={**selected[i].channel_ranks, "rerank": rank}) for rank, i in enumerate(order, 1))

    def final_results(self):
        return to_retrieval_results(self.ordered, repository=self.fused.repository, index_revision=self.fused.index_revision,
            top_k=self.top_k, score_channel="rerank" if self.status == "reranked" else "rrf")

    def to_dict(self):
        return {"fused": self.fused.to_dict(), "policy": self.policy.to_dict(), "top_k": self.top_k, "status": self.status,
                "reason": self.reason, "latency_ms": self.latency_ms, "reply": self.reply,
                "document_version": DOCUMENT_VERSION, "score_type": "cross_encoder_logit" if self.status == "reranked" else "rrf",
                "ordered": [c.to_dict() for c in self.ordered],
                "unscored_tail_ids": [c.chunk_id for c in self.fused.candidates[self.policy.candidate_limit:]] if self.status == "reranked" else [],
                "results": [r.model_dump() for r in self.final_results()]}

    @classmethod
    def from_dict(cls, value):
        result = cls(ScopedCandidateBatch.from_dict(value["fused"]), RerankPolicy(**value["policy"]),
                     value["top_k"], value["status"], value["reason"], value["latency_ms"], value["reply"])
        if result.to_dict() != value:
            raise ValueError("Rerank serialized identities, coordinates, scores or ranks differ.")
        return result


async def rerank(fused, adapter: RerankerAdapter, *, policy=None, top_k=5, enabled=False):
    policy = policy or RerankPolicy()
    started = perf_counter()
    # Validate the complete input, including unscored tail, before model work.
    empty = not fused.candidates
    base = RerankResult(fused, policy, top_k, "fallback", "empty_pool" if empty else "disabled", 0., None)
    if empty or not enabled:
        return base
    try:
        # Adapter owns a hard execution deadline and cleanup, not a detached
        # thread that could keep consuming GPU resources after timeout.
        reply = await adapter.score(fused.query, base.documents, policy)
        validate_reply(reply, fused.query, base.documents, policy)
        return RerankResult(fused, policy, top_k, "reranked", None, round((perf_counter()-started)*1000, 3), reply)
    except asyncio.CancelledError:
        raise
    except RerankFailure as exc:
        reason = exc.reason
    except Exception:
        reason = "invalid_response"
    return RerankResult(fused, policy, top_k, "fallback", reason, round((perf_counter()-started)*1000, 3), None)


class RerankingRetriever:
    """Internal opt-in wrapper: retain the snapshot lease through model scoring.

    Snapshot errors abort the whole request; they are never converted into a
    fallback containing stale code. Model failures alone use the original RRF.
    """
    def __init__(self, hybrid, adapter):
        self.hybrid, self.adapter = hybrid, adapter

    async def search(self, request, *, enabled=False, policy=None):
        from app.retrieval.snapshot_models import SnapshotError
        async def retrieve(snapshot):
            hybrid = await self.hybrid.search(request)
            if hybrid.fused.index_revision != snapshot.index.index_revision:
                raise SnapshotError("Rerank input is outside the guarded index.")
            result = await rerank(hybrid.fused, self.adapter, policy=policy, enabled=enabled, top_k=request.limits.top_k)
            return hybrid, result
        return await self.hybrid.coordinator.use(request.repository, retrieve)
