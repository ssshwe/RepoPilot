"""Offline Task 4 receipts from frozen indexed code and development queries."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import math
from pathlib import Path
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services/api"))
sys.path.insert(0, str(ROOT))

from app.retrieval.candidates import SourceMetadata
from app.retrieval.tokenizer import CodeTokenizer
from evaluation.retrieval.evaluate import require
from evaluation.stage4.dataset import load_dataset
from evaluation.stage4.state import file_reference, fingerprint, read_json, write_new

EXAMPLES = (
    "RefundService", "refund_order", "HTTPServer", "getHTTPResponse2", "XMLHTTPRequest",
    "XML_HTTP2_client", "UUID4", "refund订单Id", "退款订单", "退款退款", "钱",
    "backend/service/RefundService.java", r"C:\src\WalletService.java", r"\\server\share\A.java",
    "backend/service/A.java在哪里", "types.d.ts", "com.example.RefundService",
    "Amount must be positive", "ＲｅｆｕｎｄＳｅｒｖｉｃｅ", "caféService", "?!💳",
)


def checked_reference(reference):
    path = ROOT / reference["path"]
    require(file_reference(path) == reference, "Tokenizer input fingerprint changed")
    return read_json(path)


def load_inputs(reference_path):
    reference = read_json(reference_path)
    require(reference["task"] == "Stage 4 / Task 3" and reference["status"] == "complete"
            and reference["holdout_queried"] is False, "Completed development candidate report required")
    checked_reference(reference["dataset"])
    snapshot = checked_reference(reference["artifacts"]["index"])
    require(reference["index_revision"] == "sha256:" + fingerprint(snapshot), "Frozen candidate index identity mismatch")
    data, _ = load_dataset(ROOT / reference["dataset"]["path"], ROOT / "workspaces")
    cases = [case for case in data["cases"] if case["split"] in {"regression", "dev"}]
    require([case["id"] for case in cases] == [case["id"] for case in reference["cases"]], "Reference cases dropped or reordered")
    ids = [str(point["id"]) for point in snapshot["points"]]
    require(len(ids) == len(set(ids)), "Duplicate index Chunk identity")
    for point in snapshot["points"]:
        require(point["payload"]["chunk_id"] == str(point["id"])
                and point["payload"]["repository"] in data["repositories"], "Invalid frozen Chunk identity/scope")
    return reference, snapshot, cases


def lexical_receipt(tokens):
    return {"tokens": tokens, "term_frequencies": dict(sorted(Counter(tokens).items()))}


def measure(snapshot, cases, tokenizer):
    chunks = []
    for point in snapshot["points"]:
        payload = point["payload"]
        source = SourceMetadata.from_payload(payload)
        tokens = tokenizer.tokenize_source(source)
        require(tokens == tokenizer.tokenize("\n".join((source.content, source.path, source.symbol or ""))),
                "Source tokenization diverged from shared core rules")
        chunks.append({"chunk_id": str(point["id"]), "repository": payload["repository"],
                       "path": source.path, "start_line": source.start_line, "end_line": source.end_line,
                       "symbol": source.symbol, "source_sha256": fingerprint(source.to_dict()), **lexical_receipt(tokens)})
    queries = []
    for case in cases:
        require(case["split"] in {"regression", "dev"}, "Reserved queries must not be tokenized in Task 4")
        tokens = tokenizer.tokenize_query(case["query"])
        require(tokens == tokenizer.tokenize(case["query"]), "Query tokenization diverged from shared core rules")
        queries.append({"id": case["id"], "split": case["split"], "repository": case["repository"],
                        "query": case["query"], **lexical_receipt(tokens)})
    examples = [{"input": text, **lexical_receipt(tokenizer.tokenize(text))} for text in EXAMPLES]
    return chunks, queries, examples


def summarize(chunks, queries):
    return {"chunks": len(chunks), "queries": len(queries),
            "query_splits": dict(sorted(Counter(row["split"] for row in queries).items())),
            "chunks_per_repository": dict(sorted(Counter(row["repository"] for row in chunks).items())),
            "source_tokens": sum(len(row["tokens"]) for row in chunks),
            "query_tokens": sum(len(row["tokens"]) for row in queries),
            "source_vocabulary": len({token for row in chunks for token in row["tokens"]}),
            "query_vocabulary": len({token for row in queries for token in row["tokens"]}),
            "empty_token_sources": sum(not row["tokens"] for row in chunks),
            "empty_token_queries": sum(not row["tokens"] for row in queries)}


def build(reference_path, output):
    require(not Path(output).exists(), "Existing tokenizer report must not be overwritten")
    tokenizer = CodeTokenizer()
    source_path = ROOT / "services/api/app/retrieval/tokenizer.py"
    implementation = file_reference(source_path)
    reference_digest = file_reference(reference_path)
    reference, snapshot, cases = load_inputs(reference_path)
    started = perf_counter()
    chunks, queries, examples = measure(snapshot, cases, tokenizer)
    elapsed = round((perf_counter() - started) * 1000, 3)
    require(file_reference(reference_path) == reference_digest and file_reference(source_path) == implementation,
            "Tokenizer implementation/reference drift during run")
    for value in (reference["dataset"], reference["artifacts"]["index"]):
        checked_reference(value)
    report = {"schema_version": 1, "task": "Stage 4 / Task 4", "status": "complete",
              "created_at": datetime.now(timezone.utc).isoformat(), "reference_report": reference_digest,
              "dataset": reference["dataset"], "index_snapshot": reference["artifacts"]["index"],
              "index_revision": reference["index_revision"], "implementation": implementation,
              "tokenizer": tokenizer.specification(), "chunks": chunks, "queries": queries, "examples": examples,
              "summary": summarize(chunks, queries), "tokenization_latency_ms": elapsed,
              "calls": {"embedding_provider_calls": 0, "generation_calls": 0, "qdrant_calls": 0},
              "holdout_queried": False,
              "note": "Lexical behavior verification, not retrieval ranking or quality evaluation; no index rebuild or model calls"}
    write_new(output, report)
    return report["summary"]


def replay(path):
    report = read_json(path)
    require(report["schema_version"] == 1 and report["task"] == "Stage 4 / Task 4"
            and report["status"] == "complete" and report["holdout_queried"] is False,
            "Completed Task 4 development receipt required")
    reference = checked_reference(report["reference_report"])
    require(report["dataset"] == reference["dataset"] and report["index_snapshot"] == reference["artifacts"]["index"]
            and report["index_revision"] == reference["index_revision"], "Tokenizer report/reference association mismatch")
    require(file_reference(ROOT / "services/api/app/retrieval/tokenizer.py") == report["implementation"], "Tokenizer implementation fingerprint changed")
    _, snapshot, cases = load_inputs(ROOT / report["reference_report"]["path"])
    tokenizer = CodeTokenizer()
    require(report["tokenizer"] == tokenizer.specification(), "Tokenizer specification/Unicode runtime changed")
    chunks, queries, examples = measure(snapshot, cases, tokenizer)
    require((report["chunks"], report["queries"], report["examples"]) == (chunks, queries, examples),
            "Chunk/query/example tokens or frequencies do not recompute")
    require(report["summary"] == summarize(chunks, queries), "Tokenizer summary does not recompute")
    require(report["calls"] == {"embedding_provider_calls": 0, "generation_calls": 0, "qdrant_calls": 0}, "Unexpected tokenizer provider calls")
    elapsed = report["tokenization_latency_ms"]
    require(type(elapsed) in (int, float) and math.isfinite(elapsed) and elapsed >= 0, "Invalid observed tokenization latency")
    return {"offline_replay_passed": True, "chunks": len(chunks), "queries": len(queries), "examples": len(examples),
            "holdout_queried": False, "generation_calls": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference-report", type=Path, default=ROOT / "evaluation/reports/stage4_candidates_20261013_task3.json")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify-only", type=Path)
    args = parser.parse_args()
    if args.verify_only:
        print(replay(args.verify_only.resolve()))
    else:
        output = args.output or ROOT / "evaluation/reports" / f"stage4_tokenizer_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}.json"
        print(build(args.reference_report.resolve(), output.resolve()))


if __name__ == "__main__":
    main()
