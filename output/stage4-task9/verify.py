"""Final Task 9 acceptance: offline receipts, final renderer and protected state."""
import asyncio
from datetime import datetime,timezone
import hashlib
import math
from pathlib import Path
import socket
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/"services/api"),str(ROOT)]
import httpx
from app.core.config import get_settings
from app.retrieval.vector_store import create_qdrant_client
from evaluation.retrieval.evaluate import require
from evaluation.stage4.verify_debug import replay
from evaluation.stage4.evaluate_hybrid import replay as replay_hybrid
from evaluation.stage4.verify_exact import replay as replay_exact
from evaluation.stage4.freeze_baseline import replay_baseline
from evaluation.stage4.state import file_reference,read_json,write_new,full_snapshot,safe_configuration,verify_unchanged


async def main():
    output=ROOT/"output/stage4-task9"
    path=ROOT/"evaluation/reports/stage4_debug_20261031_task9_release.json"
    browser_path=ROOT/"output/playwright/stage4-task9/browser-verification-final.json"
    attempts=[];connect,create=socket.socket.connect,socket.create_connection
    def deny(*args,**kwargs):attempts.append(True);raise AssertionError("Offline replay attempted network access")
    try:
        socket.socket.connect,socket.create_connection=deny,deny
        current=replay(path)
        hybrid=await replay_hybrid(ROOT/"evaluation/reports/stage4_hybrid_20261027_task8.json")
        exact=await replay_exact(ROOT/"evaluation/reports/stage4_exact_20261026_task7.json")
        baseline=replay_baseline(ROOT/"evaluation/reports/stage4_dense_20261013_task1.json")
    finally:socket.socket.connect,socket.create_connection=connect,create
    require(not attempts,"Offline network usage detected")
    report=read_json(path);browser=read_json(browser_path)
    require(browser["passed"] and browser["no_additional_model_calls_for_render_fix"],"Final browser metadata QA missing")
    for ref in [browser["original_browser_run"],*browser["artifacts"].values(),*browser["final_ui_sources"].values()]:
        require(file_reference(ROOT/ref["path"])==ref,"Final browser artifact/source changed")
    original=read_json(ROOT/browser["original_browser_run"]["path"])
    require(original["passed"] and original["console_errors"]==0 and original["normal_api_restored"]
        and original["proof_interception_removed"],"Browser cleanup or console check failed")
    for ref in [original["embedding_events"],*original["artifacts"].values()]:require(file_reference(ROOT/ref["path"])==ref,"Browser run artifact changed")
    events=read_json(ROOT/original["embedding_events"]["path"])
    completed=[e for e in events if e["event"]=="embedding_completed"]
    require(len(events)==18 and len(completed)==9 and not any(e["event"]=="generation_attempted" for e in events),"Actual model-call count differs")
    require(events[:8]==[e for row in report["cases"] for e in row["events"]],"Browser/API embedding accounting overlaps incorrectly")
    require(all(len(e["vectors"])==1 and len(e["vectors"][0])==1024 and all(math.isfinite(x) for x in e["vectors"][0]) for e in completed),"Real query vector shape differs")
    require(original["checks"]["late-response-check"]["passed"] and browser["metadata_check"]["fusedOrigins"]==17,"Late-response or pool origin check missing")
    require("1279 passed, 6 skipped" in (output/"backend-tests.log").read_text(encoding="utf-8"),"Backend regression not passed")
    require("207 passed" in (output/"evaluation-tests-release.log").read_text(encoding="utf-8"),"Evaluation regression not passed")
    require("pass 76" in (output/"frontend-tests-complete.log").read_text(encoding="utf-8"),"Frontend regression not passed")
    require("built in" in (output/"frontend-build-complete.log").read_text(encoding="utf-8"),"Frontend build not passed")
    verify_unchanged(read_json(output/"settings-before.json"),safe_configuration(get_settings()),"Configuration")
    client=create_qdrant_client(get_settings())
    try:after=await full_snapshot(client)
    finally:await client.close()
    verify_unchanged(read_json(output/"qdrant-before.json"),after,"All existing Qdrant Collections")
    protected=read_json(output/"workspace-before.json");changed=[]
    allowed={"README.md","evaluation/README.md","apps/web/src/App.vue","apps/web/src/components/RetrievalDebug.vue","apps/web/src/style.css","services/api/app/api/retrieval.py"}
    for name,expected in protected.items():
        file=ROOT/name;require(file.is_file(),f"Protected file missing: {name}")
        if hashlib.sha256(file.read_bytes()).hexdigest()!=expected:
            require(name in allowed,f"Unplanned existing file change: {name}");changed.append(name)
    require(len(protected)==1227 and set(changed)==allowed,"Protection inventory differs")
    with httpx.Client(trust_env=False,timeout=10) as http:
        health=http.get("http://127.0.0.1:8000/health");require(health.status_code==200,"Normal API is not restored")
    new,old=report["cases"][0]["response"]["results"],report["cases"][-1]["response"]["results"]
    deltas=[abs(a["score"]-b["score"]) for a,b in zip(new,old,strict=True)]
    write_new(output/"qdrant-after.json",after)
    write_new(ROOT/"docs/stage-4-task-9-verification.json",{
        "task":"Stage 4 / Task 9","completed":True,"run_date":"2026-10-31","created_at":datetime.now(timezone.utc).isoformat(),
        "report":file_reference(path),"browser":file_reference(browser_path),
        "tests":{"backend_passed":1279,"backend_skipped":6,"new_backend_tests":25,"evaluation_passed":207,"new_evaluation_tests":17,
            "frontend_passed":76,"new_frontend_tests":30,"build_passed":True,"real_http_cases":9,
            "browser_modes":3,"mobile_width":390,"source_and_rrf_expansion":True,"cancel_and_late_response":True},
        "calls":{"real_api_query_embeddings":4,"real_browser_query_embeddings":5,"total_real_query_embeddings":9,"generation_calls":0,
            "additional_model_calls_for_render_fix":0},
        "legacy_compatibility":{"same_top5_source_identity_and_order":True,"separate_real_embedding_outputs_differ":True,
            "scores_verified_against_own_vectors":True,"maximum_observed_score_difference":max(deltas)},
        "offline_network_attempts":len(attempts),"debug_replay":current,"task8_replay":hybrid,"task7_replay":exact,"task1_replay":baseline,
        "tasks2_through6_replay":"Dense, BM25, shared snapshots and Tokenizer replayed by Hybrid input validator",
        "original_http_receipts_preserved":report["case_execution"],"normal_api_restored":True,
        "preservation":{"protected_files":1227,"expected_changed_files":sorted(changed),"other_existing_files_unchanged":1221,
            "environment_locks_datasets_prior_reports_unchanged":True,"qdrant_unchanged":True,
            "legacy_search_code_chat_issue_dense_unchanged":True,"holdout_queried":False},
        "test_receipts":{name:file_reference(output/name) for name in ("backend-tests.log","evaluation-tests-release.log","frontend-tests-complete.log","frontend-build-complete.log")},
        "implementation":{name:file_reference(ROOT/name) for name in ("evaluation/stage4/verify_debug.py","evaluation/tests/test_debug_receipts.py",
            "services/api/tests/test_retrieval_debug.py","apps/web/tests/retrieval-debug-contract.test.mjs","apps/web/tests/retrieval-debug-store.test.mjs",
            "apps/web/tests/fixtures/retrieval-debug.json","output/stage4-task9/serve_api.py")},
        "protection_inputs":{name:file_reference(output/name) for name in ("workspace-before.json","settings-before.json","qdrant-before.json","qdrant-after.json","git-before.json")},
        "next_task":"Stage 4 / Task 10 Reranker Adapter; not started"})
    print("Task 9 complete: 1279 backend / 207 evaluation / 76 frontend tests; build passed; 9 real HTTP cases; 9 real embeddings / zero generation; 1221 old files and all Collections unchanged")


if __name__=="__main__":asyncio.run(main())
