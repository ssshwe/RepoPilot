"""Freeze Task 13's real Dense reports; optional Top20 diagnostics, no generation/writes to Qdrant.

Replay a saved baseline completely offline with --verify-only --output <baseline.json>.
"""
import argparse
import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services/api"))
sys.path.insert(0, str(ROOT))

from app.context import ContextBuilder
from app.core.config import get_settings
from app.embedding.base import EmbeddingProvider
from app.embedding.factory import EmbeddingProviderFactory
from app.embedding.identity import embedding_identity
from app.embedding.validation import validate_embedding_batch
from app.retrieval.service import RetrievalService
from app.retrieval.vector_store import QdrantVectorStore, create_qdrant_client
from app.schemas.retrieval import RetrievalResult, RetrievalSearchRequest
from evaluation.retrieval.compare_chunking import evidence_metrics
from evaluation.retrieval.evaluate import EvaluationError, require, summarize, verify_source_hashes
from evaluation.stage3.evaluate import (
    load_grounding_dataset, measure_retrieval, summarize_retrieval, verify_response_evidence,
)
from evaluation.stage3.review import review_report
from evaluation.stage4.state import (
    file_reference, fingerprint, full_snapshot, legacy_snapshot, read_json,
    runtime_identity, safe_configuration, verify_unchanged, write_new,
)


def check_report_links(acceptance, paths):
    require(acceptance.get("completed") is True and acceptance.get("functional_acceptance") is True,
            "Task 13 functional acceptance must finish before publishing a baseline")
    for name, path in paths.items():
        require(acceptance["reports"].get(name) == file_reference(path),
                f"Task 13 does not bind this {name} report")


def index_inventory(state, config, repository):
    collection = state[config["qdrant_collection"]]
    vectors = collection["config"]["params"]["vectors"]
    require(vectors.get("distance") == "Cosine" and isinstance(vectors.get("size"), int),
            "Expected one unnamed Dense Cosine vector")
    points = {}
    for point in collection["points"]:
        payload = point.get("payload") or {}
        if payload.get("repository") != repository:
            continue
        require(str(point["id"]) == payload.get("chunk_id") and str(point["id"]) not in points,
                "Invalid/duplicate indexed chunk identity")
        require(payload.get("embedding_model") == config["embedding_model"]
                and payload.get("schema_version") == "code-points-v1", "Indexed embedding identity mismatch")
        vector = validate_embedding_batch([point["vector"]], 1)[0]
        require(len(vector) == vectors["size"], "Indexed vector dimension mismatch")
        value = {k: payload[k] for k in (
            "chunk_id", "repository", "path", "language", "start_line", "end_line", "content",
            "symbol", "node_type", "parent_symbol", "chunking_strategy", "chunking_version")}
        RetrievalResult.model_validate(value | {"rank": 1, "score": 0.0}, strict=True)
        points[str(point["id"])] = value
    require(bool(points), "Repository has no baseline chunks")
    return points


def verify_index_sources(inventory, repository_root):
    from evaluation.retrieval.evaluate import source_file
    hashes = {}
    for value in inventory.values():
        path = source_file(repository_root, value["path"])
        raw = path.read_bytes()
        hashes[value["path"]] = hashlib.sha256(raw).hexdigest()
        lines = raw.decode("utf-8-sig").splitlines(keepends=True)
        require(value["content"] == "".join(lines[value["start_line"] - 1:value["end_line"]]),
                "Indexed content/coordinates differ from source")
    return hashes


def enriched_results(results, inventory):
    enriched = []
    for result in results:
        require(result.get("chunk_id") in inventory, "Result is absent from frozen repository index")
        original = inventory[result["chunk_id"]]
        require(all(original[k] == v for k, v in result.items() if k not in {"rank", "score"}),
                "Result content/metadata differ from frozen index")
        enriched.append(original | {"rank": result["rank"], "score": result["score"]})
    require([r["rank"] for r in enriched] == list(range(1, len(enriched) + 1))
            and len({r["chunk_id"] for r in enriched}) == len(enriched), "Invalid ranking/duplicate chunks")
    return enriched


def range_covered(results, entry):
    return evidence_metrics(results, [entry])["5"]["recall"] == 1


def classify_case(case, row, review, inventory, diagnostic=None):
    """Evidence-specific observations, not an unsupported claim about causal model reasoning."""
    retrieved, context = row["trace"]["retrieval"][0]["results"], row["trace"]["contexts"][0]
    failures = []
    # evidence_metrics normally uses Top5. Full-index coverage needs all records.
    def covered_all(records, entry):
        intervals = sorted((r["start_line"], r["end_line"]) for r in records if r["path"] == entry["path"])
        cursor = entry["start_line"]
        for start, end in intervals:
            if start > cursor:
                break
            cursor = max(cursor, end + 1)
        return cursor > entry["end_line"]
    for entry in case["evidence"]:
        link = {"path": entry["path"], "start_line": entry["start_line"], "end_line": entry["end_line"]}
        in_top5 = covered_all(retrieved[:5], entry)
        if not in_top5:
            indexed = covered_all(list(inventory.values()), entry)
            diagnostic_results = diagnostic["response"]["results"] if diagnostic else []
            recovered = covered_all(diagnostic_results, entry)
            failures.append({"category": "ranking_low" if indexed and recovered else "index_or_recall_missing",
                "evidence": link, "indexed_range_complete": indexed, "top20_range_complete": recovered,
                "diagnostic_chunk_ids": [r["chunk_id"] for r in diagnostic_results
                    if r["path"] == entry["path"] and r["end_line"] >= entry["start_line"] and r["start_line"] <= entry["end_line"]],
                "reason": "Required range absent from original Top5; Top20 is a separate query and cannot replace it"})
        if in_top5 and not covered_all(context["sources"], entry):
            failures.append({"category": "context_missing", "evidence": link,
                "source_ids": [s["source_id"] for s in context["sources"] if s["path"] == entry["path"]],
                "reason": "Original retrieval covers range; actual emitted Context does not"})
    context_complete = bool(case["evidence"]) and all(covered_all(context["sources"], e) for e in case["evidence"])
    if case["expected_paths"] and (review["outcome"] in {"unsupported", "nonresponsive", "generation_error", "abstention"}
                                   or context_complete and not review["answer_complete"]):
        failures.append({"category": "generation_error", "response_sha256": review["response_sha256"],
                         "source_ids": row["response"]["body"].get("source_ids", []),
                         "context_all_evidence": context_complete, "reason": review["reason"]})
    return {"id": case["id"], "group": case["group"], "failures": failures}


def replay_inputs(data, base, retrieval, grounding, review_path, inventory):
    require(retrieval.get("status") == "complete" and retrieval["dataset"]["sha256"] == base.sha256,
            "Historical retrieval report dataset mismatch")
    require(retrieval["dataset"]["source_sha256"] == data["source_sha256"]
            and grounding["source_sha256"] == data["source_sha256"], "Report source fingerprints mismatch")
    canonical = [c for c in data["cases"] if c["group"] == "canonical"]
    require([r["id"] for r in retrieval["queries"]] == [c["id"] for c in canonical], "Historical report must contain exact 12 queries")
    require([r["id"] for r in grounding["cases"]] == [c["id"] for c in data["cases"]], "Grounding report cases/order mismatch")
    http_rows = []
    for case, row in zip(canonical, retrieval["queries"], strict=True):
        require(row["query"] == case["query"] and row["expected_paths"] == case["expected_paths"]
                and row["evidence"] == case["evidence"] and row["request_top_k"] == 5, "Historical query/labels changed")
        enriched = enriched_results(row["results"], inventory)
        limits = grounding["configuration"]
        built = json.loads(json.dumps(asdict(ContextBuilder(
            max_context_chunks=limits["max_context_chunks"],
            max_context_characters=limits["max_context_characters"],
        ).build(case["repository"], [RetrievalResult.model_validate(r, strict=True) for r in enriched]))))
        metrics = measure_retrieval(case, enriched, built)
        require(metrics["file"] == row["metrics"], "Historical metric replay mismatch")
        http_rows.append({"id": case["id"], "group": "canonical", "retrieval_metrics": metrics,
                          "results": enriched, "rebuilt_context": built,
                          "retrieval_latency_ms": row["retrieval_latency_ms"], "http_latency_ms": row["http_latency_ms"]})
    require(summarize(retrieval["queries"]) == retrieval["summary"], "Historical summary replay mismatch")
    for case, row in zip(data["cases"], grounding["cases"], strict=True):
        require(row["group"] == case["group"] and row["mode"] == case["mode"], "Grounding mode/group mismatch")
        response_metrics = measure_retrieval(case, row["trace"]["retrieval"][0]["results"], row["trace"]["contexts"][0])
        require(response_metrics == row["retrieval_metrics"], "Grounding metric replay mismatch")
        require(verify_response_evidence(case, row["response"], row["trace"], base.repository_root) == row["citation_check"],
                "Citation/Context replay mismatch")
        if case["repository"] == data["repository"]:
            enriched_results(row["trace"]["retrieval"][0]["results"], inventory)
    require(summarize_retrieval(grounding["cases"]) == grounding["retrieval_summary"], "Grounding summary replay mismatch")
    summary = review_report(review_path[0], review_path[1])
    return http_rows, summary


class CountingEmbedding(EmbeddingProvider):
    def __init__(self, provider):
        self.provider, self.calls, self.texts = provider, 0, 0

    @property
    def model_name(self):
        return self.provider.model_name

    async def embed_texts(self, texts):
        self.calls += 1
        self.texts += len(texts)
        return await self.provider.embed_texts(texts)


async def collect_diagnostics(data, grounding, inventory, settings, client, receipt_dir):
    selected = [(case, row) for case, row in zip(data["cases"], grounding["cases"], strict=True)
                if case["expected_paths"] and row["retrieval_metrics"]["annotated_lines"]["5"]["recall"] < 1]
    diagnostics = []
    if not selected:
        return diagnostics
    provider = EmbeddingProviderFactory.create(settings)
    observed = CountingEmbedding(provider)
    service = RetrievalService(observed, QdrantVectorStore(client, settings.qdrant_collection), settings)
    try:
        for case, row in selected:
            request = RetrievalSearchRequest(repository=case["repository"], query=case["query"], top_k=20)
            calls, texts = observed.calls, observed.texts
            response = (await service.search(request)).model_dump(mode="json")
            enriched_results(response["results"], inventory)
            diagnostic = {"id": case["id"], "request": request.model_dump(), "response": response,
                "embedding_provider_calls": observed.calls - calls, "embedded_texts": observed.texts - texts,
                "generation_calls": 0, "prefix_matches_original_top5":
                    [r["chunk_id"] for r in response["results"][:5]] == [r["chunk_id"] for r in row["trace"]["retrieval"][0]["results"]],
                "original_top5_chunk_ids": [r["chunk_id"] for r in row["trace"]["retrieval"][0]["results"]]}
            write_new(receipt_dir / f"{case['id']}.json", diagnostic)
            diagnostics.append(diagnostic)
            print(f"Top20 diagnostic {case['id']}: {len(response['results'])} chunks, prefix_match={diagnostic['prefix_matches_original_top5']}", flush=True)
    finally:
        await provider.aclose()
    return diagnostics


async def freeze(args):
    output = args.output.resolve()
    artifacts = output.parent / f"{output.stem}_artifacts"
    require(not output.exists() and not artifacts.exists(), "Existing baseline/artifact directory must not be overwritten")
    paths = {"retrieval": args.retrieval_report.resolve(), "grounding": args.grounding_report.resolve(), "review": args.review_report.resolve()}
    acceptance = read_json(args.acceptance)
    check_report_links(acceptance, paths)
    data, digest, base = load_grounding_dataset(ROOT / "evaluation/datasets/stage3_grounding.json", ROOT / "workspaces")
    retrieval, grounding = read_json(paths["retrieval"]), read_json(paths["grounding"])
    require(grounding["dataset_sha256"] == digest, "Grounding dataset fingerprint mismatch")
    for report, field in ((retrieval, "generated_at"), (grounding, "created_at")):
        require(report[field] >= acceptance["session_started_at"], "Report predates this acceptance session")
    expected_state = read_json(ROOT / acceptance["qdrant_snapshot"]["path"])
    require(file_reference(ROOT / acceptance["qdrant_snapshot"]["path"]) == acceptance["qdrant_snapshot"], "Acceptance snapshot fingerprint mismatch")
    settings = get_settings()
    config = safe_configuration(settings)
    verify_unchanged(acceptance["configuration"], config, "Configuration")
    verify_unchanged(acceptance["runtime"], runtime_identity(), "Runtime/implementation")
    client = create_qdrant_client(settings)
    try:
        before = await full_snapshot(client)
        verify_unchanged(expected_state, before, "Accepted Qdrant index")
        require(legacy_snapshot(before) == grounding["qdrant_before"] and grounding["qdrant_unchanged"], "Grounding index differs from accepted state")
        collection = before[settings.qdrant_collection]
        metadata = collection["config"]["metadata"]
        require(metadata["repopilot_index"] == embedding_identity(settings, settings.embedding_model), "Dense embedding identity mismatch")
        profile = metadata["repopilot_chunking"][data["repository"]]
        require(profile["chunking_version"] == "ast-v1"
                and profile["fallback_chunking_version"] == "ast-fallback-line-v1"
                and profile["chunk_lines"] == settings.chunk_lines
                and profile["overlap_lines"] == settings.chunk_overlap_lines
                and profile["max_chunk_characters"] == settings.chunk_max_characters,
                "Stored chunking profile differs from frozen configuration")
        for report_config in (retrieval["retrieval"], grounding["configuration"]):
            require(report_config["collection"] == settings.qdrant_collection and report_config["embedding_model"] == settings.embedding_model,
                    "Report collection/model mismatch")
        require(retrieval["retrieval"]["embedding_dimension"] == collection["config"]["params"]["vectors"]["size"]
                and retrieval["retrieval"]["requested_top_k"] == grounding["configuration"]["top_k"] == 5
                and grounding["configuration"]["llm_model"] == settings.llm_model
                and grounding["configuration"]["max_context_chunks"] == settings.max_context_chunks
                and grounding["configuration"]["max_context_characters"] == settings.max_context_characters,
                "Report dimension/Context configuration mismatch")
        inventory = index_inventory(before, config, data["repository"])
        source_hashes = verify_index_sources(inventory, base.repository_root)
        verify_unchanged(data["source_sha256"], source_hashes, "Indexed source set")
        http_rows, reviewed = replay_inputs(data, base, retrieval, grounding, (paths["grounding"], paths["review"]), inventory)
        for row in grounding["cases"]:
            ordinal = grounding["cases"].index(row) + 1
            receipt = paths["grounding"].parent / f"{paths['grounding'].stem}_cases" / f"{ordinal:02}-{row['id']}.json"
            require(read_json(receipt) == row, "Original per-case receipt differs from report")
        artifacts.mkdir(parents=True)
        diagnostic_dir = artifacts / "top20"
        diagnostics = await collect_diagnostics(data, grounding, inventory, settings, client, diagnostic_dir)
        by_id = {d["id"]: d for d in diagnostics}
        bad_cases = [classify_case(c, r, v, inventory, by_id.get(c["id"])) for c, r, v in
                     zip(data["cases"], grounding["cases"], reviewed["reviews"], strict=True)]
        verify_source_hashes(base)
        verify_unchanged(source_hashes, verify_index_sources(inventory, base.repository_root), "Source")
        require(hashlib.sha256((ROOT / "evaluation/datasets/stage3_grounding.json").read_bytes()).hexdigest() == digest,
                "Grounding dataset changed during freeze")
        verify_unchanged(config, safe_configuration(type(settings)()), "Configuration")
        verify_unchanged(acceptance["runtime"], runtime_identity(), "Runtime/implementation")
        verify_unchanged(before, await full_snapshot(client), "Qdrant index")
        for name, path in paths.items():
            require(file_reference(path) == acceptance["reports"][name], "Input report changed during freeze")
        write_new(artifacts / "index-snapshot.json", before)
        write_new(artifacts / "configuration.json", {"settings": config, "runtime": acceptance["runtime"]})
        write_new(artifacts / "chunks.json", list(inventory.values()))
        write_new(artifacts / "bad-cases.json", bad_cases)
        report = {"schema_version": 1, "status": "complete", "created_at": datetime.now(timezone.utc).isoformat(),
            "kind": "Dense baseline from Task 13 real reports; independent Top20 diagnostics, no generation",
            "inputs": {name: file_reference(path) for name, path in paths.items()}, "acceptance": file_reference(args.acceptance),
            "dataset": {"grounding_sha256": digest, "retrieval_sha256": base.sha256, "source_sha256": source_hashes},
            "identity": {"repository": data["repository"], "collection": settings.qdrant_collection,
                "source_snapshot_sha256": fingerprint(source_hashes), "index_snapshot_sha256": fingerprint(before),
                "repository_index_sha256": fingerprint(list(inventory.values())), "chunk_count": len(inventory),
                "git_revision": None, "native_index_revision": None, "chunking_profile": profile,
                "embedding_dimension": collection["config"]["params"]["vectors"]["size"]},
            "configuration": config, "runtime": acceptance["runtime"],
            "summary": {"http_dense": summarize_retrieval(http_rows), "same_request_grounding": grounding["retrieval_summary"],
                        "answer_quality": reviewed["review_summary"]},
            "http_cases": http_rows, "grounding_cases": grounding["cases"], "reviews": reviewed["reviews"],
            "diagnostics": diagnostics, "bad_cases": bad_cases,
            "additional_calls": {"embedding_provider_calls": sum(d["embedding_provider_calls"] for d in diagnostics), "generation_calls": 0},
            "definitions": {"range_recall": "Complete inclusive evidence ranges covered by union of TopK chunks; not per-line coverage",
                "symbol_denominator": "Only seven canonical queries with named Symbol labels",
                "context": "Actual same-request emitted Sources; HTTP-only Context is separately labelled rebuilt_context",
                "latency": "Original per-query HTTP and retrieval latency; diagnostics measured separately; no generation in Task 1",
                "diagnostic_calls": "Counts provider embed_texts invocations, not SDK transport retries",
                "quality": "Previously observed learning fixture; assistant review is not independent human approval"},
            "preservation": {"sources_unchanged": True, "configuration_unchanged": True, "qdrant_unchanged": True}}
        report["artifacts"] = {name: file_reference(artifacts / filename) for name, filename in (
            ("index", "index-snapshot.json"), ("configuration", "configuration.json"), ("chunks", "chunks.json"), ("bad_cases", "bad-cases.json"))}
        write_new(output, report)
        print(f"Frozen {len(inventory)} chunks, {len(diagnostics)} diagnostic queries: {output}")
    finally:
        await client.close()


def replay_baseline(path):
    """No provider, Qdrant or HTTP calls. Verify bindings and recompute all saved metrics."""
    report = read_json(path)
    require(report["status"] == "complete", "Only complete baselines can be replayed")
    for reference in [*report["inputs"].values(), report["acceptance"], *report["artifacts"].values()]:
        require(file_reference(ROOT / reference["path"]) == reference, "Baseline input/artifact fingerprint mismatch")
    data, digest, base = load_grounding_dataset(ROOT / "evaluation/datasets/stage3_grounding.json", ROOT / "workspaces")
    require(digest == report["dataset"]["grounding_sha256"] and base.sha256 == report["dataset"]["retrieval_sha256"], "Saved dataset changed")
    state = read_json(ROOT / report["artifacts"]["index"]["path"])
    require(fingerprint(state) == report["identity"]["index_snapshot_sha256"], "Saved index fingerprint mismatch")
    saved_config = read_json(ROOT / report["artifacts"]["configuration"]["path"])
    require(saved_config == {"settings": report["configuration"], "runtime": report["runtime"]}, "Saved configuration mismatch")
    inventory = index_inventory(state, report["configuration"], data["repository"])
    require(list(inventory.values()) == read_json(ROOT / report["artifacts"]["chunks"]["path"])
            and len(inventory) == report["identity"]["chunk_count"]
            and fingerprint(list(inventory.values())) == report["identity"]["repository_index_sha256"], "Saved chunk inventory mismatch")
    require(verify_index_sources(inventory, base.repository_root) == report["dataset"]["source_sha256"], "Saved source mismatch")
    inputs = {k: ROOT / v["path"] for k, v in report["inputs"].items()}
    http_rows, reviewed = replay_inputs(data, base, read_json(inputs["retrieval"]), read_json(inputs["grounding"]),
                                      (inputs["grounding"], inputs["review"]), inventory)
    require(http_rows == report["http_cases"] and summarize_retrieval(http_rows) == report["summary"]["http_dense"], "HTTP baseline replay mismatch")
    require(reviewed["review_summary"] == report["summary"]["answer_quality"]
            and reviewed["reviews"] == report["reviews"], "Answer review replay mismatch")
    require(read_json(inputs["grounding"])["cases"] == report["grounding_cases"]
            and summarize_retrieval(report["grounding_cases"]) == report["summary"]["same_request_grounding"], "Grounding baseline replay mismatch")
    by_id = {d["id"]: d for d in report["diagnostics"]}
    for case in data["cases"]:
        if case["id"] in by_id:
            diagnostic = by_id[case["id"]]
            require(diagnostic["request"] == {"repository": case["repository"], "query": case["query"], "top_k": 20}, "Diagnostic request mismatch")
            require(all(diagnostic["response"].get(k) == v for k, v in diagnostic["request"].items())
                    and len(diagnostic["response"]["results"]) <= 20
                    and diagnostic["embedding_provider_calls"] == diagnostic["embedded_texts"] == 1
                    and diagnostic["generation_calls"] == 0, "Diagnostic identity/call count mismatch")
            enriched_results(diagnostic["response"]["results"], inventory)
            original = next(r for r in report["grounding_cases"] if r["id"] == case["id"])["trace"]["retrieval"][0]["results"]
            prefix_matches = [r["chunk_id"] for r in diagnostic["response"]["results"][:5]] == [r["chunk_id"] for r in original]
            require(prefix_matches == diagnostic["prefix_matches_original_top5"], "Diagnostic prefix flag mismatch")
    require(report["additional_calls"] == {"embedding_provider_calls": sum(d["embedding_provider_calls"] for d in report["diagnostics"]),
                                           "generation_calls": 0}, "Additional call totals mismatch")
    bad_cases = [classify_case(c, r, v, inventory, by_id.get(c["id"])) for c, r, v in
                 zip(data["cases"], report["grounding_cases"], report["reviews"], strict=True)]
    require(bad_cases == report["bad_cases"] == read_json(ROOT / report["artifacts"]["bad_cases"]["path"]), "Bad case replay mismatch")
    return {"offline_replay_passed": True, "canonical_queries": 12, "chunk_count": len(inventory),
            "diagnostic_queries": len(by_id), "generation_calls": 0}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--retrieval-report", type=Path)
    parser.add_argument("--grounding-report", type=Path)
    parser.add_argument("--review-report", type=Path)
    parser.add_argument("--acceptance", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.verify_only:
            print(replay_baseline(args.output.resolve()))
        else:
            require(all(getattr(args, k) is not None for k in ("retrieval_report", "grounding_report", "review_report", "acceptance")),
                    "Freeze requires reports and completed Task 13 acceptance")
            asyncio.run(freeze(args))
    except Exception as error:
        # No raw upstream payloads, credentials or accidental partially accepted reports.
        print(f"Baseline failed: {str(error) if isinstance(error, EvaluationError) else type(error).__name__}; no new baseline accepted", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
