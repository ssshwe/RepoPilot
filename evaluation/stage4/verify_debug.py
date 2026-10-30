"""Real Debug HTTP receipts and offline source/rank/score verification."""
import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import shutil
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/"services/api"),str(ROOT)]
import httpx
from app.core.config import get_settings
from app.retrieval.bm25 import BM25Index
from app.retrieval.candidates import SourceMetadata
from app.retrieval.exact import ExactMatch,match_source,FULL_SCORES,PARTIAL_SCORES
from app.retrieval.scope import SearchFilters
from app.retrieval.similarity import cosine_similarity
from app.retrieval.snapshot_models import IndexedRepository,LexicalSnapshot,SnapshotScope
from app.retrieval.tokenizer import CodeTokenizer
from app.retrieval.vector_store import create_qdrant_client
from app.schemas.retrieval import RetrievalSearchResponse
from app.schemas.retrieval_debug import DebugSearchRequest,DebugSearchResponse
from evaluation.retrieval.evaluate import require
from evaluation.stage4.state import file_reference,read_json,write_new,full_snapshot,safe_configuration,verify_unchanged

CORE=("services/api/app/api/retrieval.py","services/api/app/api/retrieval_debug.py","services/api/app/retrieval/debug.py",
      "services/api/app/schemas/retrieval_debug.py","apps/web/src/api/retrieval-debug-contract.ts","apps/web/src/api/retrieval-debug.ts",
      "apps/web/src/stores/retrieval-debug.ts","apps/web/src/components/RetrievalDebug.vue","apps/web/src/components/RetrievalCandidates.vue",
      "apps/web/src/types/retrieval-debug.ts","apps/web/src/App.vue","apps/web/src/style.css")
EVENTS=ROOT/".tools/stage4-task9-api-events.jsonl"
RENDERERS={"apps/web/src/components/RetrievalDebug.vue","apps/web/src/components/RetrievalCandidates.vue"}


def events():
    return [json.loads(line) for line in EVENTS.read_text(encoding="utf-8").splitlines()] if EVENTS.exists() else []


def case_definitions():
    base={"repository":"demo-repository","query":"RefundService.refund","top_k":5,"mode":"dense",
          "filters":{"language":None,"path_prefix":None},"rerank":False,"query_expansion":False}
    return [("dense",base,200),("bm25",base|{"mode":"bm25"},200),("hybrid",base|{"mode":"hybrid"},200),
        ("filtered",base|{"mode":"hybrid","filters":{"language":"java","path_prefix":"backend/service/"}},200),
        ("empty_scope",base|{"filters":{"language":"rust","path_prefix":None}},200),
        ("no_terms",base|{"mode":"bm25","query":"?!💳"},200),
        ("missing_snapshot",base|{"repository":"task9-missing-snapshot"},409),("future_feature",base|{"rerank":True},422),
        ("legacy",{k:base[k] for k in ("repository","query","top_k")},200)]


def image_for(state,config,repository):
    collection=config["qdrant_collection"];stored=state[collection]
    return IndexedRepository.from_records(SnapshotScope("sha256:"+config["qdrant_url_sha256"],collection,repository),
        {"dimension":stored["config"]["params"]["vectors"]["size"],"distance":"Cosine",
         "embedding":stored["config"]["metadata"]["repopilot_index"],
         "chunking":stored["config"]["metadata"]["repopilot_chunking"].get(repository)},
         [p for p in stored["points"] if p["payload"]["repository"]==repository])


def verify_case(row,state,config):
    definition=next((entry for entry in case_definitions() if entry[0]==row["id"]),None)
    require(definition is not None and row["request"]==definition[1] and row["status"]==row["expected_status"]==definition[2]
        and row["url"]==("/api/retrieval/search" if row["id"]=="legacy" else "/api/retrieval/debug"),"Debug HTTP case definition/status changed")
    require(not any(e["event"]=="generation_attempted" for e in row["events"]),"Debug invoked generation")
    if row["expected_status"]!=200:
        require(row["status"]==row["expected_status"] and not row["events"] and set(row["response"])=={"detail"},"Controlled error or no-call boundary changed")
        return
    request=row["request"];image=image_for(state,config,request["repository"])
    points={str(p["id"]):p for p in state[config["qdrant_collection"]]["points"]}
    eligible={c.chunk_id:c for c in image.chunks if SearchFilters(**request.get("filters",{})).matches(c.source)}
    finished=[e for e in row["events"] if e["event"]=="embedding_completed"]
    started=[e for e in row["events"] if e["event"]=="embedding_started"]
    need=int(row["id"]=="legacy" or request.get("mode") in {"dense","hybrid"} and bool(eligible))
    require(len(finished)==len(started)==need and len(row["events"])==need*2,"Embedding accounting differs")
    vector=None
    if need:
        require(started[0]["id"]==finished[0]["id"] and started[0]["text_sha256"]==[hashlib.sha256(request["query"].encode()).hexdigest()]
                and finished[0]["model"]==config["embedding_model"] and len(finished[0]["vectors"])==1,"Actual query vector association differs")
        vector=finished[0]["vectors"][0]
        require(len(vector)==image.configuration["dimension"],"Actual query dimension differs")
    if row["id"]=="legacy":
        parsed=RetrievalSearchResponse.model_validate(row["response"])
        require(parsed.repository==request["repository"] and parsed.query==request["query"] and parsed.top_k==request["top_k"],"Legacy echo changed")
        require(all(r.chunk_id in eligible and SourceMetadata.from_payload(r.model_dump())==eligible[r.chunk_id].source
            and math.isclose(r.score,cosine_similarity(vector,points[r.chunk_id]["vector"]),abs_tol=2e-6) for r in parsed.results),"Legacy source/score changed")
        return
    parsed=DebugSearchResponse.model_validate(row["response"])
    expected=DebugSearchRequest.model_validate(request)
    require(parsed.repository==expected.repository and parsed.query==expected.query and parsed.top_k==expected.top_k and parsed.mode==expected.mode
        and parsed.filters==expected.filters and parsed.index_revision==image.index_revision
        and parsed.collection==image.scope.collection and parsed.embedding_model==config["embedding_model"]
        and parsed.embedding_dimension==image.configuration["dimension"],"Debug request/snapshot metadata differs")
    for name,batch in parsed.channels.items():
        require(all(c["chunk_id"] in eligible and SourceMetadata.from_payload(c["source_metadata"])==eligible[c["chunk_id"]].source for c in batch["candidates"]),"Debug source is outside the indexed filter scope")
        if name=="dense":
            require(len(batch["candidates"])==min(30,len(eligible)),"Dense filtered count differs")
            for c in batch["candidates"]:require(math.isclose(c["channel_scores"]["dense"],cosine_similarity(vector,points[c["chunk_id"]]["vector"]),abs_tol=2e-6),"Dense Cosine does not recompute")
            order=[(-c["channel_scores"]["dense"],c["chunk_id"]) for c in batch["candidates"]]
            require(order==sorted(order),"Dense score/tie ordering differs")
            if order:
                returned={c["chunk_id"] for c in batch["candidates"]};cutoff=-order[-1][0]
                require(all(cosine_similarity(vector,points[key]["vector"])<=cutoff+2e-6 for key in eligible if key not in returned),"Dense omitted a higher-scoring eligible Point")
        elif name=="bm25":
            tokenizer=CodeTokenizer();index=BM25Index(LexicalSnapshot.build(image,tokenizer));tokens=tokenizer.tokenize_query(request["query"])
            scores=[(key,math.fsum(v["contribution"] for v in index.explain(tokens,key))) for key in eligible]
            ranked=sorted((p for p in scores if p[1]>0),key=lambda p:(-p[1],p[0]))[:30]
            require([(c["chunk_id"],c["channel_scores"]["bm25"]) for c in batch["candidates"]]==ranked,"BM25 rank/score does not recompute")
        else:
            selector=ExactMatch.infer(request["query"]);matches=[]
            if selector:
                for key,chunk in eligible.items():
                    reason=match_source(chunk.source,selector)
                    if reason:matches.append((key,(FULL_SCORES if reason.strength=="full" else PARTIAL_SCORES)[selector.kind]))
            matches.sort(key=lambda p:(-p[1],p[0]))
            require([(c["chunk_id"],c["channel_scores"]["exact"]) for c in batch["candidates"]]==matches[:10],"Exact set/rank does not recompute")


async def evaluate(path):
    artifacts=path.parent/(path.stem+"_artifacts")
    require(not path.exists() and not artifacts.exists(),"Existing Debug report/artifacts must not be overwritten")
    config=safe_configuration(get_settings());client=create_qdrant_client(get_settings())
    try:before=await full_snapshot(client)
    finally:await client.close()
    verify_unchanged(read_json(ROOT/"output/stage4-task9/qdrant-before.json"),before,"Interrupted preflight Qdrant state")
    artifacts.mkdir(parents=True);write_new(artifacts/"index-before.json",before)
    rows=[]
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8000",timeout=180.,trust_env=False) as http:
        synced=await http.post("/api/retrieval/debug/snapshot",json={"repository":"demo-repository"})
        sync_receipt={"status":synced.status_code,"response":synced.json() if synced.content else None}
        write_new(artifacts/"snapshot-sync.json",sync_receipt)
        require(synced.status_code==200,"Real snapshot synchronization failed")
        for name,request,status in case_definitions():
            start=len(events());url="/api/retrieval/search" if name=="legacy" else "/api/retrieval/debug"
            response=await http.post(url,json=request)
            row={"id":name,"url":url,"request":request,"expected_status":status,"status":response.status_code,"response":response.json(),"events":events()[start:]}
            write_new(artifacts/(name+".json"),row)
            require(response.status_code==status,"Real Debug HTTP status differs; original receipt retained")
            verify_case(row,before,config);rows.append(row);print(name+": HTTP "+str(response.status_code),flush=True)
    legacy=rows[-1]["response"]["results"];new=rows[0]["response"]["results"]
    require([r["chunk_id"] for r in legacy]==[r["chunk_id"] for r in new],"New Dense debug differs from the original Dense Top5")
    client=create_qdrant_client(get_settings())
    try:after=await full_snapshot(client)
    finally:await client.close()
    verify_unchanged(before,after,"Real Debug read-only Collection protection")
    write_new(artifacts/"index-after.json",after)
    report={"task":"Stage 4 / Task 9","schema_version":1,"status":"complete","created_at":datetime.now(timezone.utc).isoformat(),
        "configuration":config,"implementations":{p:file_reference(ROOT/p) for p in CORE},
        "index_before":file_reference(artifacts/"index-before.json"),"index_after":file_reference(artifacts/"index-after.json"),
        "snapshot_sync":file_reference(artifacts/"snapshot-sync.json"),"cases":rows,
        "calls":{"real_query_embeddings":sum(e["event"]=="embedding_completed" for r in rows for e in r["events"]),"generation_calls":0},
        "legacy_dense_top5_compatible":True,"qdrant_unchanged":True,"holdout_queried":False}
    write_new(path,report);print("Complete: 9 real HTTP cases, 4 real query embeddings, zero generation")


def source_binding(report,frozen_ui=None):
    expected={p:file_reference(ROOT/p) for p in CORE}
    if frozen_ui:
        require(set(frozen_ui).issubset(RENDERERS),"Only render snapshots may be rebound")
        for name,ref in frozen_ui.items():
            require(file_reference(ROOT/ref["path"])==ref,"Original renderer snapshot changed")
            expected[name]={"path":name,"sha256":ref["sha256"]}
    require(report["implementations"]==expected,"Debug source/contract implementation drift")


def replay(path,frozen_ui=None):
    report=read_json(path)
    require(report["task"]=="Stage 4 / Task 9" and report["status"]=="complete" and report["schema_version"]==1
        and report["holdout_queried"] is False,"Completed Debug input/implementation receipt required")
    source_binding(report,frozen_ui)
    execution=report.get("case_execution")
    if execution:
        require(execution["mode"]=="reuse verified HTTP receipts after render-only fixes" and execution["new_query_embeddings"]==0,"Debug reuse mode changed")
        ref=execution["original_report"];require(file_reference(ROOT/ref["path"])==ref,"Original Debug report changed")
        original=read_json(ROOT/ref["path"]);source_binding(original,execution["ui_before"])
        require(original["cases"]==report["cases"] and original["configuration"]==report["configuration"]
            and original["index_before"]==report["index_before"] and original["index_after"]==report["index_after"]
            and original["calls"]==report["calls"] and original["created_at"]==report["original_capture_created_at"],"Reused HTTP capture changed")
        parent=ROOT/ref["path"];directory=parent.parent/(parent.stem+"_artifacts")
        require(execution["source_receipts"]==[file_reference(directory/(row["id"]+".json")) for row in original["cases"]],"Original case receipt association changed")
    for ref in (report["index_before"],report["index_after"],report["snapshot_sync"]):require(file_reference(ROOT/ref["path"])==ref,"Debug snapshot artifact changed")
    state=read_json(ROOT/report["index_before"]["path"]);require(state==read_json(ROOT/report["index_after"]["path"]),"Debug changed persistent data")
    require([r["id"] for r in report["cases"]]==["dense","bm25","hybrid","filtered","empty_scope","no_terms","missing_snapshot","future_feature","legacy"],"Debug cases dropped/reordered")
    artifacts=Path(path).parent/(Path(path).stem+"_artifacts")
    for row in report["cases"]:
        require(read_json(artifacts/(row["id"]+".json"))==row,"Debug per-case receipt changed")
        verify_case(row,state,report["configuration"])
    # These are separate real embedding calls. Validate each score against its
    # own recorded vector; compatibility here concerns source identity/order.
    new,legacy=report["cases"][0]["response"]["results"],report["cases"][-1]["response"]["results"]
    require([{k:v for k,v in r.items() if k!="score"} for r in new]
        ==[{k:v for k,v in r.items() if k!="score"} for r in legacy],"Dense legacy/new Top5 source order differs")
    require(report["calls"]=={"real_query_embeddings":4,"generation_calls":0} and report["legacy_dense_top5_compatible"] and report["qdrant_unchanged"],"Debug costs/protection differ")
    return {"offline_replay_passed":True,"real_http_cases":9,"real_query_embeddings":4,"generation_calls":0}


def reuse_render_capture(original_path,output,frozen_ui):
    """Bind final render QA without repeating any real model/provider call."""
    original_path,output=Path(original_path).resolve(),Path(output).resolve()
    artifacts=output.parent/(output.stem+"_artifacts")
    require(not output.exists() and not artifacts.exists(),"Existing Debug report/artifacts must not be overwritten")
    replay(original_path,frozen_ui=frozen_ui)
    report=read_json(original_path)
    require("case_execution" not in report,"Only the original fresh HTTP run may be reused")
    changed={name for name in CORE if report["implementations"][name]!=file_reference(ROOT/name)}
    require(changed==set(frozen_ui) and changed.issubset(RENDERERS),"Reuse cannot conceal backend, contract or state drift")
    source_directory=original_path.parent/(original_path.stem+"_artifacts")
    artifacts.mkdir()
    for row in report["cases"]:shutil.copyfile(source_directory/(row["id"]+".json"),artifacts/(row["id"]+".json"))
    report["original_capture_created_at"]=report["created_at"]
    report["created_at"]=datetime.now(timezone.utc).isoformat()
    report["implementations"]={name:file_reference(ROOT/name) for name in CORE}
    report["case_execution"]={"mode":"reuse verified HTTP receipts after render-only fixes","new_query_embeddings":0,
        "original_report":file_reference(original_path),"ui_before":frozen_ui,
        "source_receipts":[file_reference(source_directory/(row["id"]+".json")) for row in report["cases"]]}
    write_new(output,report)
    return replay(output)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--verify-only",type=Path);parser.add_argument("--output",type=Path)
    args=parser.parse_args()
    if args.verify_only:print(replay(args.verify_only.resolve()))
    else:
        path=args.output or ROOT/"evaluation/reports"/f"stage4_debug_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}.json"
        asyncio.run(evaluate(path.resolve()))


if __name__=="__main__":main()
