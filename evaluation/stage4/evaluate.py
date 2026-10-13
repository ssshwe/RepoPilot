"""Evaluate Dense on a private temporary index; preserve production and freeze complete receipts."""
import argparse
import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services/api"))
sys.path.insert(0, str(ROOT))

from qdrant_client import models
from app.citations import CitationValidator
from app.context import ContextBuilder
from app.core.config import get_settings
from app.embedding.factory import EmbeddingProviderFactory
from app.embedding.identity import embedding_identity
from app.indexing.ast_chunker import create_ast_code_chunker
from app.indexing.service import prepare_repository, RepositoryIndexer
from app.repository.scanner import RepositoryScanner
from app.retrieval.service import RetrievalService
from app.retrieval.vector_store import QdrantVectorStore, create_qdrant_client
from app.schemas.retrieval import RetrievalSearchRequest, RetrievalSearchResponse
from evaluation.retrieval.evaluate import require
from evaluation.stage4.dataset import load_dataset, verify_sources
from evaluation.stage4.freeze_baseline import CountingEmbedding
from evaluation.stage4.metrics import case_metrics, summarize, DEFINITIONS
from evaluation.stage4.state import fingerprint, file_reference, full_snapshot, read_json, safe_configuration, verify_unchanged, write_new


def check_holdout(split, frozen_config, digest, config):
    if split == "holdout":
        require(frozen_config is not None, "Holdout requires a previously frozen --frozen-config file")
        frozen = read_json(frozen_config)
        require(frozen["dataset_sha256"] == digest and frozen["configuration"] == config,
                "Frozen holdout configuration does not match dataset/current settings")


def verify_retrieval(case, retrieval, workspaces):
    from evaluation.retrieval.evaluate import source_file
    parsed = RetrievalSearchResponse.model_validate(retrieval, strict=True)
    require(parsed.repository == case["repository"] and parsed.query == case["query"] and parsed.top_k == 5,
            "Retrieval request identity mismatch")
    require([r.rank for r in parsed.results] == list(range(1, len(parsed.results) + 1))
            and len(parsed.results) <= 5 and len({r.chunk_id for r in parsed.results}) == len(parsed.results), "Invalid ranked result identity")
    for result in parsed.results:
        require(result.repository == case["repository"], "Foreign repository result")
        lines = source_file(workspaces / case["repository"], result.path).read_bytes().decode("utf-8-sig").splitlines(keepends=True)
        require(result.content == "".join(lines[result.start_line - 1:result.end_line]), "Stale code coordinates/content")
    return parsed


def replay_row(case, row, workspaces):
    parsed = verify_retrieval(case, row["retrieval"], workspaces)
    context = row["context"]
    built = ContextBuilder(max_context_chunks=context["max_context_chunks"], max_context_characters=context["max_context_characters"]).build(case["repository"], parsed.results)
    require(json.loads(json.dumps(asdict(built))) == context, "Actual ContextBuilder receipt does not replay")
    CitationValidator().validate(case["repository"], [s.source_id for s in built.sources], context=built, retrieval_results=parsed.results)
    measured = case_metrics(case, row["retrieval"]["results"], context["sources"])
    require(measured == row["metrics"], "Per-case metrics do not replay")


def grouped_summary(rows, cases):
    by_id = {c["id"]: c for c in cases}
    summaries = {}
    for dimension in ("split", "category", "repository"):
        summaries[dimension] = {value: summarize([r for r in rows if by_id[r["id"]][dimension] == value])
                               for value in sorted({by_id[r["id"]][dimension] for r in rows})}
    return summaries


async def evaluate(args):
    output = args.output.resolve()
    artifacts = output.parent / f"{output.stem}_artifacts"
    require(not output.exists() and not artifacts.exists(), "Existing report/artifact directory must not be overwritten")
    workspaces = ROOT / "workspaces"
    data, digest = load_dataset(args.dataset.resolve(), workspaces)
    settings = get_settings()
    config = safe_configuration(settings)
    check_holdout(args.split, args.frozen_config, digest, config)
    cases = [c for c in data["cases"] if c["split"] in {"regression", args.split}]
    artifacts.mkdir(parents=True)
    write_new(artifacts / "dataset.json", data)
    client = create_qdrant_client(settings)
    provider = None
    try:
        provider = EmbeddingProviderFactory.create(settings)
        observed = CountingEmbedding(provider)
        name = f"repopilot_stage4_task2_{uuid4().hex}"
        before = await full_snapshot(client)
        require(name not in before, "Temporary collection name collision")
    except BaseException:
        try:
            if provider is not None:
                await provider.aclose()
        finally:
            await client.close()
        raise
    rows, indexes, attempted = [], {}, False
    try:
        original = before[settings.qdrant_collection]
        dimension = original["config"]["params"]["vectors"]["size"]
        require(original["config"]["metadata"]["repopilot_index"] == embedding_identity(settings, settings.embedding_model), "Production embedding identity mismatch")
        store = QdrantVectorStore(client, name)
        attempted = True
        await store.prepare_index(dimension, embedding_identity(settings, settings.embedding_model))
        # Preserve historical Dense vectors exactly; new fixtures are indexed only in this private collection.
        historical = [p for p in original["points"] if p["payload"]["repository"] == "demo-repository"]
        require(bool(historical), "Historical repository index required")
        await store.upsert_points([models.PointStruct(id=p["id"], vector=p["vector"], payload=p["payload"]) for p in historical])
        await store.record_repository_chunking("demo-repository", original["config"]["metadata"]["repopilot_chunking"]["demo-repository"])
        indexes["demo-repository"] = {"kind": "copied exact production Payload/Vector", "chunks": len(historical), "embedding_calls": 0}
        scanner = RepositoryScanner(workspaces)
        for repository in data["repositories"]:
            if repository == "demo-repository":
                continue
            prepared = prepare_repository(repository, scanner, create_ast_code_chunker(), settings.indexing_max_text_characters)
            calls = observed.calls
            indexed = await RepositoryIndexer(observed, store, settings).index(prepared)
            indexes[repository] = indexed.model_dump() | {"embedding_provider_calls": observed.calls - calls}
        corpus = await full_snapshot(client)
        write_new(artifacts / "index-snapshot.json", {name: corpus[name]})
        service = RetrievalService(observed, store, settings)
        builder = ContextBuilder(max_context_chunks=settings.max_context_chunks, max_context_characters=settings.max_context_characters)
        indexing_calls = observed.calls
        for ordinal, case in enumerate(cases, 1):
            request = RetrievalSearchRequest(repository=case["repository"], query=case["query"], top_k=5)
            try:
                result = await service.search(request)
            except Exception as error:
                write_new(artifacts / "cases" / f"{ordinal:02}-{case['id']}-error.json",
                          {"id": case["id"], "request": request.model_dump(), "error_type": type(error).__name__,
                           "status": "failed; no metrics; complete run aborted"})
                raise
            context = builder.build(case["repository"], result.results)
            row = {"id": case["id"], "request": request.model_dump(), "retrieval": result.model_dump(),
                   "context": json.loads(json.dumps(asdict(context))),
                   "metrics": case_metrics(case, [r.model_dump() for r in result.results], [asdict(s) for s in context.sources])}
            replay_row(case, row, workspaces)
            write_new(artifacts / "cases" / f"{ordinal:02}-{case['id']}.json", row)
            rows.append(row)
            print(f"{ordinal}/{len(cases)} {case['split']} {case['id']}: {len(result.results)} chunks", flush=True)
        verify_sources(data, workspaces)
        require(file_reference(args.dataset)["sha256"] == digest, "Dataset changed during run")
        verify_unchanged(config, safe_configuration(type(settings)()), "Configuration")
        after_search = await full_snapshot(client)
        verify_unchanged(corpus[name], after_search[name], "Evaluation index")
        report = {"schema_version": 1, "status": "complete", "created_at": datetime.now(timezone.utc).isoformat(),
                  "kind": "real Dense service + actual ContextBuilder; no generation; private index with cross-repository distractors",
                  "dataset": file_reference(args.dataset), "dataset_sha256": digest, "configuration": config,
                  "strategy": "dense", "top_k": 5, "selected_split": args.split, "holdout_queried": args.split == "holdout",
                  "indexes": indexes, "collection": name, "definitions": DEFINITIONS, "cases": rows,
                  "summary": grouped_summary(rows, cases), "calls": {"indexing_embedding_provider_calls": indexing_calls,
                     "query_embedding_provider_calls": observed.calls - indexing_calls, "generation_calls": 0},
                  "artifacts": {"dataset": file_reference(artifacts / "dataset.json"), "index": file_reference(artifacts / "index-snapshot.json")},
                  "context_note": "Actual Builder output from each retrieval; no generation performed, not claimed sent to a model",
                  "preservation": {"source_unchanged": True, "temporary_index_unchanged_by_search": True}}
    finally:
        try:
            if attempted and await client.collection_exists(name):
                require(await client.delete_collection(name), "Temporary collection cleanup unconfirmed")
            require(not await client.collection_exists(name), "Temporary collection still exists")
            verify_unchanged(before, await full_snapshot(client), "All original Qdrant collections")
        finally:
            await provider.aclose()
            await client.close()
    report["preservation"] |= {"temporary_collection_deleted": True, "original_collections_unchanged": True}
    write_new(output, report)
    print(f"Complete: {len(rows)} real queries; holdout queried={report['holdout_queried']}; {output}")


def replay(path):
    report = read_json(path)
    require(report["status"] == "complete", "Completed report required")
    for reference in [report["dataset"], *report["artifacts"].values()]:
        require(file_reference(ROOT / reference["path"]) == reference, "Dataset/artifact fingerprint changed")
    data, digest = load_dataset(ROOT / report["dataset"]["path"], ROOT / "workspaces")
    require(digest == report["dataset_sha256"], "Dataset fingerprint mismatch")
    cases = [c for c in data["cases"] if c["split"] in {"regression", report["selected_split"]}]
    require([r["id"] for r in report["cases"]] == [c["id"] for c in cases], "Cases dropped/reordered")
    snapshot = read_json(ROOT / report["artifacts"]["index"]["path"])[report["collection"]]
    points = {str(p["id"]): p["payload"] for p in snapshot["points"]}
    artifact_dir = Path(path).parent / f"{Path(path).stem}_artifacts"
    require(read_json(ROOT / report["artifacts"]["dataset"]["path"]) == data, "Saved dataset differs")
    require(report["definitions"] == DEFINITIONS, "Metric definitions changed; use the matching evaluator version")
    for ordinal, (case, row) in enumerate(zip(cases, report["cases"], strict=True), 1):
        require(read_json(artifact_dir / "cases" / f"{ordinal:02}-{case['id']}.json") == row, "Per-case receipt differs from report")
        for result in row["retrieval"]["results"]:
            require(result["chunk_id"] in points and all(points[result["chunk_id"]].get(k) == v
                    for k, v in result.items() if k not in {"rank", "score"}), "Result differs from frozen index identity")
        replay_row(case, row, ROOT / "workspaces")
    require(grouped_summary(report["cases"], cases) == report["summary"], "Summary does not recompute")
    return {"offline_replay_passed": True, "cases": len(cases), "holdout_queried": report["holdout_queried"], "generation_calls": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=ROOT / "evaluation/datasets/stage4_retrieval.json")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--split", choices=("dev", "holdout"), default="dev")
    parser.add_argument("--frozen-config", type=Path)
    parser.add_argument("--verify-only", type=Path)
    args = parser.parse_args()
    if args.verify_only:
        print(replay(args.verify_only.resolve()))
    else:
        args.output = args.output or ROOT / "evaluation/reports" / f"stage4_retrieval_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}.json"
        asyncio.run(evaluate(args))


if __name__ == "__main__":
    main()
