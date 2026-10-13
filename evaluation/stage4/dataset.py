"""Validate Stage 4 labels against whole frozen repository manifests and AST Symbols."""
import hashlib
import json
from pathlib import Path

from evaluation.retrieval.evaluate import require, source_file
from evaluation.stage4.state import fingerprint
from app.indexing.ast_chunker import ASTCodeChunker
from app.indexing.service import prepare_repository
from app.repository.scanner import RepositoryScanner
from app.schemas.retrieval import RetrievalSearchRequest

CATEGORIES = {"symbol", "path", "error_text", "literal", "natural_language", "multi_file", "sql", "config", "documentation", "negative"}


def verify_sources(data, workspaces):
    scanner = RepositoryScanner(workspaces)
    for repository, metadata in data["repositories"].items():
        scan = scanner.scan(repository)
        actual = {f.path: hashlib.sha256(source_file(workspaces / repository, f.path).read_bytes()).hexdigest() for f in scan.files}
        require(actual == metadata["source_sha256"], f"Frozen repository source set changed: {repository}")
        require(fingerprint(actual) == metadata["source_hash"], "Repository aggregate fingerprint mismatch")


def load_dataset(path, workspaces):
    raw = Path(path).read_bytes()
    require(len(raw) <= 2_097_152, "Stage 4 dataset exceeds 2 MiB")
    data = json.loads(raw)
    require(type(data.get("schema_version")) is int and data["schema_version"] == 1, "Unsupported Stage 4 schema")
    require(isinstance(data.get("repositories"), dict) and bool(data["repositories"]), "Repository manifests required")
    verify_sources(data, workspaces)
    symbol_sets = {}
    for repository in data["repositories"]:
        prepared = prepare_repository(repository, RepositoryScanner(workspaces), ASTCodeChunker(), 2_000_000)
        symbol_sets[repository] = {(c.path, c.symbol) for c in prepared.chunks if c.symbol}
    seen, groups, queries = set(), {}, {}
    counts = {"regression": 0, "dev": 0, "holdout": 0}
    for case in data["cases"]:
        require(isinstance(case.get("id"), str) and case["id"] and case["id"] not in seen, "Duplicate/invalid case ID")
        seen.add(case["id"])
        split, repository = case["split"], case["repository"]
        require(split in counts and repository in data["repositories"], "Invalid split/repository")
        counts[split] += 1
        RetrievalSearchRequest.model_validate({"repository": repository, "query": case["query"], "top_k": 5}, strict=True)
        group = case["group_id"]
        require(isinstance(group, str) and bool(group), "Task-family group required")
        require(group not in groups or groups[group] == split, "Task-family group crosses splits")
        groups[group] = split
        normalized = " ".join(case["query"].lower().split())
        require(normalized not in queries or queries[normalized] == split, "Duplicate query crosses splits")
        queries[normalized] = split
        require(case["category"] in CATEGORIES and isinstance(case["annotator"], str) and case["annotator"], "Category/annotator required")
        require(case["source_hash"] == data["repositories"][repository]["source_hash"] and case["source_revision"] is None,
                "Case source identity mismatch; no Git revision is available")
        require(isinstance(case["required_facts"], list) and bool(case["required_facts"])
                and all(isinstance(f, str) and f.strip() for f in case["required_facts"]), "Source-grounded facts required")
        paths, evidence, symbols = case["expected_paths"], case["evidence"], case["expected_symbols"]
        require(len(paths) == len(set(paths)), "Duplicate expected paths")
        if case["category"] == "negative":
            require(not paths and not evidence and not symbols and case["negative_kind"] in {
                "missing_symbol", "missing_path", "missing_literal", "empty_repository", "repository_scope_mismatch"}, "Invalid negative control")
            target = case.get("absent_target")
            if case["negative_kind"] in {"missing_symbol", "repository_scope_mismatch"}:
                require(target and not any(s == target for _, s in symbol_sets[repository]), "Absent Symbol actually exists in scoped repository")
            elif case["negative_kind"] == "missing_path":
                require(target not in data["repositories"][repository]["source_sha256"], "Absent path actually exists")
            elif case["negative_kind"] == "missing_literal":
                require(target and all(target not in f.content for f in RepositoryScanner(workspaces).scan(repository).files), "Absent literal actually exists")
            else:
                require(not data["repositories"][repository]["source_sha256"], "Empty control repository is not empty")
            continue
        require(paths and evidence and {e["path"] for e in evidence} == set(paths), "Positive evidence must cover expected paths")
        ranges = set()
        for entry in evidence:
            key = (entry["path"], entry["start_line"], entry["end_line"])
            require(key not in ranges, "Duplicate evidence range")
            ranges.add(key)
            lines = source_file(workspaces / repository, entry["path"]).read_bytes().decode("utf-8-sig").splitlines(keepends=True)
            start, end = entry["start_line"], entry["end_line"]
            require(type(start) is int and type(end) is int and 1 <= start <= end <= len(lines), "Invalid inclusive range")
            require(entry["snippet"] and entry["snippet"] in "".join(lines[start - 1:end]), "Evidence snippet differs from source")
        require(len(symbols) == len({(s["path"], s["symbol"]) for s in symbols}) and
                all(s["path"] in paths and (s["path"], s["symbol"]) in symbol_sets[repository] for s in symbols), "Named Symbol label differs from actual AST")
    require(counts == {"regression": 12, "dev": 40, "holdout": 20}, "Dataset must retain 12 historical + 40 dev + 20 holdout cases")
    historical = json.loads((Path(__file__).resolve().parents[1] / "datasets/stage3_grounding.json").read_bytes())
    expected = [c for c in historical["cases"] if c["group"] == "canonical"]
    regression = [c for c in data["cases"] if c["split"] == "regression"]
    require([{k: c[k] for k in ("id", "query", "repository", "expected_paths", "evidence", "expected_symbols")} for c in regression]
            == [{k: c[k] for k in ("id", "query", "repository", "expected_paths", "evidence", "expected_symbols")} for c in expected],
            "Historical 12 queries/labels/order changed")
    return data, hashlib.sha256(raw).hexdigest()
