"""Independent BM25 spike and frozen-corpus comparison; no model regeneration."""
import argparse
import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "services/api"), str(ROOT)]

from app.citations import CitationValidator
from app.context import ContextBuilder
from app.core.config import get_settings
from app.retrieval.bm25 import BM25CandidateChannel, BM25Index, BM25Parameters
from app.retrieval.candidates import CandidateBatch, CandidateSearchRequest, SourceMetadata, to_retrieval_results
from app.retrieval.snapshot_models import SnapshotScope
from app.retrieval.snapshot_store import SnapshotStore
from app.retrieval.snapshots import QdrantImageReader, SnapshotCoordinator
from app.retrieval.tokenizer import CodeTokenizer
from app.retrieval.vector_store import QdrantVectorStore, create_qdrant_client
from evaluation.retrieval.evaluate import require
from evaluation.stage4.dataset import load_dataset
from evaluation.stage4.evaluate import grouped_summary
from evaluation.stage4.metrics import case_metrics, DEFINITIONS
from evaluation.stage4.state import file_reference, full_snapshot, read_json, safe_configuration, verify_unchanged, write_new
from evaluation.stage4.verify_snapshots import FrozenReader, replay as replay_snapshots

SNAPSHOT_REPORT = ROOT / "evaluation/reports/stage4_snapshots_20261026_task5.json"
DENSE_REPORT = ROOT / "evaluation/reports/stage4_retrieval_20261013_task2.json"
DEFINITIONS_BM25 = DEFINITIONS | {"latency": "Snapshot-only BM25 channel time including SQLite/Tokenizer validation and frozen-image guards; excludes embedding and Qdrant, not comparable to Dense service latency"}


def inputs(snapshot_report=SNAPSHOT_REPORT, dense_report=DENSE_REPORT):
    replay_snapshots(snapshot_report)
    snapshots = read_json(snapshot_report)
    dense = read_json(dense_report)
    data, digest = load_dataset(ROOT / dense["dataset"]["path"], ROOT / "workspaces")
    require(digest == dense["dataset_sha256"] and not dense["holdout_queried"], "Dense development reference mismatch")
    store = SnapshotStore(ROOT / snapshots["database"]["path"], read_only=True)
    scopes = {p["scope"]["repository"]: SnapshotScope(**p["scope"]) for p in snapshots["phases"] if p["name"].startswith("frozen-")}
    images = {repo: store.load_ready(scope).snapshot.index for repo, scope in scopes.items()}
    cases = [c for c in data["cases"] if c["split"] in {"regression", "dev"}]
    require([r["id"] for r in dense["cases"]] == [c["id"] for c in cases], "Dense cases dropped or reordered")
    for case, row in zip(cases, dense["cases"], strict=True):
        chunks = {c.chunk_id: c for c in images[case["repository"]].chunks}
        require(all(r["chunk_id"] in chunks and SourceMetadata.from_payload(r) == chunks[r["chunk_id"]].source for r in row["retrieval"]["results"]),
                "Dense and BM25 references use different indexed code")
    return snapshots, dense, data, cases, store, images


def final_result(batch, top_k=5):
    return to_retrieval_results(batch.candidates, repository=batch.repository, index_revision=batch.index_revision,
                                top_k=top_k, score_channel="bm25")


def delta(current, reference):
    if current["at_k"] is None:
        return {"negative_empty_result_changed": current["negative"]["empty_result"] != reference["negative"]["empty_result"]}
    fields = ("file_hit", "file_recall", "symbol_recall", "range_recall", "line_coverage", "evidence_mrr", "all_evidence")
    return {key: current["at_k"]["5"][key] - reference["at_k"]["5"][key]
            for key in fields if current["at_k"]["5"][key] is not None}


def verify_row(case, row, snapshot, parameters, config, dense_row):
    batch = CandidateBatch.from_dict(row["batch"])
    require(batch.repository == case["repository"] and batch.query == case["query"] and batch.channel == "bm25"
            and batch.candidate_limit == 30 and batch.index_revision == snapshot.index.index_revision, "BM25 request/revision mismatch")
    tokenizer, index = CodeTokenizer(), BM25Index(snapshot, parameters)
    words = tokenizer.tokenize_query(case["query"])
    ranked = index.search(words, 30)
    require(row["query_tokens"] == words and len(batch.candidates) == len(ranked), "BM25 query/rank count changed")
    explanations = {}
    for candidate, (document, score) in zip(batch.candidates, ranked, strict=True):
        chunk = snapshot.index.chunks[document]
        require(candidate.chunk_id == chunk.chunk_id and candidate.source_metadata == chunk.source
                and candidate.channel_scores == {"bm25": score} and candidate.matched_queries == (case["query"],), "BM25 score/source provenance mismatch")
        detail = index.explain(words, chunk.chunk_id)
        require(math.fsum(t["contribution"] for t in detail) == score, "BM25 explanation does not sum to score")
        explanations[chunk.chunk_id] = detail
    require(row["explanations"] == explanations, "BM25 TF/IDF explanation changed")
    selected = final_result(batch)
    require(row["retrieval"] == {"repository": case["repository"], "query": case["query"], "strategy": "bm25", "top_k": 5,
            "collection": snapshot.index.scope.collection, "retrieval_latency_ms": batch.retrieval_latency_ms,
            "results": [r.model_dump() for r in selected]}, "Final BM25 adapter/request changed")
    built = ContextBuilder(max_context_chunks=config["max_context_chunks"], max_context_characters=config["max_context_characters"]).build(case["repository"], selected)
    require(json.loads(json.dumps(asdict(built))) == row["context"], "BM25 actual Context changed")
    CitationValidator().validate(case["repository"], [s.source_id for s in built.sources], context=built, retrieval_results=selected)
    measured = case_metrics(case, row["retrieval"]["results"], row["context"]["sources"])
    require(row["metrics"] == measured and row["dense_metrics"] == dense_row["metrics"]
            and row["delta_at_5"] == delta(measured, dense_row["metrics"]), "BM25 comparison metrics changed")


async def live_smoke(output):
    settings = get_settings()
    client = create_qdrant_client(settings)
    rows = []
    try:
        before = await full_snapshot(client)
        reader = QdrantImageReader(QdrantVectorStore(client, settings.qdrant_collection), settings)
        store = SnapshotStore(ROOT / ".tools" / ("stage4-task6-" + uuid4().hex) / "smoke.sqlite3")
        coordinator = SnapshotCoordinator(store, reader)
        empty_repository = "task6-empty-" + uuid4().hex[:12]
        for repo in ("demo-repository", empty_repository):
            image = await reader.capture(repo)
            await coordinator.rebuild(repo, expected_source_sha256=image.source_sha256)
        channel = BM25CandidateChannel(coordinator)
        for kind, repo, query in (("identifier", "demo-repository", "RefundService.refund"),
                                  ("no_overlap", "demo-repository", "𠀃𠀄𠀅"),
                                  ("no_terms", "demo-repository", "?!💳"),
                                  ("empty_corpus", empty_repository, "WalletService.credit")):
            result = await channel.recall(CandidateSearchRequest(repo, query))
            receipt = {"kind": kind, "batch": result.to_dict()}
            write_new(output.parent / (output.stem + "-" + kind + ".json"), receipt)
            if kind == "identifier":
                require(any(c.source_metadata.symbol == "RefundService.refund" for c in result.candidates), "Live identifier method not recalled")
            else:
                require(not result.candidates, "Live empty/zero-overlap query returned unrelated candidates")
            rows.append(receipt)
            print("Live smoke " + kind + ": " + str(len(result.candidates)) + " candidates", flush=True)
        verify_unchanged(before, await full_snapshot(client), "All persistent collections during read-only BM25 smoke")
        write_new(output, {"kind": "actual read-only Qdrant guards plus BM25; no embedding", "cases": rows,
                           "collections_unchanged": True, "embedding_calls": 0, "generation_calls": 0})
    finally:
        await client.close()


async def evaluate(args):
    output = args.output.resolve()
    artifacts = output.parent / (output.stem + "_artifacts")
    require(not output.exists() and not artifacts.exists(), "Existing BM25 report/artifacts must not be overwritten")
    _, dense, _, cases, store, images = inputs()
    config = safe_configuration(get_settings())
    require(config == dense["configuration"], "Dense reference configuration drift")
    parameters = BM25Parameters(args.k1, args.b)
    coordinator = SnapshotCoordinator(store, FrozenReader(images))
    channel = BM25CandidateChannel(coordinator, parameters)
    artifacts.mkdir(parents=True)
    rows = []
    for ordinal, (case, dense_row) in enumerate(zip(cases, dense["cases"], strict=True), 1):
        try:
            if args.reuse_cases is not None:
                original_path = args.reuse_cases / f"{ordinal:02}-{case['id']}.json"
                row = read_json(original_path)
                snapshot = store.load_ready(images[case["repository"]].scope).snapshot
                require(row["snapshot_revision"] == snapshot.revision, "Reused BM25 corpus identity differs")
                verify_row(case, row, snapshot, parameters, config, dense_row)
                write_new(artifacts / "cases" / f"{ordinal:02}-{case['id']}.json", row)
                rows.append(row)
                print(f"{ordinal}/{len(cases)} {case['id']}: original verified receipt retained", flush=True)
                continue
            batch = await channel.recall(CandidateSearchRequest(case["repository"], case["query"], 30))
            snapshot = store.load_ready(images[case["repository"]].scope).snapshot
            selected = final_result(batch)
            context = ContextBuilder(max_context_chunks=config["max_context_chunks"], max_context_characters=config["max_context_characters"]).build(case["repository"], selected)
            words = CodeTokenizer().tokenize_query(case["query"])
            index = BM25Index(snapshot, parameters)
            measured = case_metrics(case, [r.model_dump() for r in selected], [asdict(s) for s in context.sources])
            row = {"id": case["id"], "snapshot_revision": snapshot.revision, "batch": batch.to_dict(), "query_tokens": words,
                   "retrieval": {"repository": case["repository"], "query": case["query"], "strategy": "bm25", "top_k": 5,
                                 "collection": snapshot.index.scope.collection, "retrieval_latency_ms": batch.retrieval_latency_ms,
                                 "results": [r.model_dump() for r in selected]},
                   "explanations": {c.chunk_id: index.explain(words, c.chunk_id) for c in batch.candidates},
                   "context": json.loads(json.dumps(asdict(context))), "metrics": measured, "dense_metrics": dense_row["metrics"],
                   "delta_at_5": delta(measured, dense_row["metrics"])}
            verify_row(case, row, snapshot, parameters, config, dense_row)
            write_new(artifacts / "cases" / f"{ordinal:02}-{case['id']}.json", row)
            rows.append(row)
            print(f"{ordinal}/{len(cases)} {case['id']}: {len(batch.candidates)} BM25 candidates", flush=True)
        except Exception as exc:
            write_new(artifacts / "cases" / f"{ordinal:02}-{case['id']}-error.json", {"id": case["id"], "error_type": type(exc).__name__, "status": "aborted; case not dropped"})
            raise
    live = None
    if args.live_smoke:
        path = artifacts / "live-smoke.json"
        await live_smoke(path)
        live = file_reference(path)
    report = {"schema_version": 1, "task": "Stage 4 / Task 6", "status": "complete", "created_at": datetime.now(timezone.utc).isoformat(),
              "snapshot_report": file_reference(SNAPSHOT_REPORT), "dense_report": file_reference(DENSE_REPORT), "parameters": parameters.to_dict(),
              "configuration": config, "implementation": file_reference(ROOT / "services/api/app/retrieval/bm25.py"),
              "definitions": DEFINITIONS_BM25, "cases": rows, "summary": grouped_summary(rows, cases), "dense_summary": dense["summary"],
              "live_smoke": live, "holdout_queried": False, "calls": {"embedding_calls": 0, "generation_calls": 0},
              "case_execution": {"mode": "reused exact verified first-run receipts" if args.reuse_cases else "fresh snapshot-only queries",
                  "source_receipts": [file_reference(args.reuse_cases / f"{i:02}-{case['id']}.json") for i, case in enumerate(cases, 1)] if args.reuse_cases else []},
              "corpora": {repo: {"snapshot_revision": store.load_ready(image.scope).snapshot.revision,
                  "documents": len(image.chunks), "average_length": BM25Index(store.load_ready(image.scope).snapshot).average_length}
                  for repo, image in images.items()},
              "note": "Quality comparison on identical frozen indexed code. Snapshot-only BM25 latency and real Dense service latency have different I/O boundaries."}
    write_new(output, report)
    print("Complete: 52 BM25/Dense comparisons; zero embedding/generation calls; " + str(output))


def replay(path):
    report = read_json(path)
    require(report["schema_version"] == 1 and report["task"] == "Stage 4 / Task 6" and report["status"] == "complete"
            and report["holdout_queried"] is False and report["definitions"] == DEFINITIONS_BM25, "Completed BM25 development receipt required")
    for ref in [report["snapshot_report"], report["dense_report"], report["implementation"]]:
        require(file_reference(ROOT / ref["path"]) == ref, "BM25 input/implementation fingerprint changed")
    require(report["implementation"] == file_reference(ROOT / "services/api/app/retrieval/bm25.py"), "BM25 report is linked to a different implementation")
    for ref in report["case_execution"]["source_receipts"]:
        require(file_reference(ROOT / ref["path"]) == ref, "Original BM25 receipt changed")
    _, dense, _, cases, store, images = inputs(ROOT / report["snapshot_report"]["path"], ROOT / report["dense_report"]["path"])
    require(report["configuration"] == dense["configuration"], "BM25/Dense comparison configuration differs")
    execution = report["case_execution"]
    require(execution["mode"] in {"fresh snapshot-only queries", "reused exact verified first-run receipts"}
            and len(execution["source_receipts"]) == (len(cases) if execution["mode"].startswith("reused") else 0), "BM25 case execution accounting differs")
    parameters = BM25Parameters(report["parameters"]["k1"], report["parameters"]["b"])
    require(report["parameters"] == parameters.to_dict(), "BM25 parameter definition changed")
    require([r["id"] for r in report["cases"]] == [c["id"] for c in cases], "BM25 cases dropped/reordered")
    artifacts = Path(path).parent / (Path(path).stem + "_artifacts")
    for ordinal, (case, row, dense_row) in enumerate(zip(cases, report["cases"], dense["cases"], strict=True), 1):
        require(read_json(artifacts / "cases" / f"{ordinal:02}-{case['id']}.json") == row, "BM25 per-case receipt differs")
        if report["case_execution"]["source_receipts"]:
            require(read_json(ROOT / report["case_execution"]["source_receipts"][ordinal - 1]["path"]) == row, "Reused BM25 receipt differs from original")
        snapshot = store.load_ready(images[case["repository"]].scope).snapshot
        require(row["snapshot_revision"] == snapshot.revision, "BM25 corpus identity changed")
        verify_row(case, row, snapshot, parameters, report["configuration"], dense_row)
    require(grouped_summary(report["cases"], cases) == report["summary"] and dense["summary"] == report["dense_summary"], "BM25/Dense summary does not recompute")
    require(report["corpora"] == {repo: {"snapshot_revision": store.load_ready(image.scope).snapshot.revision,
        "documents": len(image.chunks), "average_length": BM25Index(store.load_ready(image.scope).snapshot).average_length}
        for repo, image in images.items()}, "BM25 repository corpus statistics differ")
    require(report["calls"] == {"embedding_calls": 0, "generation_calls": 0}, "Unexpected BM25 model calls")
    if report["live_smoke"]:
        ref = report["live_smoke"]
        require(file_reference(ROOT / ref["path"]) == ref, "BM25 live smoke artifact changed")
        smoke = read_json(ROOT / ref["path"])
        require([r["kind"] for r in smoke["cases"]] == ["identifier", "no_overlap", "no_terms", "empty_corpus"]
                and smoke["collections_unchanged"] and smoke["embedding_calls"] == smoke["generation_calls"] == 0, "BM25 live smoke incomplete")
        for row in smoke["cases"]:
            batch = CandidateBatch.from_dict(row["batch"])
            require(bool(batch.candidates) == (row["kind"] == "identifier"), "BM25 live smoke outcome changed")
    return {"offline_replay_passed": True, "cases": len(cases), "holdout_queried": False, "embedding_calls": 0, "generation_calls": 0}


async def spike(args):
    _, _, _, _, store, images = inputs()
    require(args.repository in images, "Choose a repository present in the frozen snapshot")
    channel = BM25CandidateChannel(SnapshotCoordinator(store, FrozenReader(images)), BM25Parameters(args.k1, args.b))
    batch = await channel.recall(CandidateSearchRequest(args.repository, args.query, args.candidate_limit))
    return {"kind": "independent snapshot-only BM25 spike", "batch": batch.to_dict(), "results": [r.model_dump() for r in final_result(batch, args.top_k)],
            "embedding_calls": 0, "generation_calls": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--verify-only", type=Path)
    group.add_argument("--query")
    parser.add_argument("--repository")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--k1", type=float, default=1.5)
    parser.add_argument("--b", type=float, default=.75)
    parser.add_argument("--candidate-limit", type=int, default=30)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--live-smoke", action="store_true")
    parser.add_argument("--reuse-cases", type=Path, help="Retain and independently verify an existing complete 52-case receipt directory")
    args = parser.parse_args()
    if args.verify_only: print(replay(args.verify_only.resolve()))
    elif args.query is not None:
        if args.repository is None: parser.error("--query requires --repository")
        print(json.dumps(asyncio.run(spike(args)), ensure_ascii=False, indent=2))
    else:
        require(args.candidate_limit == 30 and args.top_k == 5, "Comparison uses fixed candidate limit 30 / final TopK 5")
        args.output = args.output or ROOT / "evaluation/reports" / f"stage4_bm25_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}.json"
        asyncio.run(evaluate(args))


if __name__ == "__main__": main()
