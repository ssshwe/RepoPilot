"""Frozen Dense/BM25/RRF comparison and opt-in live Hybrid proof."""
import argparse
import asyncio
from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from time import perf_counter
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "services/api"), str(ROOT)]

from qdrant_client import models
from app.citations import CitationValidator
from app.context import ContextBuilder
from app.core.config import get_settings
from app.retrieval.bm25 import BM25Index
from app.retrieval.candidates import CandidateBatch, CandidateLimits, to_retrieval_results
from app.retrieval.exact import ExactCandidateBatch, ExactCandidateChannel, ExactMatch, match_source
from app.retrieval.filtered import FilteredBM25Channel, FilteredDenseChannel
from app.retrieval.hybrid import HybridResult, HybridRetriever, HybridSearchRequest
from app.retrieval.rrf import RRFParameters, fuse_rrf
from app.retrieval.scope import ScopedCandidateBatch, SearchFilters
from app.retrieval.snapshot_models import IndexedRepository, SnapshotScope
from app.retrieval.snapshot_store import SnapshotStore
from app.retrieval.snapshots import QdrantImageReader, SnapshotCoordinator
from app.retrieval.tokenizer import CodeTokenizer
from app.retrieval.vector_store import QdrantVectorStore, create_qdrant_client
from evaluation.retrieval.evaluate import require
from evaluation.stage4.evaluate import grouped_summary
from evaluation.stage4.evaluate_bm25 import inputs, delta, replay as replay_bm25
from evaluation.stage4.metrics import case_metrics, covered
from evaluation.stage4.state import file_reference, read_json, write_new, full_snapshot, safe_configuration, verify_unchanged
from evaluation.stage4.verify_candidates import replay as replay_dense
from evaluation.stage4.verify_exact import ArchivedEmbedding, validate_live_row
from evaluation.stage4.verify_snapshots import FrozenReader

DENSE = ROOT / "evaluation/reports/stage4_candidates_20261013_task3.json"
BM25 = ROOT / "evaluation/reports/stage4_bm25_20261026_task6_final.json"
CORE = ("services/api/app/retrieval/rrf.py", "services/api/app/retrieval/hybrid.py")
MODES = ("dense_bm25_rrf", "hybrid")
LIVE_SCOPES = [("all", {}), ("formal", {"language": "java", "path_prefix": "backend/wallet/"}),
               ("empty", {"language": "python"})]
DEFINITION = {
    "quality": "Identical frozen 52 development/regression questions, index, TopK 5 and Context 5 Sources / 16000 characters; negatives separate",
    "dense_input": "Original verified real Task 3 Top30 ranks/scores rebound only to the identical repository-scoped Task 5 Point snapshot",
    "hybrid_latency": "Offline wall time includes archived-rank loading and frozen snapshot guards, not real embedding/Qdrant service latency",
    "two_channel_latency": "Pure RRF fusion computation time only; not a Dense+BM25 end-to-end service measurement",
    "live": "Real Qdrant with archived original query vector; full guard/channel/fusion wall time, no new embedding or generation calls",
    "parameters": "Fixed before run: k=60, 1-based ranks, channel limits 30/30/10, fusion=60, TopK=5; no tuning/holdout"}


def checked_inputs():
    replay_dense(DENSE)
    replay_bm25(BM25)
    _, dense, _, cases, store, images = inputs()
    archive, bm25 = read_json(DENSE), read_json(BM25)
    require([r["id"] for r in archive["cases"]] == [c["id"] for c in cases], "Archived Dense case association differs")
    state = read_json(ROOT / archive["artifacts"]["index"]["path"])
    for repository, image in images.items():
        rebound = IndexedRepository.from_records(image.scope, image.configuration,
            [p for p in state["points"] if p["payload"]["repository"] == repository])
        require(rebound == image, "Archived Dense Point/vector/source differs from the shared lexical snapshot")
    return dense, archive, bm25, cases, store, images


class ArchivedDenseChannel:
    """Evaluation-only original ranks; never substitutes arbitrary vectors/queries."""
    name = "dense"
    def __init__(self, coordinator, rows):
        self.coordinator, self.rows, self.calls = coordinator, {(r["batch"]["repository"], r["batch"]["query"]): r for r in rows}, 0
        require(len(self.rows) == len(rows), "Duplicate archived Dense request")
    async def recall(self, request):
        started = perf_counter()
        require(request.filters == SearchFilters() and request.candidate_limit == 30, "Archived Top30 supports only its original unfiltered request")
        require((request.repository, request.query) in self.rows, "No real archived ranks for this request")
        saved = CandidateBatch.from_dict(self.rows[(request.repository, request.query)]["batch"])
        async def retrieve(snapshot):
            indexed = {c.chunk_id: c.source for c in snapshot.index.chunks}
            require(all(c.chunk_id in indexed and c.source_metadata == indexed[c.chunk_id] for c in saved.candidates), "Archived Dense source drift")
            return snapshot.index.index_revision, tuple(replace(c, index_revision=snapshot.index.index_revision) for c in saved.candidates)
        revision, candidates = await self.coordinator.use(request.repository, retrieve)
        self.calls += 1
        return ScopedCandidateBatch(request.repository, request.query, self.name, revision, 30, candidates,
            round((perf_counter()-started)*1000,3), filters=request.filters)


def strategy(case, batch, config, latency):
    results = to_retrieval_results(batch.candidates, repository=case["repository"], index_revision=batch.index_revision, score_channel="rrf")
    context = ContextBuilder(max_context_chunks=config["max_context_chunks"], max_context_characters=config["max_context_characters"]).build(case["repository"], results)
    CitationValidator().validate(case["repository"], [s.source_id for s in context.sources], context=context, retrieval_results=results)
    retrieval = {"repository": case["repository"], "query": case["query"], "top_k": 5,
                 "results": [r.model_dump() for r in results], "retrieval_latency_ms": latency}
    return {"id": case["id"], "retrieval": retrieval, "context": json.loads(json.dumps(asdict(context))),
            "metrics": case_metrics(case, retrieval["results"], [asdict(s) for s in context.sources])}


def check_case(case, row, snapshot, archived, bm25_row, dense_row, config):
    result = HybridResult.from_dict(row["hybrid"])
    require(result.request == HybridSearchRequest(case["repository"], case["query"]) and result.parameters == RRFParameters(), "Hybrid comparison request/parameters differ")
    require(row["snapshot_revision"] == snapshot.revision, "Hybrid snapshot changed")
    by_channel = {b.channel: b for b in result.channels}
    old = CandidateBatch.from_dict(archived["batch"])
    expected = [replace(c, index_revision=snapshot.index.index_revision).to_dict() for c in old.candidates]
    require([c.to_dict() for c in by_channel["dense"].candidates] == expected, "Hybrid changed original Dense candidate ranks/scores")
    require([c.to_dict() for c in by_channel["bm25"].candidates] == bm25_row["batch"]["candidates"], "Hybrid changed original BM25 candidate ranks/scores")
    selector = ExactMatch.infer(case["query"])
    matches=[]
    if selector is not None:
        from app.retrieval.exact import FULL_SCORES, PARTIAL_SCORES
        for chunk in snapshot.index.chunks:
            reason = match_source(chunk.source, selector)
            if reason is not None: matches.append(((FULL_SCORES if reason.strength=="full" else PARTIAL_SCORES)[selector.kind],chunk.chunk_id))
    matches.sort(key=lambda p:(-p[0],p[1]))
    require([(c.channel_scores["exact"],c.chunk_id) for c in by_channel["exact"].candidates] == matches[:10], "Exact candidate set/rank not reproducible")
    chunks = {c.chunk_id:c.source for c in snapshot.index.chunks}
    require(all(c.chunk_id in chunks and c.source_metadata == chunks[c.chunk_id] for b in result.channels for c in b.candidates), "Hybrid channel source/index association differs")
    two = ScopedCandidateBatch.from_dict(row["without_exact"])
    expected_two = fuse_rrf(result.channels[:2])
    require(two.candidates == expected_two.candidates and two.filters == expected_two.filters
            and two.index_revision == expected_two.index_revision and two.candidate_limit == 60, "Two-channel fusion differs")
    for name, batch, latency in (("hybrid",result.fused,result.retrieval_latency_ms),("dense_bm25_rrf",two,two.retrieval_latency_ms)):
        require(strategy(case,batch,config,latency) == row["strategies"][name], "Hybrid results, actual Context or metrics changed")
    require(row["reference_metrics"] == {"dense":dense_row["metrics"],"bm25":bm25_row["metrics"]}, "Dense/BM25 reference metrics changed")
    for name, ref in (("dense",dense_row["metrics"]),("bm25",bm25_row["metrics"]),("without_exact",row["strategies"]["dense_bm25_rrf"]["metrics"])):
        require(row["deltas_at_5"][name] == delta(row["strategies"]["hybrid"]["metrics"],ref), "Hybrid comparison delta changed")


def case_analysis(case,row,image):
    """Range-grounded failure locations; no new queries or generation."""
    result=HybridResult.from_dict(row["hybrid"])
    if case["category"]=="negative":
        return {"id":case["id"],"kind":"negative_control","negative":row["strategies"]["hybrid"]["metrics"]["negative"]}
    sources={"index":[c.source.to_dict() for c in image.chunks],
        "channel_union":[c.source_metadata.to_dict() for b in result.channels for c in b.candidates],
        "fused_pool":[c.source_metadata.to_dict() for c in result.fused.candidates],
        "top5":row["strategies"]["hybrid"]["retrieval"]["results"],
        "context":row["strategies"]["hybrid"]["context"]["sources"]}
    ranges=[]
    for entry in case["evidence"]:
        coverage={name:covered(values,entry) for name,values in sources.items()}
        cause=None
        if not coverage["index"]:cause="index_missing"
        elif not coverage["channel_union"]:cause="candidate_missing"
        elif not coverage["fused_pool"]:cause="fusion_truncated"
        elif not coverage["top5"]:cause="rank_after_top5"
        elif not coverage["context"]:cause="context_missing"
        matching=[{"chunk_id":c.chunk_id,"path":c.source_metadata.path,"start_line":c.source_metadata.start_line,
            "end_line":c.source_metadata.end_line,"channel_ranks":dict(c.channel_ranks)} for c in result.fused.candidates
            if c.source_metadata.path==entry["path"] and c.source_metadata.start_line<=entry["end_line"] and c.source_metadata.end_line>=entry["start_line"]]
        ranges.append({"path":entry["path"],"start_line":entry["start_line"],"end_line":entry["end_line"],"coverage":coverage,"cause":cause,"overlapping_fused_chunks":matching})
    metrics=row["strategies"]["hybrid"]["metrics"]["at_k"]["5"]
    return {"id":case["id"],"kind":"positive","ranges":ranges,
        "mrr_declined_vs_dense":metrics["evidence_mrr"]<row["reference_metrics"]["dense"]["at_k"]["5"]["evidence_mrr"],
        "range_recall_delta_vs_dense":row["deltas_at_5"]["dense"]["range_recall"],"generation_evaluated":False}


async def live_proof(path, archive, images):
    settings=get_settings();client=create_qdrant_client(settings)
    saved=read_json(ROOT/archive["artifacts"]["index"]["path"])
    selected=next(r for r in archive["cases"] if r["id"]=="wallet-symbol")
    query=selected["batch"]["query"];provider=ArchivedEmbedding(settings.embedding_model,query,selected["query_vector"])
    name="repopilot_stage4_task8_"+uuid4().hex
    before=None;attempted=False;rows=[]
    try:
        before=await full_snapshot(client);require(name not in before,"Temporary Collection name collision");attempted=True
        require(await client.create_collection(name,vectors_config=models.VectorParams.model_validate(saved["config"]["params"]["vectors"]),metadata=saved["config"]["metadata"]),"Private Collection creation unconfirmed")
        qstore=QdrantVectorStore(client,name)
        await qstore.upsert_points([models.PointStruct(id=p["id"],vector=p["vector"],payload=p["payload"]) for p in saved["points"]])
        cloned=(await full_snapshot(client))[name];require(cloned["points"]==saved["points"],"Temporary Point copy changed")
        write_new(path.parent/"live-index.json",cloned)
        private=settings.model_copy(update={"qdrant_collection":name})
        coordinator=SnapshotCoordinator(SnapshotStore(ROOT/".tools"/("stage4-task8-"+uuid4().hex)/"live.sqlite3"),QdrantImageReader(qstore,private))
        snapshot=await coordinator.rebuild("stage4-commerce",expected_source_sha256=images["stage4-commerce"].source_sha256)
        retriever=HybridRetriever(coordinator,{"dense":FilteredDenseChannel(provider,private,coordinator),"bm25":FilteredBM25Channel(coordinator),"exact":ExactCandidateChannel(coordinator)})
        native=[];original=client.query_points
        async def trace(*args,**kwargs):
            response=await original(*args,**kwargs)
            native.append({"filter":kwargs["query_filter"].model_dump(mode="json"),"limit":kwargs["limit"],"returned_points":[p.model_dump(mode="json") for p in response.points]})
            return response
        client.query_points=trace
        for label,filters in LIVE_SCOPES:
            native.clear()
            result=await retriever.search(HybridSearchRequest("stage4-commerce",query,SearchFilters(**filters),exact_match=ExactMatch("symbol","WalletService.credit")))
            write_new(path.parent/("live-"+label+".json"),{"id":label,"hybrid":result.to_dict(),"native_dense_query":list(native)})
            rows.append({"id":label,"hybrid":result.to_dict(),"native_dense_query":list(native)})
            print("Live Hybrid "+label+": "+str(len(result.fused.candidates))+" fused candidates",flush=True)
        require((await full_snapshot(client))[name]==cloned,"Hybrid read changed private Collection")
    finally:
        try:
            if attempted and await client.collection_exists(name):require(await client.delete_collection(name),"Temporary deletion unconfirmed")
            if attempted:require(not await client.collection_exists(name),"Temporary Collection remains")
            if before is not None:verify_unchanged(before,await full_snapshot(client),"All original Qdrant Collections")
        finally:await client.close()
    write_new(path,{"scope":snapshot.index.scope.to_dict(),"index":file_reference(path.parent/"live-index.json"),"rows":rows,
        "archived_case_id":"wallet-symbol","archived_vector_reuses":provider.calls,"real_embedding_calls":0,"generation_calls":0,"temporary_collection_deleted":True})


def verify_live(live,archive):
    state=read_json(ROOT/live["index"]["path"]);scope=SnapshotScope(**live["scope"])
    archived_index=read_json(ROOT/archive["artifacts"]["index"]["path"])
    require(state["points"]==archived_index["points"] and state["config"]["metadata"]==archived_index["config"]["metadata"]
        and state["config"]["params"]["vectors"]==archived_index["config"]["params"]["vectors"],"Live Hybrid Point/configuration differs from the archived index")
    image=IndexedRepository.from_records(scope,{"dimension":1024,"distance":"Cosine","embedding":state["config"]["metadata"]["repopilot_index"],
        "chunking":state["config"]["metadata"]["repopilot_chunking"][scope.repository]},[p for p in state["points"] if p["payload"]["repository"]==scope.repository])
    points={str(p["id"]):p for p in state["points"]};selected=next(r for r in archive["cases"] if r["id"]==live["archived_case_id"])
    require([r["id"] for r in live["rows"]]==[name for name,_ in LIVE_SCOPES],"Live Hybrid scopes dropped")
    for row,(_,values) in zip(live["rows"],LIVE_SCOPES,strict=True):
        result=HybridResult.from_dict(row["hybrid"]);filters=SearchFilters(**values)
        require(result.request==HybridSearchRequest(scope.repository,selected["batch"]["query"],filters,exact_match=ExactMatch("symbol","WalletService.credit"))
                and result.parameters==RRFParameters(),"Live Hybrid request/parameters changed")
        unpack={b.channel:b.to_dict() for b in result.channels}
        # Task 7's original live proof used limit 30 for all three channels.
        # Its validator checks the same rankings; adapt only that declared limit.
        for batch in unpack.values():batch["candidate_limit"]=30
        validate_live_row({"filters":filters.to_dict(),"eligible_ids":sorted(c.chunk_id for c in image.chunks if filters.matches(c.source)),
            **unpack,"native_dense_query":row["native_dense_query"]},filters,image,selected["query_vector"],points,selected["batch"]["query"])
    require(live["archived_vector_reuses"]==2 and live["real_embedding_calls"]==live["generation_calls"]==0 and live["temporary_collection_deleted"],"Live Hybrid costs/cleanup changed")


async def evaluate(args):
    output=args.output.resolve();artifacts=output.parent/(output.stem+"_artifacts")
    require(not output.exists() and not artifacts.exists(),"Existing Hybrid report/artifacts must not be overwritten")
    dense,archive,bm25,cases,store,images=checked_inputs()
    config=safe_configuration(get_settings());require(config==dense["configuration"],"Frozen configuration drift")
    coordinator=SnapshotCoordinator(store,FrozenReader(images));archived_channel=ArchivedDenseChannel(coordinator,archive["cases"])
    retriever=HybridRetriever(coordinator,{"dense":archived_channel,"bm25":FilteredBM25Channel(coordinator),"exact":ExactCandidateChannel(coordinator)})
    artifacts.mkdir(parents=True);rows=[]
    for ordinal,(case,old_dense,old_bm25,old_archived) in enumerate(zip(cases,dense["cases"],bm25["cases"],archive["cases"],strict=True),1):
        try:
            result=await retriever.search(HybridSearchRequest(case["repository"],case["query"]))
            two=fuse_rrf(result.channels[:2]);snapshot=store.load_ready(images[case["repository"]].scope).snapshot
            strategies={"hybrid":strategy(case,result.fused,config,result.retrieval_latency_ms),"dense_bm25_rrf":strategy(case,two,config,two.retrieval_latency_ms)}
            row={"id":case["id"],"snapshot_revision":snapshot.revision,"hybrid":result.to_dict(),"without_exact":two.to_dict(),"strategies":strategies,
                "reference_metrics":{"dense":old_dense["metrics"],"bm25":old_bm25["metrics"]},
                "deltas_at_5":{name:delta(strategies["hybrid"]["metrics"],ref) for name,ref in (
                    ("dense",old_dense["metrics"]),("bm25",old_bm25["metrics"]),("without_exact",strategies["dense_bm25_rrf"]["metrics"]))}}
            check_case(case,row,snapshot,old_archived,old_bm25,old_dense,config)
            write_new(artifacts/"cases"/f"{ordinal:02}-{case['id']}.json",row);rows.append(row)
            print(f"{ordinal}/52 {case['id']}: union={result.union_count}, fused={len(result.fused.candidates)}, exact={len(result.channels[-1].candidates)}",flush=True)
        except Exception as exc:
            write_new(artifacts/"cases"/f"{ordinal:02}-{case['id']}-error.json",{"id":case["id"],"error_type":type(exc).__name__,"status":"aborted; no case removed"});raise
    live=None
    if args.live:
        live_path=artifacts/"live-summary.json";await live_proof(live_path,archive,images);verify_live(read_json(live_path),archive);live=file_reference(live_path)
    report={"schema_version":1,"task":"Stage 4 / Task 8","status":"complete","created_at":datetime.now(timezone.utc).isoformat(),
        "dense_reference":file_reference(DENSE),"bm25_reference":file_reference(BM25),"configuration":config,"definitions":DEFINITION,
        "implementations":{name:file_reference(ROOT/name) for name in CORE},"parameters":RRFParameters().to_dict(),"limits":asdict(CandidateLimits()),
        "cases":rows,"summary":{name:grouped_summary([row["strategies"][name] for row in rows],cases) for name in MODES},
        "dense_summary":dense["summary"],"bm25_summary":bm25["summary"],"live":live,"holdout_queried":False,
        "calls":{"archived_dense_rank_reuses":archived_channel.calls,"real_embedding_calls":0,"generation_calls":0},
        "repository_rebinding":{repo:{"index_revision":image.index_revision,"source_sha256":image.source_sha256,"points":len(image.chunks)} for repo,image in images.items()}}
    write_new(output,report);print("Complete: 52 frozen Hybrid comparisons; "+str(output))


async def replay(path):
    report=read_json(path)
    require(report["task"]=="Stage 4 / Task 8" and report["schema_version"]==1 and report["status"]=="complete" and report["holdout_queried"] is False
        and report["definitions"]==DEFINITION and report["parameters"]==RRFParameters().to_dict() and report["limits"]==asdict(CandidateLimits()),"Completed fixed-parameter Hybrid report required")
    require(report["dense_reference"]==file_reference(DENSE) and report["bm25_reference"]==file_reference(BM25)
        and report["implementations"]=={name:file_reference(ROOT/name) for name in CORE},"Hybrid report/reference implementation drift")
    dense,archive,bm25,cases,store,images=checked_inputs()
    require(report["configuration"]==dense["configuration"],"Hybrid configuration association differs")
    require([row["id"] for row in report["cases"]]==[c["id"] for c in cases],"Hybrid cases dropped/reordered")
    artifacts=Path(path).parent/(Path(path).stem+"_artifacts")
    for ordinal,(case,row,old_archived,old_bm25,old_dense) in enumerate(zip(cases,report["cases"],archive["cases"],bm25["cases"],dense["cases"],strict=True),1):
        require(read_json(artifacts/"cases"/f"{ordinal:02}-{case['id']}.json")==row,"Hybrid per-case receipt changed")
        check_case(case,row,store.load_ready(images[case["repository"]].scope).snapshot,old_archived,old_bm25,old_dense,report["configuration"])
    require(report["summary"]=={name:grouped_summary([row["strategies"][name] for row in report["cases"]],cases) for name in MODES}
        and report["dense_summary"]==dense["summary"] and report["bm25_summary"]==bm25["summary"],"Hybrid comparison summary differs")
    require(report["repository_rebinding"]=={repo:{"index_revision":image.index_revision,"source_sha256":image.source_sha256,"points":len(image.chunks)} for repo,image in images.items()},"Hybrid repository revision rebinding changed")
    require(report["calls"]=={"archived_dense_rank_reuses":52,"real_embedding_calls":0,"generation_calls":0},"Hybrid comparison model calls differ")
    if report["live"]:
        ref=report["live"];require(file_reference(ROOT/ref["path"])==ref,"Live Hybrid summary changed")
        live=read_json(ROOT/ref["path"]);require(file_reference(ROOT/live["index"]["path"])==live["index"],"Live Hybrid index changed")
        for row in live["rows"]:require(read_json(Path(ROOT/ref["path"]).parent/("live-"+row["id"]+".json"))==row,"Live Hybrid per-scope receipt changed")
        verify_live(live,archive)
    return {"offline_replay_passed":True,"cases":52,"strategies":4,"live_scenarios":3 if report["live"] else 0,"real_embedding_calls":0,"generation_calls":0}


async def spike(case_id):
    _,archive,_,cases,store,images=checked_inputs()
    case=next((c for c in cases if c["id"]==case_id),None);require(case is not None,"Choose a frozen development/regression case")
    coordinator=SnapshotCoordinator(store,FrozenReader(images))
    retriever=HybridRetriever(coordinator,{"dense":ArchivedDenseChannel(coordinator,archive["cases"]),"bm25":FilteredBM25Channel(coordinator),"exact":ExactCandidateChannel(coordinator)})
    return (await retriever.search(HybridSearchRequest(case["repository"],case["query"]))).to_dict()


def main():
    parser=argparse.ArgumentParser(description=__doc__);group=parser.add_mutually_exclusive_group()
    group.add_argument("--verify-only",type=Path);group.add_argument("--case-id")
    parser.add_argument("--live",action="store_true");parser.add_argument("--output",type=Path)
    args=parser.parse_args()
    if args.verify_only:print(asyncio.run(replay(args.verify_only.resolve())))
    elif args.case_id:print(json.dumps(asyncio.run(spike(args.case_id)),ensure_ascii=False,indent=2))
    else:
        args.output=args.output or ROOT/"evaluation/reports"/f"stage4_hybrid_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}.json"
        asyncio.run(evaluate(args))


if __name__=="__main__":main()
