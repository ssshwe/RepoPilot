"""Real BGE reranking of the frozen Task 8 pool, with model-free receipt replay."""
import argparse
import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/"services/api"), str(ROOT)]
from app.citations import CitationValidator
from app.context import ContextBuilder
from app.core.config import get_settings
from app.retrieval.hybrid import HybridResult
from app.retrieval.rerank import MODEL_ID, MODEL_REVISION, REASONS, RerankFailure, RerankPolicy, RerankResult, rerank, render_document, validate_reply
from app.retrieval.reranker_process import ProcessReranker
from evaluation.retrieval.evaluate import require
from evaluation.stage4.evaluate import grouped_summary
from evaluation.stage4.evaluate_hybrid import checked_inputs, replay as replay_hybrid
from evaluation.stage4.metrics import percentile, covered
from evaluation.stage4.state import file_reference, read_json, write_new, safe_configuration
from evaluation.stage4.download_reranker import FILES

HYBRID = ROOT/"evaluation/reports/stage4_hybrid_20261027_task8.json"
CORE = ("services/api/app/retrieval/rerank.py", "services/api/app/retrieval/reranker_process.py", "services/api/app/retrieval/reranker_worker.py", "evaluation/stage4/evaluate_rerank.py")
POLICY = RerankPolicy()
SWEEP = [(length, batch) for length in (128, 512, 1024) for batch in (1, 4, 8)]
DEFINITIONS = {
    "formal": "Original 52 Task 8 questions and complete fused pools; score first 30 with the unchanged original question; TopK 5; no new recall, embedding, generation or holdout queries",
    "inputs": "path-symbol-content-v1, 16000 document codepoints, preserve complete original query up to 256 tokens; only passage truncates; pair max 512 tokens including 4 XLM-R special tokens",
    "scores": "Unnormalized cross-encoder logits; descending order; equal logits keep original RRF order; never mix logits with unscored RRF tail",
    "context": "Actual ContextBuilder output, maximum 5 Sources / 16000 characters; not sent to a generation model",
    "latency": "Rerank-stage wall time including transport/tokenization/model/validation; explicit cold-load measured separately; excludes original retrieval and snapshot guard time",
    "failures": "All 52 outcomes retained; model failures use exact original fused order; no retry or cherry-picked response; cancellation propagates; index drift aborts",
    "sweep": "Nine predeclared length/batch probes on wallet-symbol first 30 candidates; deterministic repeated first passage forces truncation; performance-only synthetic passages, excluded from quality metrics",
    "default": "Disabled during Task 10; development comparison informs recommendation, final default requires Task 14 frozen holdout evaluation",
}


def artifact_directory(path):
    return path.parent/(path.stem+"_artifacts")


def ensure_new_output(path):
    require(not path.exists() and not artifact_directory(path).exists(), "Existing rerank report/artifacts must not be overwritten")


def measured(case, result, config):
    from evaluation.stage4.metrics import case_metrics
    results = result.final_results()
    context = ContextBuilder(max_context_chunks=config["max_context_chunks"], max_context_characters=config["max_context_characters"]).build(case["repository"], results)
    CitationValidator().validate(case["repository"], [s.source_id for s in context.sources], context=context, retrieval_results=results)
    retrieval = {"repository": case["repository"], "query": case["query"], "top_k": 5,
                 "results": [r.model_dump() for r in results], "retrieval_latency_ms": result.latency_ms}
    return {"id": case["id"], "retrieval": retrieval, "context": json.loads(json.dumps(asdict(context))),
            "metrics": case_metrics(case, retrieval["results"], [asdict(s) for s in context.sources])}


def comparison(before, after):
    if before["at_k"] is None:
        return {"positive": False, "empty_before": before["negative"]["empty_result"], "empty_after": after["negative"]["empty_result"]}
    fields = ("range_recall", "line_coverage", "symbol_recall", "evidence_mrr", "all_evidence")
    return {"positive": True, "at_5": {name: {"before": before["at_k"]["5"][name], "after": after["at_k"]["5"][name]} for name in fields},
            "context": {name: {"before": before["context"][name], "after": after["context"][name]} for name in ("range_recall", "line_coverage", "all_evidence")}}


def evidence_diagnostics(case, result, context):
    original = [c.source_metadata.to_dict() for c in result.fused.candidates]
    budget = original[:result.policy.candidate_limit]
    final = [r.model_dump() for r in result.final_results()]
    ranks = {c.chunk_id: i for i,c in enumerate(result.ordered, 1)}
    rows = []
    for evidence in case["evidence"]:
        if not covered(original, evidence): stage = "outside_fused_pool"
        elif result.status == "reranked" and not covered(budget, evidence): stage = "outside_rerank_budget"
        elif not covered(final, evidence): stage = "after_final_top5"
        elif not covered(context["sources"], evidence): stage = "context_missing"
        else: stage = "covered"
        chunks = [{"chunk_id": c.chunk_id, "path": c.source_metadata.path, "start_line": c.source_metadata.start_line,
                   "end_line": c.source_metadata.end_line, "symbol": c.source_metadata.symbol, "fused_rank": i,
                   "rerank_rank": ranks.get(c.chunk_id) if result.status == "reranked" else None}
                  for i,c in enumerate(result.fused.candidates, 1) if c.source_metadata.path == evidence["path"]
                  and c.source_metadata.start_line <= evidence["end_line"] and c.source_metadata.end_line >= evidence["start_line"]]
        rows.append({"required_range": evidence, "stage": stage, "chunks": chunks})
    return rows


def check_row(case, row, original, config, manifest_sha):
    require(row["id"] == case["id"] == original["id"], "Rerank case association differs")
    hybrid = HybridResult.from_dict(original["hybrid"])
    result = RerankResult.from_dict(row["rerank"])
    require(result.fused == hybrid.fused and result.policy == POLICY and result.top_k == 5, "Original fused pool, question or fixed rerank policy changed")
    if result.reply:
        require(result.reply["manifest_sha256"] == manifest_sha, "Rerank used another model snapshot")
    require(row["measured"] == measured(case, result, config), "Rerank Context/metrics/result cannot be recomputed")
    # This is the frozen offline strategy receipt, not an HTTP
    # RetrievalSearchResponse. Full ancestry replay verifies source bytes;
    # immutable fused-pool equality plus measured() verifies actual coordinates,
    # ContextBuilder Sources, Citation mapping and every metric here.
    require(row["comparison"] == comparison(original["strategies"]["hybrid"]["metrics"], row["measured"]["metrics"]), "Rerank before/after comparison differs")
    require(row["evidence_diagnostics"] == evidence_diagnostics(case, result, row["measured"]["context"]), "Rerank evidence/chunk diagnosis differs")
    return result


def performance_summary(rows):
    successful = [r for r in rows if r["rerank"]["status"] == "reranked"]
    latencies = [r["rerank"]["latency_ms"] for r in successful]
    replies = [r["rerank"]["reply"] for r in successful]
    traces = [i for reply in replies for i in reply["inputs"]]
    return {"successful_queries": len(successful), "empty_queries": sum(r["rerank"]["reason"] == "empty_pool" for r in rows),
        "degraded_queries": sum(r["rerank"]["status"] == "fallback" and r["rerank"]["reason"] != "empty_pool" for r in rows),
        "latency_ms": {name: percentile(latencies, p) for name, p in (("p50", .5), ("p95", .95), ("max", 1.))} if latencies else None,
        "pairs": sum(reply["performance"]["pairs"] for reply in replies), "forward_calls": sum(reply["performance"]["forward_calls"] for reply in replies),
        "truncated_pairs": sum(i["document_tokens"] > i["used_document_tokens"] or i["original_characters"] > i["capped_characters"] for i in traces),
        "maximum_pair_tokens": max((len(i["input_ids"]) for i in traces), default=0),
        "max_observed_rss_bytes": max((r["performance"]["rss_bytes"] for r in replies), default=0),
        "peak_cuda_allocated_bytes": max((r["performance"]["cuda_peak_allocated_bytes"] for r in replies), default=0),
        "peak_cuda_reserved_bytes": max((r["performance"]["cuda_peak_reserved_bytes"] for r in replies), default=0)}


def sweep_documents(original):
    hybrid = HybridResult.from_dict(original["hybrid"])
    documents = [render_document(c) for c in hybrid.fused.candidates[:30]]
    documents[0] += ("\n"+documents[0])*20
    return hybrid.fused.query, tuple(documents)


async def run_faults(adapter, python, model, artifacts, original):
    query, documents = sweep_documents(original)
    if adapter.process is None:
        recovery = await adapter.start()
        write_new(artifacts/"worker-recovery-before-faults.json", recovery)
    second = ProcessReranker(python, model)
    try:
        try:
            await second.start()
            raise AssertionError("Second worker unexpectedly acquired the same model lease")
        except Exception as exc:
            from app.retrieval.rerank import RerankFailure
            require(isinstance(exc, RerankFailure) and exc.reason == "busy", "Real process-level concurrency limit failed")
        require(adapter.process is not None and adapter.process.returncode is None, "Busy rejection killed the active worker")
        busy = {"reason": "busy", "second_worker_reaped": second.process is None, "first_worker_alive": True}
    finally:
        await second.aclose()
    process = adapter.process
    started = perf_counter()
    timeout = await rerank(HybridResult.from_dict(original["hybrid"]).fused, adapter, enabled=True, policy=RerankPolicy(timeout_seconds=.001))
    require(timeout.reason == "timeout" and process.returncode is not None and adapter.process is None, "Real timeout failed to reap the worker")
    timeout_row = {"rerank": timeout.to_dict(), "wall_ms": round((perf_counter()-started)*1000, 3), "worker_exit_code": process.returncode,
                   "interrupted_requests": 1, "completed_forward_calls": None, "note": "Interrupted model execution is unacknowledged; do not claim zero work or completed pairs"}
    write_new(artifacts/"real-timeout.json", timeout_row)
    missing = ProcessReranker(python, model/"intentionally-absent-model")
    try:
        missing_result = await rerank(HybridResult.from_dict(original["hybrid"]).fused, missing, enabled=True)
        require(missing_result.reason == "model_unavailable" and missing.process is None, "Missing model fallback differs")
    finally:
        await missing.aclose()
    write_new(artifacts/"real-missing-model.json", missing_result.to_dict())
    return {"cross_process_busy": busy, "timeout": file_reference(artifacts/"real-timeout.json"),
            "missing_model": file_reference(artifacts/"real-missing-model.json"), "worker_stopped_after_timeout": True}


async def evaluate(output, python, model):
    output = output.resolve()
    ensure_new_output(output)
    await replay_hybrid(HYBRID)
    dense, _, _, cases, _, _ = checked_inputs()
    original = read_json(HYBRID)
    config = safe_configuration(get_settings())
    require(config == original["configuration"], "Frozen configuration drift")
    artifacts = artifact_directory(output)
    artifacts.mkdir(parents=True)
    manifest = read_json(model/"manifest.json")
    require(manifest["model"] == MODEL_ID and manifest["revision"] == MODEL_REVISION and set(manifest["files"]) == set(FILES), "Pinned model manifest differs")
    manifest_sha = file_reference(model/"manifest.json")["sha256"]
    # Preserve original bytes: the worker reports the original manifest digest.
    with (artifacts/"model-manifest.json").open("xb") as stream:
        stream.write((model/"manifest.json").read_bytes())
    adapter = ProcessReranker(python, model)
    rows, sweep = [], []
    try:
        ready = await adapter.start()
        require(ready["manifest_sha256"] == manifest_sha, "Ready worker model association differs")
        write_new(artifacts/"worker-ready.json", ready)
        for ordinal, (case, old) in enumerate(zip(cases, original["cases"], strict=True), 1):
            fused = HybridResult.from_dict(old["hybrid"]).fused
            result = await rerank(fused, adapter, policy=POLICY, enabled=True)
            row = {"id": case["id"], "rerank": result.to_dict(), "measured": measured(case, result, config)}
            row["comparison"] = comparison(old["strategies"]["hybrid"]["metrics"], row["measured"]["metrics"])
            row["evidence_diagnostics"] = evidence_diagnostics(case, result, row["measured"]["context"])
            path = artifacts/"cases"/f"{ordinal:02}-{case['id']}.json"
            write_new(path, row)
            check_row(case, row, old, config, manifest_sha)
            rows.append(row)
            print(f"{ordinal}/52 {case['id']}: {result.status}, {result.latency_ms:.1f}ms", flush=True)
        selected = next(r for r in original["cases"] if r["id"] == "wallet-symbol")
        query, documents = sweep_documents(selected)
        for length, batch in SWEEP:
            policy = RerankPolicy(max_length=length, max_query_tokens=min(256, length-4), batch_size=batch)
            started = perf_counter()
            try:
                reply = await adapter.score(query, documents, policy)
                reason = None
            except RerankFailure as exc:
                reply, reason = None, exc.reason
            item = {"max_length": length, "batch_size": batch, "wall_ms": round((perf_counter()-started)*1000, 3), "reply": reply, "reason": reason}
            write_new(artifacts/"sweep"/f"length-{length}-batch-{batch}.json", item)
            sweep.append(item)
            print(f"Performance length={length}, batch={batch}: {item['wall_ms']}ms", flush=True)
        faults = await run_faults(adapter, python, model, artifacts, selected)
    finally:
        await adapter.aclose()
    completed = sum(r["rerank"]["status"] == "reranked" for r in rows)
    report = {"schema_version": 1, "task": "Stage 4 / Task 10", "status": "complete", "created_at": datetime.now(timezone.utc).isoformat(),
        "hybrid_reference": file_reference(HYBRID), "definitions": DEFINITIONS, "policy": POLICY.to_dict(), "configuration": config,
        "implementations": {name: file_reference(ROOT/name) for name in CORE},
        "model_manifest": file_reference(artifacts/"model-manifest.json"), "worker_ready": file_reference(artifacts/"worker-ready.json"),
        "case_receipts": [file_reference(artifacts/"cases"/f"{i:02}-{r['id']}.json") for i,r in enumerate(rows,1)],
        "cases": rows, "summary": grouped_summary([r["measured"] for r in rows], cases), "before_summary": original["summary"]["hybrid"],
        "performance": performance_summary(rows), "sweep": sweep, "faults": faults, "default_enabled": False, "holdout_queried": False,
        "calls": {"completed_formal_rerank_requests": completed, "sweep_requests": len(sweep), "completed_sweep_requests": sum(r["reply"] is not None for r in sweep),
                  "completed_forward_calls": sum(r["rerank"]["reply"]["performance"]["forward_calls"] for r in rows if r["rerank"]["reply"])
                      + sum(r["reply"]["performance"]["forward_calls"] for r in sweep if r["reply"]),
                  "interrupted_requests": 1, "unacknowledged_formal_or_sweep_requests": sum(r["rerank"]["status"] == "fallback" and r["rerank"]["reason"] != "empty_pool" for r in rows)+sum(r["reply"] is None for r in sweep),
                  "interrupted_forward_calls": "unknown; worker killed before acknowledgement",
                  "new_embedding_calls": 0, "generation_calls": 0}, "worker_closed": adapter.process is None}
    write_new(output, report)
    print(json.dumps({"performance": report["performance"], "summary": report["summary"]["split"], "report": str(output)}, ensure_ascii=False), flush=True)


async def replay(path):
    report = read_json(path)
    require(report["schema_version"] == 1 and report["task"] == "Stage 4 / Task 10" and report["status"] == "complete", "Completed rerank report required")
    require(report["hybrid_reference"] == file_reference(HYBRID) and report["implementations"] == {n: file_reference(ROOT/n) for n in CORE}, "Rerank report/implementation association drift")
    require(report["definitions"] == DEFINITIONS and report["policy"] == POLICY.to_dict() and report["holdout_queried"] is False
            and report["default_enabled"] is False and report["worker_closed"] is True, "Rerank fixed scope/default/cleanup differs")
    await replay_hybrid(HYBRID)
    _, _, _, cases, _, _ = checked_inputs()
    original = read_json(HYBRID)
    require(report["configuration"] == original["configuration"], "Rerank configuration drift")
    for ref in (report["model_manifest"], report["worker_ready"], *report["case_receipts"], report["faults"]["timeout"], report["faults"]["missing_model"]):
        require(file_reference(ROOT/ref["path"]) == ref, "Rerank artifact fingerprint drift")
    manifest = read_json(ROOT/report["model_manifest"]["path"])
    require(manifest["model"] == MODEL_ID and manifest["revision"] == MODEL_REVISION and set(manifest["files"]) == set(FILES), "Rerank model manifest differs")
    manifest_sha = report["model_manifest"]["sha256"]
    require(read_json(ROOT/report["worker_ready"]["path"])["manifest_sha256"] == manifest_sha, "Ready model association differs")
    require([r["id"] for r in report["cases"]] == [c["id"] for c in cases] and len(report["case_receipts"]) == len(cases), "Rerank case inventory differs")
    for case, row, old, ref in zip(cases, report["cases"], original["cases"], report["case_receipts"], strict=True):
        require(read_json(ROOT/ref["path"]) == row, "Raw rerank receipt differs")
        check_row(case, row, old, report["configuration"], manifest_sha)
    require(report["summary"] == grouped_summary([r["measured"] for r in report["cases"]], cases)
            and report["before_summary"] == original["summary"]["hybrid"] and report["performance"] == performance_summary(report["cases"]), "Rerank summary differs")
    selected = next(r for r in original["cases"] if r["id"] == "wallet-symbol")
    query, documents = sweep_documents(selected)
    require([(r["max_length"], r["batch_size"]) for r in report["sweep"]] == SWEEP, "Predeclared performance sweep differs")
    for row in report["sweep"]:
        policy = RerankPolicy(max_length=row["max_length"], max_query_tokens=min(256, row["max_length"]-4), batch_size=row["batch_size"])
        if row["reply"] is not None:
            require(row["reason"] is None, "Successful sweep cannot contain a degradation reason")
            validate_reply(row["reply"], query, documents, policy)
            require(row["reply"]["manifest_sha256"] == manifest_sha, "Sweep model snapshot differs")
        else:
            require(row["reason"] in REASONS, "Failed performance probe needs explicit safe reason")
        receipt = artifact_directory(path)/"sweep"/f"length-{row['max_length']}-batch-{row['batch_size']}.json"
        require(read_json(receipt) == row, "Sweep raw receipt differs")
    faults = report["faults"]
    require(faults["cross_process_busy"] == {"reason": "busy", "second_worker_reaped": True, "first_worker_alive": True}
            and faults["worker_stopped_after_timeout"] is True, "Real worker concurrency proof missing")
    timeout = read_json(ROOT/faults["timeout"]["path"])
    fallback = RerankResult.from_dict(timeout["rerank"])
    require(fallback.reason == "timeout" and fallback.fused == HybridResult.from_dict(selected["hybrid"]).fused
            and timeout["worker_exit_code"] is not None and timeout["interrupted_requests"] == 1 and timeout["completed_forward_calls"] is None, "Real timeout/reaping proof differs")
    missing = RerankResult.from_dict(read_json(ROOT/faults["missing_model"]["path"]))
    require(missing.reason == "model_unavailable" and missing.fused == fallback.fused, "Missing model proof differs")
    expected_calls = {"completed_formal_rerank_requests": sum(r["rerank"]["status"] == "reranked" for r in report["cases"]),
        "sweep_requests": len(SWEEP), "completed_sweep_requests": sum(r["reply"] is not None for r in report["sweep"]),
        "completed_forward_calls": report["performance"]["forward_calls"]+sum(r["reply"]["performance"]["forward_calls"] for r in report["sweep"] if r["reply"]),
        "interrupted_requests": 1, "unacknowledged_formal_or_sweep_requests": report["performance"]["degraded_queries"]+sum(r["reply"] is None for r in report["sweep"]),
        "interrupted_forward_calls": "unknown; worker killed before acknowledgement", "new_embedding_calls": 0, "generation_calls": 0}
    require(report["calls"] == expected_calls, "Actual rerank call accounting differs")
    return {"offline_replay_passed": True, "cases": len(cases), "completed_rerank_requests": expected_calls["completed_formal_rerank_requests"],
            "performance_probes": len(SWEEP), "new_embedding_calls": 0, "generation_calls": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-only", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT/"evaluation/reports/stage4_rerank_20261031_task10.json")
    parser.add_argument("--python", type=Path, default=ROOT/".tools/reranker-venv/Scripts/python.exe")
    parser.add_argument("--model-directory", type=Path, default=ROOT/".tools/reranker-model")
    args = parser.parse_args()
    if args.verify_only:
        print(asyncio.run(replay(args.verify_only.resolve())))
    else:
        asyncio.run(evaluate(args.output, args.python, args.model_directory))


if __name__ == "__main__":
    main()
