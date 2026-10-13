"""Chunk-cutoff metrics; complete ranges and unique required lines are distinct."""
import math
from statistics import mean

from evaluation.retrieval.evaluate import require

KS = (1, 3, 5)


def intervals(results, path):
    merged = []
    for start, end in sorted((r["start_line"], r["end_line"]) for r in results if r["path"] == path):
        if merged and start <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def covered(results, entry):
    return any(start <= entry["start_line"] and end >= entry["end_line"] for start, end in intervals(results, entry["path"]))


def evidence_coverage(results, evidence):
    if not evidence:
        return None
    required = {}
    for entry in evidence:
        required.setdefault(entry["path"], set()).update(range(entry["start_line"], entry["end_line"] + 1))
    hits = 0
    for path, lines in required.items():
        spans = intervals(results, path)
        hits += sum(any(start <= line <= end for start, end in spans) for line in lines)
    ranges = sum(covered(results, entry) for entry in evidence)
    return {"range_recall": ranges / len(evidence), "covered_ranges": ranges, "required_ranges": len(evidence),
            "line_coverage": hits / sum(map(len, required.values())), "covered_lines": hits,
            "required_lines": sum(map(len, required.values())), "all_evidence": ranges == len(evidence)}


def relevance(result, evidence):
    return any(result["path"] == e["path"] and result["start_line"] <= e["end_line"]
               and result["end_line"] >= e["start_line"] for e in evidence)


def case_metrics(case, results, sources, ks=KS):
    if case["category"] == "negative":
        return {"at_k": None, "context": None, "negative": {
            "kind": case["negative_kind"], "returned_chunks": len(results), "empty_result": not results,
            "foreign_repository_chunks": sum(r["repository"] != case["repository"] for r in results)}}
    expected_paths = set(case["expected_paths"])
    expected_symbols = {(s["path"], s["symbol"]) for s in case["expected_symbols"]}
    at_k = {}
    for k in ks:
        selected = results[:k]
        found_paths = expected_paths.intersection(r["path"] for r in selected)
        found_symbols = expected_symbols.intersection((r["path"], r.get("symbol")) for r in selected)
        first = next((rank for rank, r in enumerate(selected, 1) if relevance(r, case["evidence"])), None)
        at_k[str(k)] = {"file_hit": bool(found_paths), "file_recall": len(found_paths) / len(expected_paths),
                       "symbol_hit": bool(found_symbols) if expected_symbols else None,
                       "symbol_recall": len(found_symbols) / len(expected_symbols) if expected_symbols else None,
                       "evidence_mrr": 1 / first if first else 0.0,
                       **evidence_coverage(selected, case["evidence"])}
    return {"at_k": at_k, "context": evidence_coverage(sources, case["evidence"]), "negative": None}


def percentile(values, fraction):
    """Linear interpolation on sorted samples, rank=(n-1)*fraction; one sample returns itself."""
    require(bool(values) and 0 <= fraction <= 1 and all(type(v) in (int, float) and math.isfinite(v) and v >= 0 for v in values),
            "Finite nonnegative latency samples required")
    samples = sorted(values)
    position = (len(samples) - 1) * fraction
    low = math.floor(position)
    high = math.ceil(position)
    return samples[low] + (samples[high] - samples[low]) * (position - low)


def summarize(rows):
    positives = [r for r in rows if r["metrics"]["at_k"] is not None]
    negatives = [r for r in rows if r["metrics"]["negative"] is not None]
    symbol_rows = [r for r in positives if r["metrics"]["at_k"]["5"]["symbol_recall"] is not None]
    def average(values):
        return mean(values) if values else None
    at_k = {}
    for k in KS:
        key = str(k)
        at_k[key] = {"positive_cases": len(positives), "symbol_cases": len(symbol_rows),
            **{name: average([r["metrics"]["at_k"][key][name] for r in positives]) for name in (
                "file_hit", "file_recall", "range_recall", "line_coverage", "evidence_mrr", "all_evidence")},
            **{name: average([r["metrics"]["at_k"][key][name] for r in symbol_rows]) for name in ("symbol_hit", "symbol_recall")}}
    contexts = [r["metrics"]["context"] for r in positives]
    times = [r["retrieval"]["retrieval_latency_ms"] for r in rows if r.get("retrieval") is not None]
    return {"cases": len(rows), "at_k": at_k,
            "context": {"cases": len(contexts), **{name: average([c[name] for c in contexts]) for name in ("range_recall", "line_coverage", "all_evidence")}},
            "negative": {"cases": len(negatives), "empty_results": sum(r["metrics"]["negative"]["empty_result"] for r in negatives),
                         "empty_result_rate": average([r["metrics"]["negative"]["empty_result"] for r in negatives]),
                         "foreign_repository_chunks": sum(r["metrics"]["negative"]["foreign_repository_chunks"] for r in negatives)},
            "latency_ms": {"samples": len(times), "p50": percentile(times, .5) if times else None,
                           "p95": percentile(times, .95) if times else None}}


DEFINITIONS = {
    "cutoff": "K cuts ranked chunks before file/Symbol deduplication",
    "file": "Unique expected paths; Hit means any match, Recall fraction of expected paths",
    "symbol": "Exact (path, symbol), N/A for questions without named Symbol labels",
    "range_recall": "Fraction of annotated inclusive ranges fully covered by the union of TopK chunks",
    "line_coverage": "Fraction of unique required (path,line) pairs covered; overlapping annotations count once",
    "evidence_mrr": "Reciprocal rank of first chunk overlapping a labelled evidence range; zero when absent within K",
    "all_evidence": "All annotated ranges completely covered; equivalent to range_recall=1",
    "context": "Actual emitted Sources from the same retrieval's ContextBuilder, with their actual truncated ranges",
    "negative": "No relevant evidence: recall/MRR/context are N/A; empty results and foreign-repository counts reported separately",
    "macro": "Means of applicable per-case values; supplementary negatives never inflate positive denominators",
    "latency": "Service retrieval time includes query embedding and Qdrant; p50/p95 use linear interpolation on observed samples",
}
