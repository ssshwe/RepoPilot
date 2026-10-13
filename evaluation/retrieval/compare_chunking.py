"""Compare Simple and AST with real dense retrieval in disposable Qdrant indexes."""
import argparse
import asyncio
from collections.abc import Sequence
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services/api"))

sys.path.insert(0, str(ROOT))
from evaluation.retrieval.evaluate import (
    KS, EvaluationError, load_dataset, metrics_for_case, require,
    summarize, validate_response, verify_source_hashes,
)
from app.core.config import get_settings
from app.embedding.base import EmbeddingProvider
from app.embedding.factory import EmbeddingProviderFactory
from app.embedding.validation import validate_embedding_batch
from app.indexing.ast_chunker import create_ast_code_chunker
from app.indexing.chunker import create_code_chunker
from app.indexing.service import RepositoryIndexer, prepare_repository
from app.repository.scanner import create_repository_scanner
from app.retrieval.service import RetrievalService
from app.retrieval.vector_store import QdrantVectorStore, create_qdrant_client
from app.schemas.retrieval import RetrievalSearchRequest


def fingerprint(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode()).hexdigest()


def evidence_metrics(results: list[dict], evidence: list[dict]) -> dict:
    """Supplement file metrics: cover every annotated line, possibly across chunks."""
    metrics = {}
    for k in KS:
        covered = []
        for entry in evidence:
            intervals = sorted((r["start_line"], r["end_line"]) for r in results[:k]
                               if r["path"] == entry["path"])
            cursor = entry["start_line"]
            for start, end in intervals:
                if start > cursor:
                    break
                cursor = max(cursor, end + 1)
            if cursor > entry["end_line"]:
                covered.append(entry["path"])
        metrics[str(k)] = dict(hit=bool(covered), recall=len(covered) / len(evidence),
                               matched_paths=sorted(covered))
    return metrics


class SharedQueryEmbedding(EmbeddingProvider):
    """Reuse one REAL provider output for both searches, never a synthetic vector."""
    def __init__(self, model: str, query: str, vector: list[float]):
        self.model, self.query, self.vector = model, query, vector

    @property
    def model_name(self) -> str:
        return self.model

    async def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        require(list(texts) == [self.query], "Shared embedding used for a different query")
        return [self.vector.copy()]


async def snapshot(client, names: list[str]) -> dict:
    state = {}
    for name in names:
        info = await client.get_collection(name)
        points, offset = [], None
        while True:
            page, offset = await client.scroll(name, limit=100, offset=offset,
                                               with_payload=True, with_vectors=True)
            points.extend(p.model_dump(mode="json") for p in page)
            if offset is None:
                break
        points.sort(key=lambda point: str(point["id"]))
        state[name] = dict(metadata=info.config.metadata,
                           vectors=info.config.params.vectors.model_dump(mode="json"),
                           points_count=len(points), points_sha256=fingerprint(points))
    return state


async def compare(dataset, dataset_path: Path, progress=print) -> dict:
    settings = get_settings()
    scanner = create_repository_scanner()
    require(dataset.repository_root == scanner.workspaces_root / dataset.data["repository"],
            "Dataset must use the configured repository workspace")
    chunkers = {"simple": create_code_chunker(), "ast": create_ast_code_chunker()}
    require(chunkers["simple"].max_chunk_characters == chunkers["ast"].max_chunk_characters,
            "Both strategies must use the same character budget")
    prepared = {kind: prepare_repository(dataset.data["repository"], scanner, chunker,
                                        settings.indexing_max_text_characters)
                for kind, chunker in chunkers.items()}
    source_hashes = {f.path: hashlib.sha256(f.content.encode()).hexdigest()
                     for f in prepared["simple"].scan.files}
    require(source_hashes == {f.path: hashlib.sha256(f.content.encode()).hexdigest()
                             for f in prepared["ast"].scan.files}, "Scanned source changed between strategies")
    provider = EmbeddingProviderFactory.create(settings)
    try:
        client = create_qdrant_client(settings)
        try:
            return await _compare_with_clients(dataset, dataset_path, settings, scanner, prepared,
                                               source_hashes, provider, client, progress)
        finally:
            await client.close()
    finally:
        await provider.aclose()


async def _compare_with_clients(dataset, dataset_path, settings, scanner, prepared,
                                source_hashes, provider, client, progress):
    original_names = sorted(c.name for c in (await client.get_collections()).collections)
    original = await snapshot(client, original_names)
    suffix = uuid4().hex
    names = {kind: f"repopilot_chunk_compare_{kind}_{suffix}" for kind in prepared}
    report, rows, indexes = None, [], {}
    # Names are private to this run. Never delete a pre-existing collection.
    require(not set(names.values()).intersection(original_names), "Temporary name already exists")
    try:
        stores = {kind: QdrantVectorStore(client, name) for kind, name in names.items()}
        for kind in prepared:
            progress(f"Indexing {kind}: {len(prepared[kind].chunks)} real chunks", flush=True)
            indexed = await RepositoryIndexer(provider, stores[kind], settings).index(prepared[kind])
            indexes[kind] = indexed.model_dump(mode="json")
        require(indexes["simple"]["embedding_dimension"] == indexes["ast"]["embedding_dimension"],
                "Comparison embedding dimensions differ")
        temporary_before = await snapshot(client, list(names.values()))
        for index, case in enumerate(dataset.data["cases"], 1):
            vector = validate_embedding_batch(await provider.embed_texts([case["query"]]), 1)[0]
            shared = SharedQueryEmbedding(provider.model_name, case["query"], vector)
            request = RetrievalSearchRequest(repository=dataset.data["repository"],
                                             query=case["query"], top_k=max(KS))
            row = {key: case[key] for key in ("id", "query", "expected_paths", "evidence")}
            row["query_vector_sha256"] = fingerprint(vector)
            for kind, store in stores.items():
                response = (await RetrievalService(shared, store, settings).search(request)).model_dump(mode="json")
                validated = validate_response(response, request.model_dump(), dataset.repository_root)
                results = validated["results"]
                for result, raw in zip(results, response["results"], strict=True):
                    result.update({key: raw[key] for key in (
                        "symbol", "node_type", "parent_symbol", "chunking_strategy", "chunking_version")})
                row[kind] = dict(results=results,
                    metrics=metrics_for_case([r["path"] for r in results], case["expected_paths"]),
                    evidence_metrics=evidence_metrics(results, case["evidence"]))
            rows.append(row)
            progress(f"[{index}/{len(dataset.data['cases'])}] {case['id']}: compared", flush=True)
        verify_source_hashes(dataset)
        require(hashlib.sha256(dataset_path.read_bytes()).hexdigest() == dataset.sha256,
                "Dataset changed during comparison")
        rescanned = scanner.scan(dataset.data["repository"])
        require(source_hashes == {f.path: hashlib.sha256(f.content.encode()).hexdigest()
                                 for f in rescanned.files}, "Indexed source changed during comparison")
        require(await snapshot(client, list(names.values())) == temporary_before,
                "Search changed temporary indexes")
        report = dict(status="complete", generated_at=datetime.now(timezone.utc).isoformat(),
            dataset=dict(name=dataset.data["name"], sha256=dataset.sha256,
                         source_sha256=dataset.data["source_sha256"]),
            method=dict(retrieval="dense", distance="Cosine", query_embedding="one real vector shared per query",
                        embedding_model=provider.model_name, dimension=indexes["ast"]["embedding_dimension"],
                        ks=list(KS), top_k=max(KS), ranking_unit="code_chunk", relevance_unit="unique_source_path",
                        evidence_policy="All inclusive annotated lines covered by the union of TopK chunks",
                        latency_comparison=False, scanned_content_sha256=source_hashes),
            indexes=indexes, summary={kind: summarize([row[kind] for row in rows]) for kind in stores},
            evidence_summary={kind: summarize([dict(metrics=row[kind]["evidence_metrics"]) for row in rows])
                              for kind in stores}, queries=rows)
    finally:
        cleanup_errors = []
        for name in names.values():
            try:
                if await client.collection_exists(name):
                    require(await client.delete_collection(name), "Temporary collection deletion unconfirmed")
            except Exception:
                cleanup_errors.append(name)
        # Attempt both deletions even if one fails. Surface the exact temporary
        # names for recovery, without printing any upstream error body.
        require(not cleanup_errors, "Could not clean temporary collections: " + ", ".join(cleanup_errors))
        after = await snapshot(client, original_names)
        require(after == original, "An existing collection changed during comparison")
        require(not set(names.values()).intersection(c.name for c in (await client.get_collections()).collections),
                "Temporary comparison collections remain")
        if report is not None:
            report["preservation"] = dict(existing_collections_unchanged=True,
                temporary_collections_deleted=True, query_indexes_unchanged=True,
                before=original, after=after)
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=ROOT / "evaluation/datasets/stage2_retrieval.json")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    output = args.output or ROOT / "evaluation/reports" / (
        "stage3_chunking_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json")
    try:
        require(not output.exists(), "Output already exists; choose a new report filename")
        dataset = load_dataset(args.dataset, ROOT / "workspaces")
        report = asyncio.run(compare(dataset, args.dataset))
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        for kind, summary in report["summary"].items():
            print(f"{kind}: " + "; ".join(
                f"Hit@{k}={summary['hit_at_k'][str(k)]['rate']:.2%}, Recall@{k}={summary['macro_recall_at_k'][str(k)]:.2%}"
                for k in KS))
        print(f"Report: {output.resolve()}")
        return 0
    except Exception as error:
        # Do not print upstream bodies, vectors, code, configuration or credentials.
        detail = str(error) if isinstance(error, EvaluationError) else type(error).__name__
        print(f"Comparison failed: {detail}; no completed report saved", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
