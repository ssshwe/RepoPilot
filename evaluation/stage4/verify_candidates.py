"""Real Task 3 compatibility proof using the saved Task 2 vectors, without reindexing."""
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
sys.path.insert(0, str(ROOT / "services/api"))
sys.path.insert(0, str(ROOT))

from qdrant_client import models
from app.citations import CitationValidator
from app.context import ContextBuilder
from app.core.config import get_settings
from app.embedding.base import EmbeddingProvider
from app.embedding.factory import EmbeddingProviderFactory
from app.embedding.identity import embedding_identity
from app.embedding.validation import validate_embedding_batch
from app.retrieval.candidates import CandidateBatch, CandidateLimits, CandidateSearchRequest, SourceMetadata, to_retrieval_results
from app.retrieval.dense import DenseCandidateChannel
from app.retrieval.service import RetrievalService
from app.retrieval.similarity import cosine_similarity
from app.retrieval.vector_store import QdrantVectorStore, create_qdrant_client
from app.schemas.retrieval import RetrievalSearchRequest, RetrievalSearchResponse
from evaluation.retrieval.evaluate import require
from evaluation.stage4.dataset import load_dataset, verify_sources
from evaluation.stage4.evaluate import replay as replay_task2, grouped_summary
from evaluation.stage4.metrics import case_metrics
from evaluation.stage4.state import file_reference, fingerprint, full_snapshot, read_json, safe_configuration, verify_unchanged, write_new


class PinnedQueryEmbedding(EmbeddingProvider):
    """One real embedding per case, shared by both paths to compare identical inputs.

    This cache belongs only to the proof; it is never installed in production.
    """
    def __init__(self, provider):
        self.provider, self.query, self.vector = provider, None, None
        self.calls, self.cache_hits = 0, 0

    @property
    def model_name(self):
        return self.provider.model_name

    def reset(self, query):
        self.query, self.vector = query, None

    async def embed_texts(self, texts):
        require(texts == [self.query], "Proof must embed the exact current case query")
        if self.vector is None:
            self.calls += 1
            self.vector = validate_embedding_batch(await self.provider.embed_texts(texts), 1)[0]
        else:
            self.cache_hits += 1
        return [list(self.vector)]

    async def aclose(self):
        await self.provider.aclose()


def verify_row(case, row, snapshot, config):
    require(row["id"] == case["id"], "Candidate receipt case identity mismatch")
    batch = CandidateBatch.from_dict(row["batch"])
    revision = "sha256:" + fingerprint(snapshot)
    require(batch.repository == case["repository"] and batch.query == case["query"]
            and batch.channel == "dense" and batch.candidate_limit == 30 and batch.index_revision == revision,
            "Candidate request/revision mismatch")
    vector = validate_embedding_batch([row["query_vector"]], 1)[0]
    require(len(vector) == snapshot["config"]["params"]["vectors"]["size"], "Query vector dimension mismatch")
    points = {str(p["id"]): p for p in snapshot["points"]}
    scoped = {key: p for key, p in points.items() if p["payload"]["repository"] == case["repository"]}
    require(len(batch.candidates) == min(30, len(scoped)), "Candidate count differs from indexed repository")
    actual_scores = []
    for candidate in batch.candidates:
        require(candidate.chunk_id in scoped, "Candidate absent from scoped snapshot")
        point = scoped[candidate.chunk_id]
        require(candidate.source_metadata == SourceMetadata.from_payload(point["payload"]), "Candidate source changed")
        require(set(candidate.channel_scores) == {"dense"} and set(candidate.channel_ranks) == {"dense"}
                and candidate.matched_queries == (case["query"],), "Unexpected candidate provenance")
        calculated = cosine_similarity(vector, point["vector"])
        require(math.isclose(calculated, candidate.channel_scores["dense"], abs_tol=2e-6), "Cosine score does not recompute")
        actual_scores.append(candidate.channel_scores["dense"])
    require(actual_scores == sorted(actual_scores, reverse=True), "Dense candidates not ordered by score")
    # On this small saved corpus, also prove no better scored chunk was omitted.
    if actual_scores:
        cutoff = actual_scores[-1]
        chosen = {c.chunk_id for c in batch.candidates}
        require(all(cosine_similarity(vector, p["vector"]) <= cutoff + 2e-6
                    for key, p in scoped.items() if key not in chosen), "Dense pool omits a better-scoring chunk")
    require(set(row["legacy"]) == {"1", "5", "20"}, "Missing legacy TopK comparisons")
    for top_k in (1, 5, 20):
        parsed = RetrievalSearchResponse.model_validate(row["legacy"][str(top_k)], strict=True)
        require(parsed.repository == case["repository"] and parsed.query == case["query"]
                and parsed.top_k == top_k and parsed.embedding_dimension == len(vector)
                and parsed.embedding_model == config["embedding_model"], "Legacy request identity mismatch")
        adapted = to_retrieval_results(batch.candidates, repository=batch.repository,
                                      index_revision=revision, top_k=top_k)
        require(parsed.results == adapted, "Dense candidate prefix differs from legacy results")
    top5 = to_retrieval_results(batch.candidates, repository=batch.repository, index_revision=revision)
    built = ContextBuilder(max_context_chunks=config["max_context_chunks"], max_context_characters=config["max_context_characters"]).build(case["repository"], top5)
    require(json.loads(json.dumps(asdict(built))) == row["context"], "Candidate Context receipt changed")
    CitationValidator().validate(case["repository"], [s.source_id for s in built.sources], context=built, retrieval_results=top5)
    require(case_metrics(case, [r.model_dump() for r in top5], [asdict(s) for s in built.sources]) == row["metrics"], "Candidate metrics do not replay")


def summary(rows, cases):
    normalized = [{"id": row["id"], "metrics": row["metrics"],
                   "retrieval": {"retrieval_latency_ms": row["batch"]["retrieval_latency_ms"]}} for row in rows]
    return {"cases": len(rows), "cases_over_twenty_candidates": sum(len(r["batch"]["candidates"]) > 20 for r in rows),
            "exact_legacy_comparisons": 3 * len(rows), "candidate_limit": 30,
            "metrics": grouped_summary(normalized, cases)}


async def verify(args):
    output = args.output.resolve()
    artifacts = output.parent / f"{output.stem}_artifacts"
    require(not output.exists() and not artifacts.exists(), "Existing candidate report/artifacts must not be overwritten")
    replay_task2(args.reference_report)
    old = read_json(args.reference_report)
    require(not old["holdout_queried"] and old["selected_split"] == "dev", "Development reference required")
    data, digest = load_dataset(ROOT / old["dataset"]["path"], ROOT / "workspaces")
    settings = get_settings()
    config = safe_configuration(settings)
    require(config == old["configuration"], "Reference configuration drift")
    saved = read_json(ROOT / old["artifacts"]["index"]["path"])[old["collection"]]
    cases = [c for c in data["cases"] if c["split"] in {"regression", "dev"}]
    artifacts.mkdir(parents=True)
    client, observed = create_qdrant_client(settings), None
    attempted, before, rows = False, None, []
    name = f"repopilot_stage4_task3_{uuid4().hex}"
    try:
        observed = PinnedQueryEmbedding(EmbeddingProviderFactory.create(settings))
        before = await full_snapshot(client)
        require(name not in before, "Temporary candidate collection name collision")
        original = before[settings.qdrant_collection]
        require(original["config"]["params"]["vectors"] == saved["config"]["params"]["vectors"]
                and original["config"]["metadata"]["repopilot_index"] == saved["config"]["metadata"]["repopilot_index"]
                == embedding_identity(settings, observed.model_name), "Reference index identity drift")
        require(original["points"] == [p for p in saved["points"] if p["payload"]["repository"] == "demo-repository"],
                "Production historical index differs from saved reference")
        attempted = True
        require(await client.create_collection(name, vectors_config=models.VectorParams.model_validate(saved["config"]["params"]["vectors"]),
                                              metadata=saved["config"]["metadata"]), "Temporary creation unconfirmed")
        store = QdrantVectorStore(client, name)
        await store.upsert_points([models.PointStruct(id=p["id"], payload=p["payload"], vector=p["vector"]) for p in saved["points"]])
        snapshot = (await full_snapshot(client))[name]
        require(snapshot["points"] == saved["points"], "Temporary clone changed saved Payload/Vector")
        revision = "sha256:" + fingerprint(snapshot)
        write_new(artifacts / "index-snapshot.json", snapshot)
        write_new(artifacts / "configuration.json", {"settings": config, "limits": asdict(CandidateLimits()),
                   "index_revision": revision, "revision_kind": "caller-bound verified complete snapshot SHA-256; no native revision"})
        channel = DenseCandidateChannel(observed, store, settings, index_revision=revision)
        legacy = RetrievalService(observed, store, settings)
        for ordinal, case in enumerate(cases, 1):
            observed.reset(case["query"])
            try:
                batch = await channel.recall(CandidateSearchRequest(case["repository"], case["query"], 30))
                responses = {str(k): (await legacy.search(RetrievalSearchRequest(repository=case["repository"], query=case["query"], top_k=k))).model_dump()
                             for k in (1, 5, 20)}
                adapted = to_retrieval_results(batch.candidates, repository=case["repository"], index_revision=revision)
                built = ContextBuilder(max_context_chunks=settings.max_context_chunks, max_context_characters=settings.max_context_characters).build(case["repository"], adapted)
                row = {"id": case["id"], "batch": batch.to_dict(), "query_vector": observed.vector,
                       "legacy": responses, "context": json.loads(json.dumps(asdict(built))),
                       "metrics": case_metrics(case, [r.model_dump() for r in adapted], [asdict(s) for s in built.sources])}
                verify_row(case, row, snapshot, config)
                write_new(artifacts / "cases" / f"{ordinal:02}-{case['id']}.json", row)
                rows.append(row)
            except Exception as error:
                write_new(artifacts / "cases" / f"{ordinal:02}-{case['id']}-error.json", {"id": case["id"], "error_type": type(error).__name__, "status": "aborted; case not removed from denominator"})
                raise
            print(f"{ordinal}/{len(cases)} {case['id']}: {len(batch.candidates)} candidates; legacy K=1/5/20 identical", flush=True)
        verify_sources(data, ROOT / "workspaces")
        require(file_reference(ROOT / old["dataset"]["path"])["sha256"] == digest, "Dataset drift")
        verify_unchanged(config, safe_configuration(type(settings)()), "Configuration")
        verify_unchanged(snapshot, (await full_snapshot(client))[name], "Temporary clone after searches")
        report = {"schema_version": 1, "status": "complete", "task": "Stage 4 / Task 3", "created_at": datetime.now(timezone.utc).isoformat(),
                  "reference_report": file_reference(args.reference_report), "dataset": old["dataset"], "configuration": config,
                  "collection": name, "index_revision": revision, "cases": rows, "summary": summary(rows, cases),
                  "calls": {"query_embedding_provider_calls": observed.calls, "proof_embedding_cache_hits": observed.cache_hits,
                            "qdrant_candidate_queries": len(rows), "qdrant_legacy_queries": 3 * len(rows), "indexing_embedding_calls": 0, "generation_calls": 0},
                  "holdout_queried": False, "artifacts": {"index": file_reference(artifacts / "index-snapshot.json"), "configuration": file_reference(artifacts / "configuration.json")},
                  "note": "Same real query vector shared by both paths. Latencies of cached legacy calls are not independent end-to-end performance measurements."}
    finally:
        try:
            if attempted and await client.collection_exists(name):
                require(await client.delete_collection(name), "Temporary candidate collection deletion unconfirmed")
            if attempted:
                require(not await client.collection_exists(name), "Temporary candidate collection remains")
            if before is not None:
                verify_unchanged(before, await full_snapshot(client), "All original collections")
        finally:
            try:
                if observed is not None:
                    await observed.aclose()
            finally:
                await client.close()
    report["preservation"] = {"source_unchanged": True, "original_collections_unchanged": True,
                              "temporary_collection_deleted": True, "temporary_index_unchanged_by_search": True}
    write_new(output, report)
    print(f"Complete: {len(rows)} cases; {report['summary']['exact_legacy_comparisons']} exact comparisons; {output}")


def replay(path):
    report = read_json(path)
    require(report["schema_version"] == 1 and report["status"] == "complete" and not report["holdout_queried"], "Completed development proof required")
    for ref in [report["reference_report"], report["dataset"], *report["artifacts"].values()]:
        require(file_reference(ROOT / ref["path"]) == ref, "Candidate proof input/artifact changed")
    replay_task2(ROOT / report["reference_report"]["path"])
    data, _ = load_dataset(ROOT / report["dataset"]["path"], ROOT / "workspaces")
    cases = [c for c in data["cases"] if c["split"] in {"regression", "dev"}]
    require([r["id"] for r in report["cases"]] == [c["id"] for c in cases], "Candidate proof cases dropped/reordered")
    snapshot = read_json(ROOT / report["artifacts"]["index"]["path"])
    config = read_json(ROOT / report["artifacts"]["configuration"]["path"])
    require(report["index_revision"] == config["index_revision"] == "sha256:" + fingerprint(snapshot)
            and config["settings"] == report["configuration"] and config["limits"] == asdict(CandidateLimits()), "Candidate configuration/revision mismatch")
    artifact_dir = Path(path).parent / f"{Path(path).stem}_artifacts"
    for ordinal, (case, row) in enumerate(zip(cases, report["cases"], strict=True), 1):
        require(read_json(artifact_dir / "cases" / f"{ordinal:02}-{case['id']}.json") == row, "Candidate receipt differs from report")
        require(all(body["collection"] == report["collection"] for body in row["legacy"].values()), "Legacy collection mismatch")
        verify_row(case, row, snapshot, report["configuration"])
    require(summary(report["cases"], cases) == report["summary"], "Candidate summary does not recompute")
    require(report["calls"] == {"query_embedding_provider_calls": len(cases), "proof_embedding_cache_hits": 3 * len(cases),
                               "qdrant_candidate_queries": len(cases), "qdrant_legacy_queries": 3 * len(cases), "indexing_embedding_calls": 0, "generation_calls": 0}, "Candidate call accounting differs")
    return {"offline_replay_passed": True, "cases": len(cases), "exact_legacy_comparisons": 3 * len(cases), "generation_calls": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference-report", type=Path, default=ROOT / "evaluation/reports/stage4_retrieval_20261013_task2.json")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify-only", type=Path)
    args = parser.parse_args()
    if args.verify_only:
        print(replay(args.verify_only.resolve()))
    else:
        args.output = args.output or ROOT / "evaluation/reports" / f"stage4_candidates_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}.json"
        asyncio.run(verify(args))


if __name__ == "__main__":
    main()
