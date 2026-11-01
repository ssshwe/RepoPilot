"""Real bounded expansion on frozen code, with complete offline score/Context replay."""
import argparse
import asyncio
from dataclasses import asdict, replace
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys
from time import perf_counter
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/"services/api"), str(ROOT)]
from qdrant_client import models
from app.core.config import get_settings
from app.embedding.factory import EmbeddingProviderFactory
from app.embedding.validation import validate_embedding_batch
from app.retrieval.candidates import to_retrieval_results, CandidateLimits
from app.retrieval.dense import DenseCandidateChannel
from app.retrieval.expanded_retrieval import ExpandedRetriever, ExpandedRetrievalResult
from app.retrieval.expansion import ExpansionPolicy, QueryExpander, expansion_messages
from app.retrieval.expansion_llm import StructuredQueryGenerator
from app.retrieval.exact import ExactCandidateChannel, ExactMatch, match_source
from app.retrieval.filtered import FilteredBM25Channel, FilteredDenseChannel, SnapshotFilteredStore
from app.retrieval.hybrid import HybridResult, HybridSearchRequest
from app.retrieval.scope import ScopedCandidateBatch, SearchFilters
from app.retrieval.bm25 import BM25Index
from app.retrieval.rerank import RerankResult, RerankPolicy
from app.retrieval.reranker_process import ProcessReranker
from app.retrieval.similarity import cosine_similarity
from app.retrieval.snapshots import SnapshotCoordinator, QdrantImageReader
from app.retrieval.snapshot_store import SnapshotStore
from app.retrieval.snapshot_models import digest
from app.retrieval.vector_store import QdrantVectorStore, create_qdrant_client
from evaluation.retrieval.evaluate import require
from evaluation.stage4.evaluate import grouped_summary
from evaluation.stage4.evaluate_bm25 import inputs
from evaluation.stage4.evaluate_rerank import measured, comparison, evidence_diagnostics, replay as replay_rerank
from evaluation.stage4.state import file_reference, read_json, write_new, safe_configuration, full_snapshot, verify_unchanged
from evaluation.stage4.verify_snapshots import FrozenReader

HYBRID = ROOT/"evaluation/reports/stage4_hybrid_20261027_task8.json"
RERANK = ROOT/"evaluation/reports/stage4_rerank_20261031_task10_final.json"
POLICY = ExpansionPolicy()
LLM_CASES = ("wallet-symbol", "wallet-behavior", "refund-chain", "wallets-schema")
LIVE_SCOPES = (("all", {}), ("formal", {"language": "java", "path_prefix": "backend/wallet/"}), ("empty", {"language": "rust"}))
CORE = ("services/api/app/retrieval/expansion.py", "services/api/app/retrieval/expansion_llm.py",
        "services/api/app/retrieval/expanded_retrieval.py", "evaluation/stage4/evaluate_expansion.py")
DEFINITION = {
    "formal": "Fixed original 52 regression/development questions, archived original Dense ranks and identical 129 indexed Points; deterministic expansion; no holdout or new quality dataset",
    "budgets": "At most original+2 variants, each extra <=2000 codepoints, total <=6000; 30/30/10 per query, <=210 reserved raw slots, merged channel caps 30/30/10, fusion<=60, rerank<=30, TopK5; Context5/16000",
    "merge": "Within each channel select best original query rank; ties prefer earlier query then UUID; assign merged ranks; one RRF vote per channel k60; repeat matches do not sum query votes",
    "provenance": "matched_queries contains only actual supporting queries; an expansion-only hit never claims the original matched it; original remains the request and rerank question",
    "dense": "Original ranks reused only for the exact archived original request; every nonempty new variant uses its own real qwen3-embedding:0.6b vector and real Qdrant search on a private exact clone",
    "guards": "Formal lexical corpus is the frozen Task5 image; private Qdrant clone checked before/after. Three independent live scopes use actual QdrantImageReader/FilteredDenseChannel guards",
    "llm": "Four predeclared separate probes, one optional Structured Output call each, qwen2.5:0.5b unchanged; max_tokens512, temperature0, SDK retries0, deadline15s; raw invalid output retained; no answer generation",
    "latency": "Formal wall time includes frozen-corpus checks, archived-original loading, real variant embedding/search and optional rerank; not a production service latency benchmark",
    "defaults": "Expansion and Rerank remain off in product/API; Task14 freezes and evaluates final defaults on holdout",
}


def artifacts_for(path):
    return path.parent/(path.stem+"_artifacts")


def ensure_new_output(path):
    require(not path.exists() and not artifacts_for(path).exists(), "Existing expansion report/artifacts must not be overwritten")


class Journal:
    def __init__(self, path):
        self.stream = path.open("x", encoding="utf-8")
        self.events = []
    def add(self, row):
        row = json.loads(json.dumps(row, ensure_ascii=False, allow_nan=False))
        self.events.append(row)
        self.stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False)+"\n")
        self.stream.flush()
    def close(self):
        self.stream.close()


class ObservedEmbedding:
    def __init__(self, provider, journal):
        self.provider, self.journal, self.calls = provider, journal, 0
        self.model_name = provider.model_name
    async def embed_texts(self, texts):
        require(len(texts) == 1, "Expansion proof embeds one actual query at a time")
        self.calls += 1
        call_id = uuid4().hex
        self.journal.add({"event": "embedding_started", "id": call_id, "query": texts[0], "model": self.model_name})
        started = perf_counter()
        try:
            vectors = validate_embedding_batch(await self.provider.embed_texts(texts), 1)
            self.journal.add({"event": "embedding_completed", "id": call_id, "query": texts[0], "model": self.model_name,
                "vectors": vectors, "wall_ms": round((perf_counter()-started)*1000,3)})
            return vectors
        except BaseException as exc:
            self.journal.add({"event": "embedding_failed", "id": call_id, "query": texts[0], "error_type": type(exc).__name__})
            raise


class ObservedGenerator:
    def __init__(self, generator, journal):
        self.generator, self.journal, self.last_receipt = generator, journal, None
    async def generate(self, messages, policy):
        call_id = uuid4().hex
        self.journal.add({"event":"llm_started","id":call_id,"messages":messages})
        try:
            reply = await self.generator.generate(messages,policy)
            self.last_receipt = reply
            self.journal.add({"event":"llm_completed","id":call_id,"receipt":reply})
            return reply
        except BaseException as exc:
            self.last_receipt = self.generator.last_receipt
            self.journal.add({"event":"llm_interrupted","id":call_id,"error_type":type(exc).__name__,"receipt":self.last_receipt})
            raise


class OriginalOrLiveDense:
    """Evaluation-only original rank reuse, with real new-query Dense search."""
    name = "dense"
    def __init__(self, coordinator, original, provider, store, settings):
        self.coordinator, self.original, self.provider, self.store, self.settings = coordinator, original, provider, store, settings
        self.case_id, self.original_reuses = None, 0
    async def recall(self, request):
        old = next(r for r in self.original["cases"] if r["id"] == self.case_id)
        if (request.repository == old["hybrid"]["request"]["repository"] and request.query == old["hybrid"]["request"]["query"]
                and request.filters == SearchFilters() and request.candidate_limit == 30):
            self.original_reuses += 1
            return ScopedCandidateBatch.from_dict(old["hybrid"]["channels"]["dense"])
        started = perf_counter()
        async def retrieve(snapshot):
            eligible = {c.chunk_id:c.source for c in snapshot.index.chunks if request.filters.matches(c.source)}
            if not eligible: return snapshot.index.index_revision, ()
            store = self.store if request.filters == SearchFilters() else SnapshotFilteredStore(self.store, request.repository, sorted(eligible))
            result = await DenseCandidateChannel(self.provider, store, self.settings, index_revision=snapshot.index.index_revision).recall(request)
            require(all(eligible.get(c.chunk_id) == c.source_metadata for c in result.candidates), "New Dense returned code outside frozen repository/filter scope")
            return snapshot.index.index_revision, result.candidates
        revision, candidates = await self.coordinator.use(request.repository, retrieve)
        return ScopedCandidateBatch(request.repository, request.query, "dense", revision, request.candidate_limit, candidates,
            round((perf_counter()-started)*1000,3), filters=request.filters)


def _dump(value):
    if hasattr(value, "model_dump"): return value.model_dump(mode="json")
    if isinstance(value, dict): return {k:_dump(v) for k,v in value.items()}
    if isinstance(value, (list,tuple)): return [_dump(v) for v in value]
    return value


def attach_native_observer(client, journal):
    original = client.query_points
    async def query_points(*args, **kwargs):
        require(not args, "Proof expects named native Qdrant search arguments")
        call_id = uuid4().hex
        journal.add({"event": "qdrant_started", "id": call_id, "request": _dump(kwargs)})
        try:
            result = await original(**kwargs)
            journal.add({"event": "qdrant_completed", "id": call_id, "response": result.model_dump(mode="json")})
            return result
        except BaseException as exc:
            journal.add({"event": "qdrant_failed", "id": call_id, "error_type": type(exc).__name__})
            raise
    client.query_points = query_points


def expected_lexical(batch, snapshot):
    eligible = [c for c in snapshot.index.chunks if batch.filters.matches(c.source)]
    if batch.channel == "exact":
        selector = batch.match
        matches = [(c,match_source(c.source,selector)) for c in eligible] if selector else []
        matches = [(c,r) for c,r in matches if r is not None]
        matches.sort(key=lambda row: (-{"symbol":100,"short_symbol":90,"path":80,"filename":70,"text":60,"literal":60}[row[1].kind], row[0].chunk_id))
        # All production inferred selectors are full matches in this proof.
        expected = [(c.chunk_id,float({"symbol":100,"short_symbol":90,"path":80,"filename":70,"text":60,"literal":60}[r.kind]),r.to_dict()) for c,r in matches[:batch.candidate_limit]]
        require([(c.chunk_id,c.channel_scores["exact"],batch.reasons[c.chunk_id].to_dict()) for c in batch.candidates] == expected, "Exact expanded ranks/reasons differ from indexed source")
    else:
        index = BM25Index(snapshot)
        from app.retrieval.tokenizer import CodeTokenizer
        words = set(CodeTokenizer().tokenize_query(batch.query))
        scores = []
        allowed = {c.chunk_id for c in eligible}
        for ordinal, chunk in enumerate(snapshot.index.chunks):
            if chunk.chunk_id not in allowed: continue
            score = math.fsum(item["contribution"] for item in index.explain(sorted(words),chunk.chunk_id))
            if score > 0: scores.append((chunk.chunk_id,score))
        scores.sort(key=lambda row:(-row[1],row[0]))
        require([(c.chunk_id,c.channel_scores["bm25"]) for c in batch.candidates] == scores[:batch.candidate_limit], "BM25 expanded rank/score differs")


def verify_events(row, result, snapshot, points, old, *, live=False):
    events = row["events"]
    embeddings = [e for e in events if e["event"] == "embedding_completed"]
    starts = [e for e in events if e["event"] == "embedding_started"]
    require(len({e["id"] for e in starts}) == len(starts) and len(starts) <= len(result.plan.queries), "Embedding query/call budget differs")
    native = [e for e in events if e["event"] == "qdrant_started"]
    for kind in ("embedding","qdrant"):
        begin = [e for e in events if e["event"] == kind+"_started"]
        end = [e for e in events if e["event"] in {kind+"_completed",kind+"_failed"}]
        require(len(begin) == len(end) and {e["id"] for e in begin} == {e["id"] for e in end}, "Native/model operation acknowledgement missing")
    require(not any(e["event"].endswith("_failed") for e in events) or result.reason in {"variant_error","variant_timeout"}, "Failed variant call was silently accepted")
    actual_vectors = {e["query"]:e["vectors"][0] for e in embeddings}
    require(len(actual_vectors) == len(embeddings) and all(e["model"] == "qwen3-embedding:0.6b" for e in embeddings), "A query vector was substituted or repeated")
    require(all(any(s["id"] == e["id"] and s["query"] == e["query"] for s in starts) for e in embeddings), "Embedding start/completion query association differs")
    llm_starts = [e for e in events if e["event"] == "llm_started"]
    llm_ends = [e for e in events if e["event"] in {"llm_completed","llm_interrupted"}]
    require(len(llm_starts) == len(llm_ends) == result.plan.llm_calls
            and {e["id"] for e in llm_starts} == {e["id"] for e in llm_ends}
            and all(e["messages"] == expansion_messages(result.request.query) for e in llm_starts), "LLM one-call/original-question association differs")
    if result.plan.llm_receipt is not None:
        require(len(llm_ends) == 1 and llm_ends[0]["receipt"] == result.plan.llm_receipt,"Raw LLM reply differs from expansion diagnostics")
    for e in llm_ends:
        if e["receipt"] is not None:
            require(e["receipt"]["request"]["messages"] == expansion_messages(result.request.query)
                    and 0 <= e["receipt"]["http_attempts"] <= 1,"Raw model transport budget changed")
    raw_sources = {c.chunk_id:c.source for c in snapshot.index.chunks}
    for batch in result.batches:
        require(all(raw_sources.get(c.chunk_id) == c.source_metadata for c in batch.candidates), "Expanded source identity/coordinates differ from snapshot")
        if batch.channel != "dense":
            expected_lexical(batch, snapshot)
            continue
        archived = not live and batch.query == result.request.query and result.request.filters == SearchFilters()
        if archived:
            require(batch.to_dict() == old["hybrid"]["channels"]["dense"], "Original Dense ranks/scores were changed")
            continue
        eligible = [c for c in snapshot.index.chunks if result.request.filters.matches(c.source)]
        if not eligible:
            require(not batch.candidates and batch.query not in actual_vectors, "Empty scope made a new embedding call")
            continue
        require(batch.query in actual_vectors, "New Dense query has no own real embedding vector")
        vector = validate_embedding_batch([actual_vectors[batch.query]],1)[0]
        require(len(vector) == 1024, "Embedding dimension drift")
        corresponding = [e for e in native if e["request"]["query"] == vector]
        require(len(corresponding) == 1, "Native query did not use its own actual query vector")
        request = corresponding[0]["request"]
        require(request["limit"] == batch.candidate_limit, "Filtering/search cutoff differs")
        must = request["query_filter"]["must"]
        require(any(c.get("key") == "repository" and c.get("match",{}).get("value") == result.request.repository for c in must), "Native repository filter missing")
        if result.request.filters != SearchFilters():
            require(any(sorted(c.get("has_id",[])) == sorted(c.chunk_id for c in eligible) for c in must if "has_id" in c), "Native filter must restrict snapshot IDs before TopK")
        calculated = {c.chunk_id:cosine_similarity(vector,points[c.chunk_id]["vector"]) for c in eligible}
        scores = [c.channel_scores["dense"] for c in batch.candidates]
        require(len(scores) == min(batch.candidate_limit,len(eligible)) and scores == sorted(scores,reverse=True), "Native Dense count/order differs")
        require(all(math.isclose(calculated[c.chunk_id],c.channel_scores["dense"],abs_tol=2e-6) for c in batch.candidates), "New Dense scores do not recompute against their own vector")
        if scores:
            chosen = {c.chunk_id for c in batch.candidates}
            require(all(score <= scores[-1]+2e-6 for identity,score in calculated.items() if identity not in chosen), "Native Dense omits a better scored eligible Chunk")
    require(len(native) <= len(embeddings) and (len(native) == len(embeddings) or result.reason in {"variant_error","variant_timeout"}), "Unexpected additional native Dense/model calls")


def make_row(case, result, events, config, before, old_rerank):
    fusion = RerankResult(result.fused, result.reranked.policy, result.request.limits.top_k, "fallback", "empty_pool" if not result.fused.candidates else "disabled", 0., None)
    strategies = {"expansion": measured(case,fusion,config), "expansion_rerank": measured(case,result.reranked,config)}
    return {"id":case["id"], "expanded":result.to_dict(), "events":events, "strategies":strategies,
        "comparisons": {"fusion":comparison(before["strategies"]["hybrid"]["metrics"],strategies["expansion"]["metrics"]),
                        "rerank":comparison(old_rerank["measured"]["metrics"],strategies["expansion_rerank"]["metrics"])},
        "evidence_diagnostics":evidence_diagnostics(case,result.reranked,strategies["expansion_rerank"]["context"])}


def verify_row(case,row,snapshot,points,old,old_rerank,config,method="deterministic"):
    result = ExpandedRetrievalResult.from_dict(row["expanded"])
    require(row["id"] == case["id"] == old["id"] == old_rerank["id"], "Expansion case association changed")
    require(result.request == HybridSearchRequest(case["repository"],case["query"]) and result.plan.policy == POLICY, "Formal original request or expansion budget changed")
    require(result.plan.method == ("disabled" if not snapshot.index.chunks else method), "Expansion method differs")
    require(result.fused.index_revision == snapshot.index.index_revision, "Expanded pool uses another index")
    require(result.reranked.policy == RerankPolicy(),"Rerank policy changed during expansion comparison")
    if result.reranked.reply:
        require(old_rerank["rerank"]["reply"] is not None and result.reranked.reply["manifest_sha256"] == old_rerank["rerank"]["reply"]["manifest_sha256"],"Rerank model snapshot changed")
    verify_events(row,result,snapshot,points,old)
    require(row == make_row(case,result,row["events"],config,old,old_rerank), "Expanded Context, metrics or before/after diagnosis cannot be recomputed")
    require(result.reranked.status == ("reranked" if result.fused.candidates else "fallback") or result.reranked.reason is not None, "Missing rerank outcome")
    return result


def lightweight(path,row):
    return {"id":row["id"], "receipt":file_reference(path), "metrics":{n:v["metrics"] for n,v in row["strategies"].items()},
            "comparisons":row["comparisons"], "queries":row["expanded"]["used_queries"], "reason":row["expanded"]["reason"]}


async def live_proof(client, qstore, settings, provider, journal, original, artifacts, points):
    reader = QdrantImageReader(qstore,settings)
    image = await reader.capture("stage4-commerce")
    store = SnapshotStore(ROOT/".tools"/("stage4-task11-live-"+uuid4().hex)/"snapshots.sqlite3")
    coordinator = SnapshotCoordinator(store,reader)
    snapshot = await coordinator.rebuild("stage4-commerce",expected_source_sha256=image.source_sha256)
    retriever = ExpandedRetriever(coordinator,{"dense":FilteredDenseChannel(provider,settings,coordinator),
        "bm25":FilteredBM25Channel(coordinator), "exact":ExactCandidateChannel(coordinator)})
    selected = next(r for r in original["cases"] if r["id"] == "wallet-symbol")
    rows = []
    for name, filters in LIVE_SCOPES:
        offset = len(journal.events)
        result = await retriever.search(HybridSearchRequest("stage4-commerce",selected["hybrid"]["request"]["query"],SearchFilters(**filters)),enabled=True)
        row = {"id":name,"expanded":result.to_dict(),"events":journal.events[offset:]}
        write_new(artifacts/("live-"+name+".json"),row)
        verify_events(row,result,snapshot,points,selected,live=True)
        rows.append(file_reference(artifacts/("live-"+name+".json")))
        print(f"Live {name}: {len(result.used_queries)} queries, {len(result.final_results())} results",flush=True)
    write_new(artifacts/"live-snapshot.json",snapshot.to_dict())
    return {"cases":rows,"snapshot":file_reference(artifacts/"live-snapshot.json")}


async def evaluate(output, python, model):
    output = output.resolve()
    ensure_new_output(output)
    await replay_rerank(RERANK)
    _,_,_,cases,store,images = inputs()
    original, before_rerank = read_json(HYBRID),read_json(RERANK)
    settings = get_settings()
    config = safe_configuration(settings)
    require(config == original["configuration"], "Frozen configuration drift")
    artifacts = artifacts_for(output)
    artifacts.mkdir(parents=True)
    definition = {"definitions":DEFINITION,"policy":POLICY.to_dict(),"limits":asdict(CandidateLimits()),"llm_cases":list(LLM_CASES),
        "hybrid":file_reference(HYBRID),"rerank":file_reference(RERANK),"core":{n:file_reference(ROOT/n) for n in CORE},"case_ids":[c["id"] for c in cases]}
    write_new(artifacts/"run-definition.json",definition)
    journal = Journal(artifacts/"events.jsonl")
    client = create_qdrant_client(settings)
    attach_native_observer(client,journal)
    provider = EmbeddingProviderFactory.create(settings)
    observed = ObservedEmbedding(provider,journal)
    reranker = ProcessReranker(python,model)
    name = "repopilot_stage4_task11_"+uuid4().hex
    before, attempted, rows, probes = None,False,[],[]
    try:
        before = await full_snapshot(client)
        require(name not in before,"Private collection collision")
        write_new(artifacts/"production-before.json",before)
        archived = read_json(ROOT/"evaluation/reports/stage4_candidates_20261013_task3.json")
        index = read_json(ROOT/archived["artifacts"]["index"]["path"])
        attempted = True
        require(await client.create_collection(name,vectors_config=models.VectorParams.model_validate(index["config"]["params"]["vectors"]),metadata=index["config"]["metadata"]),"Private collection creation unconfirmed")
        qstore = QdrantVectorStore(client,name)
        await qstore.upsert_points([models.PointStruct(id=p["id"],vector=p["vector"],payload=p["payload"]) for p in index["points"]])
        cloned = (await full_snapshot(client))[name]
        require(cloned["points"] == index["points"],"Cloned Point/vector/payload drift")
        write_new(artifacts/"live-index.json",cloned)
        points = {str(p["id"]):p for p in cloned["points"]}
        coordinator = SnapshotCoordinator(store,FrozenReader(images))
        dense = OriginalOrLiveDense(coordinator,original,observed,qstore,settings)
        channels = {"dense":dense,"bm25":FilteredBM25Channel(coordinator),"exact":ExactCandidateChannel(coordinator)}
        retriever = ExpandedRetriever(coordinator,channels,reranker=reranker)
        ready = await reranker.start()
        require(ready["manifest_sha256"] == before_rerank["model_manifest"]["sha256"],"Reranker model snapshot drift")
        write_new(artifacts/"reranker-ready.json",ready)
        for ordinal,(case,old,old_rerank) in enumerate(zip(cases,original["cases"],before_rerank["cases"],strict=True),1):
            dense.case_id = case["id"]
            offset = len(journal.events)
            result = await retriever.search(HybridSearchRequest(case["repository"],case["query"]),enabled=True,rerank_enabled=True)
            row = make_row(case,result,journal.events[offset:],config,old,old_rerank)
            path = artifacts/"cases"/f"{ordinal:02}-{case['id']}.json"
            write_new(path,row)
            verify_row(case,row,store.load_ready(images[case["repository"]].scope).snapshot,points,old,old_rerank,config)
            rows.append(lightweight(path,row))
            print(f"{ordinal}/52 {case['id']}: {len(result.used_queries)} queries, {len(result.fused.candidates)} fused, rerank={result.reranked.status}",flush=True)
        generator = ObservedGenerator(StructuredQueryGenerator(settings),journal)
        llm_retriever = ExpandedRetriever(coordinator,channels,expander=QueryExpander(generator),reranker=reranker)
        for case_id in LLM_CASES:
            ordinal = next(i for i,c in enumerate(cases) if c["id"] == case_id)
            case,old,old_rerank = cases[ordinal],original["cases"][ordinal],before_rerank["cases"][ordinal]
            dense.case_id = case_id
            offset = len(journal.events)
            result = await llm_retriever.search(HybridSearchRequest(case["repository"],case["query"]),enabled=True,method="llm",rerank_enabled=True)
            row = make_row(case,result,journal.events[offset:],config,old,old_rerank)
            path = artifacts/"llm"/(case_id+".json")
            write_new(path,row)
            verify_row(case,row,store.load_ready(images[case["repository"]].scope).snapshot,points,old,old_rerank,config,"llm")
            probes.append(lightweight(path,row))
            print(f"LLM {case_id}: {result.plan.status}/{result.plan.reason}, {len(result.used_queries)} queries",flush=True)
        await reranker.aclose()
        live = await live_proof(client,qstore,settings,observed,journal,original,artifacts,points)
        require((await full_snapshot(client))[name] == cloned,"Expansion proof modified private indexed data")
        verify_unchanged(definition["core"],{n:file_reference(ROOT/n) for n in CORE},"Expansion implementation")
        verify_unchanged(config,safe_configuration(get_settings()),"Configuration")
    finally:
        try:
            await reranker.aclose()
            await provider.aclose()
            if attempted and await client.collection_exists(name): require(await client.delete_collection(name),"Temporary deletion unconfirmed")
            if attempted: require(not await client.collection_exists(name),"Temporary collection remains")
            if before is not None: verify_unchanged(before,await full_snapshot(client),"All original Collections")
        finally:
            await client.close()
            journal.close()
    full_rows = [read_json(ROOT/r["receipt"]["path"]) for r in rows]
    probe_rows = [read_json(ROOT/r["receipt"]["path"]) for r in probes]
    completed_llm = [e["receipt"] for r in probe_rows for e in r["events"] if e["event"] in {"llm_completed","llm_interrupted"} and e["receipt"] is not None]
    report = {"schema_version":1,"task":"Stage 4 / Task 11","status":"complete","created_at":datetime.now(timezone.utc).isoformat(),
        "definition":file_reference(artifacts/"run-definition.json"),"configuration":config,"cases":rows,"llm_probes":probes,"live":live,
        "index":file_reference(artifacts/"live-index.json"),"events":file_reference(artifacts/"events.jsonl"),"reranker_ready":file_reference(artifacts/"reranker-ready.json"),
        "summary":{n:grouped_summary([r["strategies"][n] for r in full_rows],cases) for n in ("expansion","expansion_rerank")},
        "before_summary":{"hybrid":original["summary"]["hybrid"],"hybrid_rerank":before_rerank["summary"]},
        "calls":{"archived_original_dense_reuses":dense.original_reuses,"new_embedding_calls":observed.calls,
            "native_qdrant_queries":sum(e["event"] == "qdrant_started" for e in journal.events),
            "llm_logical_calls":sum(r["expanded"]["plan"]["llm_calls"] for r in probe_rows),"llm_http_attempts":sum(r["http_attempts"] for r in completed_llm),
            "acknowledged_llm_usage":{key:sum((r["usage"] or {}).get(key,0) for r in completed_llm) for key in ("prompt_tokens","completion_tokens","total_tokens")},
            "llm_requests_with_unacknowledged_usage":sum(r["usage"] is None for r in completed_llm),
            "rerank_attempted_requests":sum(bool(r["expanded"]["fused"]["candidates"]) for r in full_rows+probe_rows),
            "rerank_completed_requests":sum(r["expanded"]["reranked"]["status"] == "reranked" for r in full_rows+probe_rows),
            "answer_generation_calls":0},"holdout_queried":False,"default_expansion_enabled":False,"default_rerank_enabled":False,
        "preservation":{"temporary_collection_deleted":True,"original_collections_unchanged":True,"private_clone_unchanged_by_search":True}}
    write_new(output,report)
    print("Complete: "+str(output),flush=True)


async def replay(path):
    report = read_json(path)
    require(report["schema_version"] == 1 and report["task"] == "Stage 4 / Task 11" and report["status"] == "complete", "Completed expansion report required")
    require(report["holdout_queried"] is False and report["default_expansion_enabled"] is False and report["default_rerank_enabled"] is False, "Expansion scope/defaults changed")
    definition = read_json(ROOT/report["definition"]["path"])
    require(file_reference(ROOT/report["definition"]["path"]) == report["definition"],"Run definition fingerprint changed")
    require(definition["definitions"] == DEFINITION and definition["policy"] == POLICY.to_dict() and definition["limits"] == asdict(CandidateLimits())
        and definition["llm_cases"] == list(LLM_CASES) and definition["core"] == {n:file_reference(ROOT/n) for n in CORE}
        and definition["hybrid"] == file_reference(HYBRID) and definition["rerank"] == file_reference(RERANK), "Frozen expansion definition/implementation/baseline association drift")
    await replay_rerank(RERANK)
    _,_,_,cases,store,images = inputs()
    original,before_rerank = read_json(HYBRID),read_json(RERANK)
    require(report["configuration"] == original["configuration"] and definition["case_ids"] == [c["id"] for c in cases]
            and [r["id"] for r in report["cases"]] == definition["case_ids"], "Formal expansion case inventory/configuration differs")
    for ref in (report["index"],report["events"],report["reranker_ready"],report["live"]["snapshot"],*report["live"]["cases"]):
        require(file_reference(ROOT/ref["path"]) == ref,"Expansion artifact fingerprint changed")
    cloned = read_json(ROOT/report["index"]["path"])
    archived = read_json(ROOT/"evaluation/reports/stage4_candidates_20261013_task3.json")
    index = read_json(ROOT/archived["artifacts"]["index"]["path"])
    require(cloned["points"] == index["points"] and cloned["config"]["metadata"] == index["config"]["metadata"],"Live clone does not match frozen source/payload/vector")
    points = {str(p["id"]):p for p in cloned["points"]}
    full_rows = []
    for case,entry,old,old_rerank in zip(cases,report["cases"],original["cases"],before_rerank["cases"],strict=True):
        require(file_reference(ROOT/entry["receipt"]["path"]) == entry["receipt"],"Formal raw receipt fingerprint changed")
        row = read_json(ROOT/entry["receipt"]["path"])
        verify_row(case,row,store.load_ready(images[case["repository"]].scope).snapshot,points,old,old_rerank,report["configuration"])
        require(entry == lightweight(ROOT/entry["receipt"]["path"],row),"Formal light summary differs from raw receipt")
        full_rows.append(row)
    require([r["id"] for r in report["llm_probes"]] == list(LLM_CASES),"Predeclared LLM probes dropped/reordered")
    probe_rows = []
    for entry in report["llm_probes"]:
        ordinal = next(i for i,c in enumerate(cases) if c["id"] == entry["id"])
        require(file_reference(ROOT/entry["receipt"]["path"]) == entry["receipt"],"LLM raw receipt fingerprint changed")
        row = read_json(ROOT/entry["receipt"]["path"])
        verify_row(cases[ordinal],row,store.load_ready(images[cases[ordinal]["repository"]].scope).snapshot,points,original["cases"][ordinal],before_rerank["cases"][ordinal],report["configuration"],"llm")
        require(entry == lightweight(ROOT/entry["receipt"]["path"],row),"LLM light summary differs")
        probe_rows.append(row)
    from app.retrieval.snapshot_models import LexicalSnapshot
    from app.retrieval.tokenizer import CodeTokenizer
    live_snapshot = LexicalSnapshot.from_dict(read_json(ROOT/report["live"]["snapshot"]["path"]),CodeTokenizer())
    require([read_json(ROOT/ref["path"])["id"] for ref in report["live"]["cases"]] == [n for n,_ in LIVE_SCOPES],"Live scope inventory changed")
    live_rows = []
    for ref,(_,filters) in zip(report["live"]["cases"],LIVE_SCOPES,strict=True):
        row = read_json(ROOT/ref["path"])
        result = ExpandedRetrievalResult.from_dict(row["expanded"])
        require(result.request.repository == "stage4-commerce" and result.request.filters == SearchFilters(**filters)
            and result.request.query == next(r for r in original["cases"] if r["id"] == "wallet-symbol")["hybrid"]["request"]["query"],"Live scope/original question differs")
        verify_events(row,result,live_snapshot,points,{},live=True)
        live_rows.append(row)
    require(report["summary"] == {n:grouped_summary([r["strategies"][n] for r in full_rows],cases) for n in ("expansion","expansion_rerank")}
            and report["before_summary"] == {"hybrid":original["summary"]["hybrid"],"hybrid_rerank":before_rerank["summary"]},"Expansion quality summaries differ")
    events = [json.loads(line) for line in (ROOT/report["events"]["path"]).read_text(encoding="utf-8").splitlines()]
    require(events == [e for row in full_rows+probe_rows+live_rows for e in row["events"]],"Raw event journal/case association differs")
    llm = [e["receipt"] for r in probe_rows for e in r["events"] if e["event"] in {"llm_completed","llm_interrupted"} and e["receipt"] is not None]
    expected_calls = {"archived_original_dense_reuses":len(full_rows)+len(probe_rows),"new_embedding_calls":sum(e["event"] == "embedding_started" for e in events),
        "native_qdrant_queries":sum(e["event"] == "qdrant_started" for e in events),"llm_logical_calls":sum(r["expanded"]["plan"]["llm_calls"] for r in probe_rows),
        "llm_http_attempts":sum(r["http_attempts"] for r in llm),
        "acknowledged_llm_usage":{key:sum((r["usage"] or {}).get(key,0) for r in llm) for key in ("prompt_tokens","completion_tokens","total_tokens")},
        "llm_requests_with_unacknowledged_usage":sum(r["usage"] is None for r in llm),
        "rerank_attempted_requests":sum(bool(r["expanded"]["fused"]["candidates"]) for r in full_rows+probe_rows),
        "rerank_completed_requests":sum(r["expanded"]["reranked"]["status"] == "reranked" for r in full_rows+probe_rows),"answer_generation_calls":0}
    require(report["calls"] == expected_calls,"Expansion model/transport/candidate call accounting differs")
    require(read_json(ROOT/report["reranker_ready"]["path"])["manifest_sha256"] == before_rerank["model_manifest"]["sha256"],"Reranker model identity differs")
    require(report["preservation"] == {"temporary_collection_deleted":True,"original_collections_unchanged":True,"private_clone_unchanged_by_search":True},"Cleanup/preservation incomplete")
    return {"offline_replay_passed":True,"formal_cases":52,"llm_probes":4,"live_scopes":3,"new_embedding_calls":expected_calls["new_embedding_calls"],
            "llm_http_attempts":expected_calls["llm_http_attempts"],"answer_generation_calls":0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-only",type=Path)
    parser.add_argument("--output",type=Path,default=ROOT/"evaluation/reports/stage4_expansion_20261101_task11.json")
    parser.add_argument("--python",type=Path,default=ROOT/".tools/reranker-venv/Scripts/python.exe")
    parser.add_argument("--model-directory",type=Path,default=ROOT/".tools/reranker-model")
    args = parser.parse_args()
    if args.verify_only: print(asyncio.run(replay(args.verify_only.resolve())))
    else: asyncio.run(evaluate(args.output,args.python,args.model_directory))


if __name__ == "__main__": main()
