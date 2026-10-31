import asyncio
from dataclasses import replace
from pathlib import Path
import sys
import time

import pytest
from app.retrieval.candidates import RetrievalCandidate, SourceMetadata
from app.retrieval.rerank import RerankFailure, RerankPolicy, RerankResult, rerank, render_document
from app.retrieval.reranker_process import ProcessReranker
from app.retrieval.scope import ScopedCandidateBatch, SearchFilters

STUB = Path(__file__).parent/"fixtures/reranker_worker_stub.py"


def pool(count=3):
    candidates = tuple(RetrievalCandidate(f"00000000-0000-0000-0000-{i:012d}", "repo", "sha256:"+"a"*64,
        SourceMetadata(f"src/{i}.py", "python", 11, 12, "def refund():\n    return 1\n", symbol="refund", node_type="function_definition", chunking_strategy="ast", chunking_version="ast-v1"),
        {"dense": .1*i, "rrf": 1/(60+i)}, {"dense": i, "rrf": i}, ("原始 RefundService 问题",)) for i in range(1, count+1))
    return ScopedCandidateBatch("repo", "原始 RefundService 问题", "rrf", "sha256:"+"a"*64, 60, candidates, 1., filters=SearchFilters())


def worker(tmp_path, mode="ok"):
    return ProcessReranker(sys.executable, tmp_path/mode, device="cpu", worker_script=STUB)


def test_success_preserves_all_identity_coordinates_and_channel_provenance(tmp_path):
    async def run():
        adapter = worker(tmp_path)
        try:
            fused = pool(6)
            result = await rerank(fused, adapter, enabled=True, policy=RerankPolicy(candidate_limit=3), top_k=2)
            assert result.status == "reranked" and result.reason is None
            assert [c.chunk_id for c in result.ordered] == [c.chunk_id for c in fused.candidates[:3]][::-1]
            by_id = {c.chunk_id: c for c in fused.candidates}
            for candidate in result.ordered:
                old = by_id[candidate.chunk_id]
                assert candidate.source_metadata == old.source_metadata
                assert (candidate.repository, candidate.index_revision, candidate.matched_queries) == (old.repository, old.index_revision, old.matched_queries)
                assert all(candidate.channel_scores[k] == v for k, v in old.channel_scores.items())
                assert all(candidate.channel_ranks[k] == v for k, v in old.channel_ranks.items())
            assert len(result.final_results()) == 2
            assert result.to_dict()["unscored_tail_ids"] == [c.chunk_id for c in fused.candidates[3:]]
            assert RerankResult.from_dict(result.to_dict()) == result
            assert result.reply["query_sha256"] != result.reply["inputs"][0]["document_sha256"]
            assert "Path: src/1.py\nSymbol: refund\nContent:\n" in render_document(fused.candidates[0])
        finally:
            process = adapter.process
            await adapter.aclose()
            assert process.returncode is not None and adapter.process is None
    asyncio.run(run())


@pytest.mark.parametrize("mode,reason", [("hang", "timeout"), ("exit", "worker_failed"), ("malformed", "invalid_response"),
                                         ("wrong-id", "invalid_response"), ("unavailable", "model_unavailable"), ("input", "input_limit"), ("oom", "out_of_memory")])
def test_failure_falls_back_to_exact_original_pool_and_stops_worker(tmp_path, mode, reason):
    async def run():
        adapter = worker(tmp_path, mode)
        try:
            if mode != "unavailable":
                await adapter.start()
            process = adapter.process
            result = await rerank(pool(), adapter, enabled=True, policy=RerankPolicy(timeout_seconds=.1 if mode == "hang" else 10))
            assert result.status == "fallback" and result.reason == reason and result.reply is None
            assert result.ordered == result.fused.candidates
            assert [r.score for r in result.final_results()] == [c.channel_scores["rrf"] for c in result.fused.candidates]
            assert adapter.process is None
            if process:
                assert process.returncode is not None
        finally:
            await adapter.aclose()
    asyncio.run(run())


def test_busy_is_immediate_and_cancellation_reaps_worker(tmp_path):
    async def run():
        adapter = worker(tmp_path, "hang")
        await adapter.start()
        process = adapter.process
        task = asyncio.create_task(rerank(pool(), adapter, enabled=True))
        await asyncio.sleep(.02)
        started = time.perf_counter()
        result = await rerank(pool(), adapter, enabled=True)
        assert result.reason == "busy" and time.perf_counter()-started < .2
        assert adapter.process is process and process.returncode is None
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert process.returncode is not None and not adapter.busy
        await adapter.aclose()
    asyncio.run(run())


def test_startup_timeout_and_missing_runtime_are_bounded(tmp_path):
    async def run():
        adapter = worker(tmp_path, "startup-hang")
        adapter.startup_timeout = .1
        with pytest.raises(RerankFailure, match="timeout"):
            await adapter.start()
        assert adapter.process is None and not adapter.busy
        await adapter.aclose()
        missing = ProcessReranker(tmp_path/"missing-python.exe", tmp_path)
        result = await rerank(pool(), missing, enabled=True)
        assert result.reason == "model_unavailable" and missing.process is None
        await missing.aclose()
    asyncio.run(run())


def test_default_disabled_empty_and_closed_never_call_model(tmp_path):
    async def run():
        adapter = worker(tmp_path)
        assert (await rerank(pool(), adapter)).reason == "disabled"
        assert (await rerank(pool(0), adapter, enabled=True)).reason == "empty_pool"
        assert adapter.process is None
        await adapter.aclose()
        assert (await rerank(pool(), adapter, enabled=True)).reason == "closed"
    asyncio.run(run())


@pytest.mark.parametrize("kwargs", [{"candidate_limit": True}, {"candidate_limit": 61}, {"batch_size": 9}, {"batch_size": 0},
    {"max_length": 63}, {"max_length": 1025}, {"max_length": 128, "max_query_tokens": 125},
    {"timeout_seconds": float("nan")}, {"timeout_seconds": 121}, {"max_document_characters": 20001}])
def test_policy_hard_limits(kwargs):
    with pytest.raises(ValueError):
        RerankPolicy(**kwargs)


@pytest.mark.parametrize("field", ["fused_source", "ordered_source", "rank", "raw_score", "score", "model", "query", "document", "tokens", "calls", "tail", "scope"])
def test_serialized_tampering_is_rejected(tmp_path, field):
    async def run():
        adapter = worker(tmp_path)
        try:
            result = await rerank(pool(), adapter, enabled=True)
            value = result.to_dict()
            if field == "fused_source": value["fused"]["candidates"][0]["source_metadata"]["content"] = "changed\n"
            if field == "ordered_source": value["ordered"][0]["source_metadata"]["start_line"] = 8
            if field == "rank": value["ordered"][0]["channel_ranks"]["dense"] = 4
            if field == "raw_score": value["ordered"][0]["channel_scores"]["rrf"] = .99
            if field == "score": value["reply"]["scores"][0] = float("nan")
            if field == "model": value["reply"]["revision"] = "other"
            if field == "query": value["reply"]["query_sha256"] = "b"*64
            if field == "document": value["reply"]["inputs"][0]["document_sha256"] = "b"*64
            if field == "tokens": value["reply"]["inputs"][0]["input_ids"] *= 1000
            if field == "calls": value["reply"]["performance"]["forward_calls"] = 0
            if field == "tail": value["unscored_tail_ids"] = ["missing"]
            if field == "scope": value["fused"]["filters"]["language"] = "java"
            with pytest.raises((ValueError, TypeError)):
                RerankResult.from_dict(value)
        finally:
            await adapter.aclose()
    asyncio.run(run())


def test_unknown_revision_and_non_rrf_pool_rejected_before_scoring():
    with pytest.raises(ValueError):
        RerankResult(replace(pool(), channel="dense"), RerankPolicy(), 5, "fallback", "disabled", 0, None)
    with pytest.raises(ValueError):
        RerankResult(pool(), RerankPolicy(candidate_limit=2), 5, "fallback", "disabled", 0, None)


def test_close_during_inference_cancels_and_reaps_before_return(tmp_path):
    async def run():
        adapter = worker(tmp_path, "hang")
        await adapter.start()
        process = adapter.process
        task = asyncio.create_task(rerank(pool(), adapter, enabled=True))
        await asyncio.sleep(.02)
        await adapter.aclose()
        assert task.cancelled() and adapter.closed and not adapter.busy
        assert process.returncode is not None and adapter.process is None
    asyncio.run(run())


@pytest.mark.parametrize("drift", [False, True])
def test_snapshot_guard_covers_the_entire_rerank_and_rejects_drift(tmp_path, drift):
    from app.retrieval.hybrid import HybridRetriever, HybridSearchRequest
    from app.retrieval.rerank import RerankingRetriever
    from app.retrieval.snapshot_models import SnapshotError
    from app.retrieval.snapshot_store import SnapshotStore
    from app.retrieval.snapshots import SnapshotCoordinator
    from tests.test_hybrid_rrf import IMAGE, LIMITS, Channel
    from tests.test_lexical_snapshots import Reader, publish
    async def run():
        store = SnapshotStore(tmp_path/"snapshots.sqlite3")
        publish(store, IMAGE)
        reader = Reader(IMAGE)
        coordinator = SnapshotCoordinator(store, reader)
        hybrid = HybridRetriever(coordinator, {n: Channel(n, coordinator) for n in ("dense", "bm25")})
        class Failure:
            async def score(self, query, documents, policy):
                if drift:
                    from tests.test_exact_and_filters import repo_image, source
                    reader.images["demo"] = repo_image(sources=[source(content="void changed() {}\n")])
                raise RerankFailure("timeout")
        retriever = RerankingRetriever(hybrid, Failure())
        request = HybridSearchRequest("demo", "credit", limits=LIMITS)
        if drift:
            with pytest.raises(SnapshotError):
                await retriever.search(request, enabled=True)
        else:
            before, result = await retriever.search(request, enabled=True)
            assert result.reason == "timeout" and result.fused == before.fused
    asyncio.run(run())
