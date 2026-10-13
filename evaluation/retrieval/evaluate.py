"""Evaluate the existing HTTP retriever; no indexing, reranking or LLM judging."""
import argparse
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
from statistics import mean
import sys
from time import perf_counter
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[2]
KS = (1, 3, 5)


class EvaluationError(ValueError):
    """Invalid labels, failed requests or incompatible run data."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise EvaluationError(message)


def integer(value: object, minimum: int, maximum: int) -> bool:
    return type(value) is int and minimum <= value <= maximum


def finite(value: object) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def source_file(root: Path, path: str) -> Path:
    require(isinstance(path, str) and bool(path), "Source path must be nonempty")
    require(not PurePosixPath(path).is_absolute() and not any(c in path for c in "\\:\x00")
            and all(part not in ("", ".", "..") for part in path.split("/")),
            "Source paths must be relative POSIX paths")
    candidate = (root / path).resolve()
    require(candidate.is_relative_to(root.resolve()) and candidate.is_file(),
            f"Source file missing or outside repository: {path}")
    return candidate


@dataclass(frozen=True)
class Dataset:
    data: dict
    sha256: str
    repository_root: Path


def verify_source_hashes(dataset: Dataset) -> None:
    for path, expected in dataset.data["source_sha256"].items():
        actual = hashlib.sha256(source_file(dataset.repository_root, path).read_bytes()).hexdigest()
        require(actual == expected, f"Annotation source changed: {path}")


def load_dataset(path: Path, workspaces: Path) -> Dataset:
    raw = path.read_bytes()
    require(len(raw) <= 1_048_576, "Dataset exceeds 1 MiB")
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (ValueError, UnicodeError):
        raise EvaluationError("Dataset is not valid UTF-8 JSON") from None
    require(isinstance(data, dict) and type(data.get("schema_version")) is int
            and data["schema_version"] == 1, "Unsupported dataset schema")
    require(isinstance(data.get("name"), str) and bool(data["name"]), "Dataset name required")
    repository = data.get("repository")
    require(isinstance(repository, str) and bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", repository))
            and not repository.endswith(".")
            and not re.fullmatch(r"CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9]", repository.split(".")[0], re.I),
            "Invalid portable repository name")
    repository_root = (workspaces / repository).resolve()
    require(repository_root.is_relative_to(workspaces.resolve()) and repository_root.is_dir(),
            "Repository missing or outside workspaces")
    cases, hashes = data.get("cases"), data.get("source_sha256")
    require(isinstance(cases, list) and 1 <= len(cases) <= 100, "Dataset needs 1..100 cases")
    require(isinstance(hashes, dict) and bool(hashes), "Source fingerprints required")
    expected_union, seen_ids = set(), set()
    for case in cases:
        require(isinstance(case, dict), "Case must be an object")
        identifier, query = case.get("id"), case.get("query")
        require(isinstance(identifier, str) and bool(identifier) and identifier not in seen_ids,
                "Case IDs must be nonempty and unique")
        seen_ids.add(identifier)
        require(isinstance(query, str) and bool(query.strip()) and len(query) <= 8192,
                f"Invalid query: {identifier}")
        paths, evidence = case.get("expected_paths"), case.get("evidence")
        require(isinstance(paths, list) and bool(paths) and all(isinstance(p, str) for p in paths)
                and len(set(paths)) == len(paths), f"Expected paths must be nonempty and unique: {identifier}")
        for source_path in paths:
            source_file(repository_root, source_path)
            require(source_path in hashes, f"Missing source fingerprint: {source_path}")
        expected_union.update(paths)
        require(isinstance(evidence, list) and len(evidence) == len(paths)
                and all(isinstance(e, dict) for e in evidence)
                and all(isinstance(e.get("path"), str) for e in evidence)
                and set(e["path"] for e in evidence) == set(paths), f"Evidence must cover each expected file: {identifier}")
        for entry in evidence:
            lines = source_file(repository_root, entry["path"]).read_text(encoding="utf-8-sig").splitlines(keepends=True)
            start, end, snippet = entry.get("start_line"), entry.get("end_line"), entry.get("snippet")
            require(integer(start, 1, len(lines)) and integer(end, start, len(lines)), "Invalid evidence line range")
            require(isinstance(snippet, str) and bool(snippet.strip())
                    and snippet in "".join(lines[start - 1:end]), f"Evidence does not match source: {identifier}")
    require(set(hashes) == expected_union
            and all(isinstance(v, str) and re.fullmatch(r"[0-9a-f]{64}", v) for v in hashes.values()),
            "Source fingerprints must cover exactly the labelled files")
    dataset = Dataset(data, hashlib.sha256(raw).hexdigest(), repository_root)
    verify_source_hashes(dataset)
    return dataset


def metrics_for_case(paths: list[str], expected_paths: list[str], ks=KS) -> dict:
    require(bool(expected_paths) and len(set(expected_paths)) == len(expected_paths),
            "Recall denominator must contain unique relevant paths")
    require(bool(ks) and all(integer(k, 1, 20) for k in ks), "K must be an integer in 1..20")
    expected = set(expected_paths)
    metrics = {}
    for k in ks:
        # Keep K as a CHUNK cutoff; deduplicate files only AFTER taking the prefix.
        found = expected.intersection(paths[:k])
        metrics[str(k)] = {"hit": bool(found), "recall": len(found) / len(expected),
                           "matched_paths": sorted(found)}
    return metrics


def summarize(rows: list[dict], ks=KS) -> dict:
    require(bool(rows), "Cannot summarize zero queries")
    return {"queries": len(rows), "hit_at_k": {
        str(k): {"hits": sum(row["metrics"][str(k)]["hit"] for row in rows),
                 "total": len(rows), "rate": mean(row["metrics"][str(k)]["hit"] for row in rows)} for k in ks
    }, "macro_recall_at_k": {
        str(k): mean(row["metrics"][str(k)]["recall"] for row in rows) for k in ks
    }}


def validate_response(data: object, request: dict, root: Path) -> dict:
    require(isinstance(data, dict) and all(data.get(key) == value for key, value in request.items()),
            "Response does not match the submitted request")
    require(type(data["top_k"]) is int, "Invalid response TopK")
    require(all(isinstance(data.get(key), str) and bool(data[key]) for key in ("collection", "embedding_model"))
            and integer(data.get("embedding_dimension"), 1, 65536)
            and finite(data.get("retrieval_latency_ms")) and data["retrieval_latency_ms"] >= 0,
            "Invalid response model/latency metadata")
    results = data.get("results")
    require(isinstance(results, list) and len(results) <= request["top_k"], "Invalid result count")
    projected, seen = [], set()
    for rank, item in enumerate(results, start=1):
        require(isinstance(item, dict) and type(item.get("rank")) is int and item["rank"] == rank
                and item.get("repository") == request["repository"], "Invalid result rank or repository scope")
        identifier = item.get("chunk_id")
        require(isinstance(identifier, str) and bool(identifier) and identifier not in seen, "Invalid/duplicate chunk ID")
        seen.add(identifier)
        require(finite(item.get("score")) and isinstance(item.get("language"), str) and bool(item["language"]),
                "Invalid result score/language")
        start, end = item.get("start_line"), item.get("end_line")
        require(integer(start, 1, sys.maxsize) and integer(end, start, sys.maxsize), "Invalid result line range")
        source = source_file(root, item.get("path"))
        text = source.read_bytes().decode("utf-8-sig")
        content = item.get("content")
        require(isinstance(content, str) and bool(content.strip()) and len(content) <= 8192
                and len(content.splitlines()) == end - start + 1
                and content == "".join(text.splitlines(keepends=True)[start - 1:end]),
                "Returned chunk does not match current source; verify/reindex before evaluation")
        projected.append({key: item[key] for key in (
            "rank", "chunk_id", "repository", "score", "path", "language", "start_line", "end_line",
        )})
    return {key: data[key] for key in ("collection", "embedding_model", "embedding_dimension", "retrieval_latency_ms")} | {"results": projected}


def search_api(base_url: str, body: dict, timeout: float = 180) -> dict:
    request = Request(base_url.rstrip("/") + "/api/retrieval/search",
                      data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                      headers={"Content-Type": "application/json"}, method="POST")
    try:
        with build_opener(ProxyHandler({})).open(request, timeout=timeout) as response:
            require(response.status == 200, "Search requires HTTP 200")
            raw = response.read(2_097_153)
            require(len(raw) <= 2_097_152, "Search response exceeds 2 MiB")
            return json.loads(raw)
    except HTTPError as error:
        raise EvaluationError(f"Search failed with HTTP {error.code}; no metrics report saved") from None
    except (URLError, TimeoutError, ValueError) as error:
        if isinstance(error, EvaluationError):
            raise
        raise EvaluationError("Search connection or JSON response failed; no metrics report saved") from None


def evaluate(dataset: Dataset, search: Callable[[dict], dict], progress=None) -> dict:
    rows, identity = [], None
    for case in dataset.data["cases"]:
        request = {"repository": dataset.data["repository"], "query": case["query"], "top_k": max(KS)}
        started = perf_counter()
        raw_response = search(request)
        elapsed = (perf_counter() - started) * 1000
        response = validate_response(raw_response, request, dataset.repository_root)
        current_identity = tuple(response[key] for key in ("collection", "embedding_model", "embedding_dimension"))
        require(identity is None or identity == current_identity, "Model/collection changed during evaluation")
        identity = current_identity
        metrics = metrics_for_case([item["path"] for item in response["results"]], case["expected_paths"])
        rows.append({"id": case["id"], "query": case["query"], "expected_paths": case["expected_paths"],
                     "evidence": case["evidence"], "request_top_k": max(KS), "http_latency_ms": elapsed,
                     "retrieval_latency_ms": response["retrieval_latency_ms"],
                     "results": response["results"], "metrics": metrics})
        if progress:
            progress(len(rows), len(dataset.data["cases"]), case["id"], len(response["results"]))
    verify_source_hashes(dataset)
    return {"status": "complete", "generated_at": datetime.now(timezone.utc).isoformat(),
            "dataset": {"name": dataset.data["name"], "sha256": dataset.sha256,
                        "source_sha256": dataset.data["source_sha256"], "relevance_policy": dataset.data.get("relevance_policy", "")},
            "retrieval": {"repository": dataset.data["repository"], "collection": identity[0],
                          "embedding_model": identity[1], "embedding_dimension": identity[2],
                          "requested_top_k": max(KS), "ks": list(KS), "ranking_unit": "code_chunk",
                          "relevance_unit": "unique_source_path"},
            "metric_definitions": {"hit_at_k": "Fraction of queries with at least one expected file in the first K chunks",
                                   "macro_recall_at_k": "Mean across queries of distinct expected files retrieved in first K chunks / expected file count"},
            "summary": summarize(rows), "queries": rows}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=ROOT / "evaluation/datasets/stage2_retrieval.json")
    parser.add_argument("--workspaces", type=Path, default=ROOT / "workspaces")
    parser.add_argument("--api-base-url", default="http://localhost:8000")
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        url = urlsplit(args.api_base_url)
        require(url.scheme in ("http", "https") and bool(url.hostname) and url.username is None
                and not url.query and not url.fragment, "API URL requires HTTP(S), no credentials/query/fragment")
        require(math.isfinite(args.timeout) and 0 < args.timeout <= 600, "Timeout must be in (0, 600] seconds")
        output = args.output or ROOT / "evaluation/reports" / (
            "stage2_retrieval_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json")
        require(not output.exists(), "Output already exists; choose a new report filename")
        dataset = load_dataset(args.dataset, args.workspaces)
        report = evaluate(dataset, lambda body: search_api(args.api_base_url, body, args.timeout),
                          lambda index, total, identifier, count: print(f"[{index}/{total}] {identifier}: {count} chunks", flush=True))
        require(hashlib.sha256(args.dataset.read_bytes()).hexdigest() == dataset.sha256, "Dataset changed during evaluation")
        report["dataset"]["path"] = str(args.dataset.resolve())
        report["retrieval"]["api_base_url"] = args.api_base_url.rstrip("/")
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        print(f"Queries: {report['summary']['queries']}")
        for k in KS:
            hit = report["summary"]["hit_at_k"][str(k)]
            recall = report["summary"]["macro_recall_at_k"][str(k)]
            print(f"Hit@{k}: {hit['hits']}/{hit['total']} ({hit['rate']:.2%}); Macro Recall@{k}: {recall:.2%}")
        print(f"Report: {output.resolve()}")
        return 0
    except (EvaluationError, OSError, UnicodeError) as error:
        print(f"Evaluation failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
