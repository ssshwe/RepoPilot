"""Task 7 locator/filters spike, frozen compatibility and real Qdrant evidence."""
import argparse
import asyncio
import json
import math
from pathlib import Path
import sys
from datetime import datetime, timezone
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "services/api"), str(ROOT)]

from qdrant_client import models
from app.core.config import get_settings
from app.retrieval.bm25 import BM25Index
from app.retrieval.candidates import CandidateSearchRequest, SourceMetadata, to_retrieval_results
from app.retrieval.dense import DenseCandidateChannel
from app.retrieval.exact import ExactCandidateBatch, ExactCandidateChannel, ExactMatch, ExactSearchRequest
from app.retrieval.filtered import FilteredBM25Channel, FilteredDenseChannel
from app.retrieval.scope import SCOPE_VERSION, ScopedCandidateBatch, ScopedSearchRequest, SearchFilters
from app.retrieval.snapshot_models import IndexedRepository, LexicalSnapshot, SnapshotScope, digest
from app.retrieval.snapshot_store import SnapshotStore
from app.retrieval.snapshots import QdrantImageReader, SnapshotCoordinator
from app.retrieval.similarity import cosine_similarity
from app.retrieval.tokenizer import CodeTokenizer
from app.retrieval.vector_store import QdrantVectorStore, create_qdrant_client
from evaluation.retrieval.evaluate import require
from evaluation.stage4.evaluate_bm25 import inputs
from evaluation.stage4.verify_snapshots import FrozenReader
from evaluation.stage4.state import file_reference, full_snapshot, read_json, safe_configuration, verify_unchanged, write_new

BM25_REPORT = ROOT / "evaluation/reports/stage4_bm25_20261026_task6_final.json"
CORE = ("services/api/app/retrieval/scope.py", "services/api/app/retrieval/filtered.py", "services/api/app/retrieval/exact.py")


def probe(name, kind, value, count, *, mode="full", filters=None, repository="stage4-commerce", query=None, strength="full"):
    return {"id": name, "repository": repository, "query": query or value, "limit": 10,
            "selector": {"kind": kind, "value": value, "mode": mode} if kind else None,
            "filters": filters or {}, "expected_count": count, "expected_strength": strength}


PROBES = [
    probe("full-symbol", "symbol", "WalletService.credit", 2),
    probe("short-symbol", "short_symbol", "credit", 2),
    probe("missing-symbol", "symbol", "WalletService.missing", 0),
    probe("wrong-case", "symbol", "walletservice.credit", 0),
    probe("formal-filter", "symbol", "WalletService.credit", 1, filters={"language": "java", "path_prefix": "backend/wallet/"}),
    probe("legacy-filter", "symbol", "WalletService.credit", 1, filters={"language": "java", "path_prefix": "backend/legacy/"}),
    probe("full-path", "path", "backend/wallet/WalletService.java", 4),
    probe("filename", "filename", "WalletService.java", 7),
    probe("partial-filename", "filename", "Wallet", 7, mode="partial", strength="partial"),
    probe("full-strength-in-partial-mode", "short_symbol", "credit", 2, mode="partial"),
    probe("error-text", "text", "Amount must be positive", 1),
    probe("partial-error", "text", "must be positive", 1, mode="partial", strength="partial"),
    probe("quoted-literal", "literal", '"PAID"', 1, filters={"path_prefix": "backend/orders/"}),
    probe("numeric-full", "literal", "1000", 1, filters={"language": "json"}),
    probe("numeric-substring-rejected", "literal", "0", 0, filters={"language": "json"}),
    probe("numeric-partial-explicit", "literal", "0", 1, mode="partial", filters={"language": "json"}, strength="partial"),
    probe("unknown-language", "symbol", "WalletService.credit", 0, filters={"language": "python"}),
    probe("unknown-path", "symbol", "WalletService.credit", 0, filters={"path_prefix": "backend/absent/"}),
    probe("auto-natural-symbol", None, "WalletService.credit 的正式与 legacy 实现在哪里？", 2),
    probe("auto-missing-symbol", None, "WalletService.missing 在哪里？", 0),
    probe("auto-ambiguous", None, "WalletService.credit 和 RefundService.refund", 0),
    probe("auto-quoted-error", None, '哪个实现抛出错误 "Amount must be positive"？', 1),
    probe("other-repository", "symbol", "WalletService.credit", 1, repository="stage4-shadow"),
    probe("empty-repository", "symbol", "WalletService.credit", 0, repository="stage4-empty"),
]


def request_for(definition):
    common = (definition["repository"], definition["query"], definition["limit"])
    filters = SearchFilters(**definition["filters"])
    return ExactSearchRequest(*common, filters=filters, match=ExactMatch(**definition["selector"])) if definition["selector"] else ScopedSearchRequest(*common, filters=filters)


def compact(batch):
    return {"index_revision": batch.index_revision, "scope_fingerprint": batch.filters.fingerprint(batch.repository),
            "candidates_sha256": digest([c.to_dict() for c in batch.candidates]), "count": len(batch.candidates)}


def validate_probe(definition, row, snapshot):
    exact = ExactCandidateBatch.from_dict(row["exact"])
    bm25 = ScopedCandidateBatch.from_dict(row["bm25"])
    filters = SearchFilters(**definition["filters"])
    allowed = {c.chunk_id: c for c in snapshot.index.chunks if filters.matches(c.source)}
    require(row["eligible_ids"] == sorted(allowed), "Filter eligibility differs from the frozen source")
    for batch in (exact, bm25):
        require(batch.repository == definition["repository"] and batch.query == definition["query"]
                and batch.filters == filters and batch.index_revision == snapshot.index.index_revision, "Probe scope/revision mismatch")
        require(all(c.chunk_id in allowed and c.source_metadata == allowed[c.chunk_id].source for c in batch.candidates), "Out-of-scope source in probe")
    require(len(exact.candidates) == definition["expected_count"], "Exact probe count differs from the declared contract")
    require(all(reason.strength == definition["expected_strength"] for reason in exact.reasons.values()), "Full/partial reason mismatch")
    require(exact.match == (ExactMatch(**definition["selector"]) if definition["selector"] else ExactMatch.infer(definition["query"])), "Exact intent was changed")


class ArchivedEmbedding:
    """Only reuses one original real query vector; never calls an embedding API."""
    def __init__(self, model, query, vector): self.model_name, self.query, self.vector, self.calls = model, query, vector, 0
    async def embed_texts(self, texts):
        require(texts == [self.query], "Archived vector must only be used for its original question")
        self.calls += 1
        return [list(self.vector)]


LIVE_FILTERS = [("all", {}), ("formal", {"language": "java", "path_prefix": "backend/wallet/"}),
                ("legacy", {"language": "java", "path_prefix": "backend/legacy/"}),
                ("json", {"language": "json"}), ("empty", {"language": "python"})]


async def live_verify(path, frozen_images):
    settings = get_settings()
    archive_path = ROOT / "evaluation/reports/stage4_candidates_20261013_task3.json"
    archive = read_json(archive_path)
    saved = read_json(ROOT / archive["artifacts"]["index"]["path"])
    selected = next(row for row in archive["cases"] if row["id"] == "wallet-symbol")
    query = selected["batch"]["query"]
    provider = ArchivedEmbedding(settings.embedding_model, query, selected["query_vector"])
    client = create_qdrant_client(settings)
    name = "repopilot_stage4_task7_" + uuid4().hex
    attempted, before, rows = False, None, []
    try:
        before = await full_snapshot(client)
        require(name not in before, "Private Collection name collision")
        attempted = True
        require(await client.create_collection(name, vectors_config=models.VectorParams.model_validate(saved["config"]["params"]["vectors"]),
                                               metadata=saved["config"]["metadata"]), "Private Collection creation unconfirmed")
        qstore = QdrantVectorStore(client, name)
        await qstore.upsert_points([models.PointStruct(id=p["id"], vector=p["vector"], payload=p["payload"]) for p in saved["points"]])
        cloned = (await full_snapshot(client))[name]
        require(cloned["points"] == saved["points"], "Copy changed original indexed Point bytes")
        index_path = path.parent / "live-index.json"
        write_new(index_path, cloned)
        private = settings.model_copy(update={"qdrant_collection": name})
        reader = QdrantImageReader(qstore, private)
        store = SnapshotStore(ROOT / ".tools" / ("stage4-task7-" + uuid4().hex) / "live.sqlite3")
        coordinator = SnapshotCoordinator(store, reader)
        snapshot = await coordinator.rebuild("stage4-commerce", expected_source_sha256=frozen_images["stage4-commerce"].source_sha256)
        dense, bm25, exact = FilteredDenseChannel(provider, private, coordinator), FilteredBM25Channel(coordinator), ExactCandidateChannel(coordinator)
        native = []
        original_query = client.query_points
        async def trace_query(*args, **kwargs):
            result = await original_query(*args, **kwargs)
            native.append({"filter": kwargs["query_filter"].model_dump(mode="json"), "limit": kwargs["limit"],
                           "returned_points": [p.model_dump(mode="json") for p in result.points]})
            return result
        client.query_points = trace_query
        for label, values in LIVE_FILTERS:
            filters = SearchFilters(**values)
            request = ExactSearchRequest("stage4-commerce", query, 30, filters=filters, match=ExactMatch("symbol", "WalletService.credit"))
            eligible = {c.chunk_id: c for c in snapshot.index.chunks if filters.matches(c.source)}
            native.clear()
            dense_batch = await dense.recall(request)
            query_trace = list(native)
            bm25_batch, exact_batch = await bm25.recall(request), await exact.recall(request)
            for batch in (dense_batch, bm25_batch, exact_batch):
                require(batch.filters == filters and batch.index_revision == snapshot.index.index_revision
                        and all(c.chunk_id in eligible and c.source_metadata == eligible[c.chunk_id].source for c in batch.candidates), "Channels disagree about live filtered code scope")
            require(len(dense_batch.candidates) == min(30, len(eligible)), "Dense filter was applied after candidate limit")
            require({c.chunk_id for c in exact_batch.candidates} == {key for key, c in eligible.items() if c.source.symbol == "WalletService.credit"}, "Live exact Symbol set differs")
            if filters == SearchFilters():
                legacy = await DenseCandidateChannel(provider, qstore, private, index_revision=snapshot.index.index_revision).recall(
                    CandidateSearchRequest("stage4-commerce", query, 30))
                require([c.to_dict() for c in legacy.candidates] == [c.to_dict() for c in dense_batch.candidates], "Unfiltered Dense behavior changed")
            if eligible:
                require(len(query_trace) == 1 and query_trace[0]["limit"] == 30, "Dense query trace missing")
                id_conditions = [c for c in query_trace[0]["filter"]["must"] if "has_id" in c]
                require((not id_conditions) if filters == SearchFilters() else (len(id_conditions) == 1 and id_conditions[0]["has_id"] == sorted(eligible)), "Qdrant did not filter eligible IDs before ranking")
            else:
                require(not query_trace, "Empty eligibility should not issue a Dense query")
            row = {"id": label, "filters": filters.to_dict(), "eligible_ids": sorted(eligible), "dense": dense_batch.to_dict(),
                   "bm25": bm25_batch.to_dict(), "exact": exact_batch.to_dict(), "native_dense_query": query_trace}
            write_new(path.parent / ("live-" + label + ".json"), row)
            rows.append(row)
            print("Live " + label + ": eligible=" + str(len(eligible)) + ", exact=" + str(len(exact_batch.candidates)), flush=True)
        require(cloned == (await full_snapshot(client))[name], "Read-only filtered search changed the private index")
        result = {"archive": file_reference(archive_path), "archived_case_id": "wallet-symbol", "index": file_reference(index_path),
                  "scope": snapshot.index.scope.to_dict(), "rows": rows, "unfiltered_dense_compatible": True,
                  "archived_vector_reuses": provider.calls, "real_embedding_calls": 0, "generation_calls": 0}
    finally:
        try:
            if attempted and await client.collection_exists(name): require(await client.delete_collection(name), "Private Collection cleanup unconfirmed")
            if attempted: require(not await client.collection_exists(name), "Private Collection remains")
            if before is not None: verify_unchanged(before, await full_snapshot(client), "All original Qdrant collections")
        finally: await client.close()
    result["temporary_collection_deleted"] = True
    write_new(path, result)


async def compatibility(store, images, cases, expected_rows):
    channel = FilteredBM25Channel(SnapshotCoordinator(store, FrozenReader(images)))
    rows = []
    for case, expected in zip(cases, expected_rows, strict=True):
        require(case["id"] == expected["id"], "Compatibility cases reordered")
        batch = await channel.recall(ScopedSearchRequest(case["repository"], case["query"], 30))
        require([c.to_dict() for c in batch.candidates] == expected["batch"]["candidates"]
                and batch.index_revision == expected["batch"]["index_revision"], "Unfiltered BM25 behavior changed")
        rows.append({"id": case["id"], **compact(batch)})
    return rows


async def evaluate(args):
    output = args.output.resolve()
    artifacts = output.parent / (output.stem + "_artifacts")
    require(not output.exists() and not artifacts.exists(), "Existing Exact report/artifacts must not be overwritten")
    _, _, _, cases, store, images = inputs()
    bm25_reference = read_json(BM25_REPORT)
    coordinator = SnapshotCoordinator(store, FrozenReader(images))
    exact, bm25 = ExactCandidateChannel(coordinator), FilteredBM25Channel(coordinator)
    artifacts.mkdir(parents=True)
    rows = []
    for definition in PROBES:
        request = request_for(definition)
        snapshot = store.load_ready(images[request.repository].scope).snapshot
        try:
            exact_batch, bm25_batch = await exact.recall(request), await bm25.recall(request)
            row = {"id": definition["id"], "exact": exact_batch.to_dict(), "bm25": bm25_batch.to_dict(),
                   "eligible_ids": sorted(c.chunk_id for c in snapshot.index.chunks if request.filters.matches(c.source))}
            validate_probe(definition, row, snapshot)
            write_new(artifacts / "probes" / (definition["id"] + ".json"), row)
            rows.append(row)
            print(definition["id"] + ": " + str(len(exact_batch.candidates)) + " exact matches", flush=True)
        except Exception as exc:
            write_new(artifacts / "probes" / (definition["id"] + "-error.json"), {"id": definition["id"], "error_type": type(exc).__name__, "status": "aborted; no omitted probe"})
            raise
    compatible = await compatibility(store, images, cases, bm25_reference["cases"])
    live_ref = None
    if args.live:
        path = artifacts / "live-summary.json"
        await live_verify(path, images)
        live_ref = file_reference(path)
    report = {"schema_version": 1, "task": "Stage 4 / Task 7", "status": "complete", "created_at": datetime.now(timezone.utc).isoformat(),
              "bm25_reference": file_reference(BM25_REPORT), "implementations": {name: file_reference(ROOT / name) for name in CORE},
              "scope_version": SCOPE_VERSION, "exact_version": ExactCandidateChannel.version, "definitions": PROBES,
              "probes": rows, "unfiltered_bm25_compatibility": compatible, "live": live_ref,
              "real_embedding_calls": 0, "generation_calls": 0, "holdout_queried": False,
              "note": "Functional locator/filter probes are separate from the unchanged 52-case retrieval benchmark"}
    write_new(output, report)
    print("Complete: 24 Exact probes, 52 BM25 compatibility queries; " + str(output))


async def replay(path):
    report = read_json(path)
    require(report["task"] == "Stage 4 / Task 7" and report["status"] == "complete" and report["schema_version"] == 1
            and report["definitions"] == PROBES and report["scope_version"] == SCOPE_VERSION
            and report["exact_version"] == ExactCandidateChannel.version and not report["holdout_queried"], "Completed Exact/filter contract receipt required")
    require(report["bm25_reference"] == file_reference(BM25_REPORT), "Wrong BM25 comparison report association")
    for ref in [report["bm25_reference"], *report["implementations"].values()]:
        require(file_reference(ROOT / ref["path"]) == ref, "Exact/filter input implementation drift")
    require(report["implementations"] == {name: file_reference(ROOT / name) for name in CORE}, "Wrong Exact/filter implementation association")
    _, _, _, cases, store, images = inputs()
    coordinator = SnapshotCoordinator(store, FrozenReader(images))
    exact, bm25 = ExactCandidateChannel(coordinator), FilteredBM25Channel(coordinator)
    require([r["id"] for r in report["probes"]] == [p["id"] for p in PROBES], "Exact probes dropped/reordered")
    artifacts = Path(path).parent / (Path(path).stem + "_artifacts")
    for definition, row in zip(PROBES, report["probes"], strict=True):
        require(read_json(artifacts / "probes" / (definition["id"] + ".json")) == row, "Exact per-probe receipt differs")
        request = request_for(definition)
        snapshot = store.load_ready(images[request.repository].scope).snapshot
        validate_probe(definition, row, snapshot)
        generated = [await exact.recall(request), await bm25.recall(request)]
        for key, batch in zip(("exact", "bm25"), generated, strict=True):
            actual, original = batch.to_dict(), dict(row[key])
            actual.pop("retrieval_latency_ms"); original.pop("retrieval_latency_ms")
            require(actual == original, "Exact/filtered BM25 ranks or diagnostics do not recompute")
    reference = read_json(ROOT / report["bm25_reference"]["path"])
    require(await compatibility(store, images, cases, reference["cases"]) == report["unfiltered_bm25_compatibility"], "Legacy BM25 compatibility receipt changed")
    if report["live"]:
        ref = report["live"]
        require(file_reference(ROOT / ref["path"]) == ref, "Live Exact/filter summary changed")
        live = read_json(ROOT / ref["path"])
        for value in (live["archive"], live["index"]): require(file_reference(ROOT / value["path"]) == value, "Live indexed/vector evidence changed")
        archive = read_json(ROOT / live["archive"]["path"])
        vector = next(r for r in archive["cases"] if r["id"] == live["archived_case_id"])["query_vector"]
        state = read_json(ROOT / live["index"]["path"])
        scope = SnapshotScope(**live["scope"])
        image = IndexedRepository.from_records(scope, {"dimension": state["config"]["params"]["vectors"]["size"], "distance": "Cosine",
            "embedding": state["config"]["metadata"]["repopilot_index"], "chunking": state["config"]["metadata"]["repopilot_chunking"][scope.repository]},
            [p for p in state["points"] if p["payload"]["repository"] == scope.repository])
        points = {str(p["id"]): p for p in state["points"]}
        require([r["id"] for r in live["rows"]] == [name for name, _ in LIVE_FILTERS], "Live filter scenarios dropped")
        for row, (_, values) in zip(live["rows"], LIVE_FILTERS, strict=True):
            require(read_json(Path(ROOT / ref["path"]).parent / ("live-" + row["id"] + ".json")) == row, "Live per-scenario receipt differs")
            validate_live_row(row, SearchFilters(**values), image, vector, points,
                              next(r for r in archive["cases"] if r["id"] == live["archived_case_id"])["batch"]["query"])
        require(live["temporary_collection_deleted"] and live["unfiltered_dense_compatible"]
                and live["archived_vector_reuses"] == 5 and live["real_embedding_calls"] == live["generation_calls"] == 0, "Live compatibility/cleanup not confirmed")
    require(report["real_embedding_calls"] == report["generation_calls"] == 0, "Unexpected Exact/filter model calls")
    return {"offline_replay_passed": True, "exact_probes": len(PROBES), "bm25_compatibility_cases": len(cases), "live_filter_scenarios": 5 if report["live"] else 0, "generation_calls": 0}


def validate_live_row(row, filters, image, vector, points, query):
    """Recompute all three channels and bind the native pre-limit query evidence."""
    eligible = {c.chunk_id: c for c in image.chunks if filters.matches(c.source)}
    require(row["filters"] == filters.to_dict() and row["eligible_ids"] == sorted(eligible), "Live eligible set changed")
    batches = {}
    for key in ("dense", "bm25", "exact"):
        batch = ExactCandidateBatch.from_dict(row[key]) if key == "exact" else ScopedCandidateBatch.from_dict(row[key])
        require(batch.channel == key and batch.repository == image.scope.repository and batch.query == query
                and batch.candidate_limit == 30 and batch.filters == filters and batch.index_revision == image.index_revision
                and all(c.chunk_id in eligible and c.source_metadata == eligible[c.chunk_id].source
                        and c.matched_queries == (query,) for c in batch.candidates), "Live channel scope differs")
        batches[key] = batch
    dense = batches["dense"]
    require(len(dense.candidates) == min(30, len(eligible)), "Live Dense pre-limit filtering count changed")
    for c in dense.candidates:
        require(math.isclose(c.channel_scores["dense"], cosine_similarity(vector, points[c.chunk_id]["vector"]), abs_tol=2e-6), "Live Dense score changed")
    if dense.candidates:
        cutoff = dense.candidates[-1].channel_scores["dense"]
        require(all(cosine_similarity(vector, points[key]["vector"]) <= cutoff + 2e-6
                    for key in eligible if key not in {c.chunk_id for c in dense.candidates}), "Filtered Dense omitted a higher-scoring eligible Chunk")
    trace = row["native_dense_query"]
    if eligible:
        expected_filter = QdrantVectorStore.repository_filter(image.scope.repository)
        if filters != SearchFilters(): expected_filter.must.append(models.HasIdCondition(has_id=sorted(eligible)))
        require(len(trace) == 1 and trace[0]["limit"] == 30
                and trace[0]["filter"] == expected_filter.model_dump(mode="json"), "Native Dense filter/limit evidence differs")
        native = trace[0]["returned_points"]
        # The existing Qdrant query projects source/identity fields and omits
        # index-only chunking parameters. Compare that exact projection.
        payload_fields = {"chunk_id", "repository", "embedding_model", "schema_version", "path", "language",
                          "start_line", "end_line", "content", "symbol", "node_type", "parent_symbol",
                          "chunking_strategy", "chunking_version"}
        ordered_native = sorted(native, key=lambda p: (-p["score"], str(p["id"])))
        require([str(p["id"]) for p in ordered_native] == [c.chunk_id for c in dense.candidates]
                and all(p["score"] == c.channel_scores["dense"]
                        and p["payload"] == {key: value for key, value in points[c.chunk_id]["payload"].items() if key in payload_fields}
                        for p, c in zip(ordered_native, dense.candidates, strict=True)), "Native Dense result evidence differs")
    else: require(not trace, "Empty eligibility issued a Dense query")
    tokenizer = CodeTokenizer()
    index = BM25Index(LexicalSnapshot.build(image, tokenizer))
    # This is a small offline proof: score all repository documents, then select
    # eligible results. Runtime filtering happens in postings before its limit.
    tokens = tokenizer.tokenize_query(query)
    scores = [(chunk.chunk_id, math.fsum(t["contribution"] for t in index.explain(tokens, chunk.chunk_id)))
              for chunk in image.chunks if chunk.chunk_id in eligible]
    ranked = sorted(((key, score) for key, score in scores if score > 0), key=lambda p: (-p[1], p[0]))[:30]
    require([(c.chunk_id, c.channel_scores["bm25"]) for c in batches["bm25"].candidates] == ranked, "Live filtered BM25 scores/ranks differ")
    exact = batches["exact"]
    require(exact.match == ExactMatch("symbol", "WalletService.credit")
            and [c.chunk_id for c in exact.candidates] == sorted(key for key, c in eligible.items()
                    if c.source.symbol == "WalletService.credit")[:30], "Live Exact Symbol set differs")


async def spike(args):
    _, _, _, _, store, images = inputs()
    require(args.repository in images, "Choose an indexed frozen repository")
    filters = SearchFilters(args.language, args.path_prefix)
    request = ExactSearchRequest(args.repository, args.query, args.limit, filters=filters,
        match=ExactMatch(args.kind, args.needle if args.needle is not None else args.query, "partial" if args.partial else "full")) if args.kind else ScopedSearchRequest(args.repository, args.query, args.limit, filters=filters)
    result = await ExactCandidateChannel(SnapshotCoordinator(store, FrozenReader(images))).recall(request)
    return result.to_dict()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--verify-only", type=Path); modes.add_argument("--query")
    parser.add_argument("--output", type=Path); parser.add_argument("--live", action="store_true")
    parser.add_argument("--repository"); parser.add_argument("--language"); parser.add_argument("--path-prefix")
    parser.add_argument("--kind", choices=("symbol", "short_symbol", "path", "filename", "text", "literal"))
    parser.add_argument("--needle"); parser.add_argument("--partial", action="store_true"); parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()
    if args.verify_only: print(asyncio.run(replay(args.verify_only.resolve())))
    elif args.query is not None:
        if not args.repository: parser.error("--query requires --repository")
        if (args.needle or args.partial) and not args.kind: parser.error("--needle/--partial require --kind")
        print(json.dumps(asyncio.run(spike(args)), ensure_ascii=False, indent=2))
    else:
        args.output = args.output or ROOT / "evaluation/reports" / f"stage4_exact_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}.json"
        asyncio.run(evaluate(args))


if __name__ == "__main__": main()
