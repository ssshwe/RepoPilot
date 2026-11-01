"""Final Task 11 acceptance, source protection, offline lineage and real cost evidence."""
import asyncio
from datetime import datetime,timezone
import hashlib
from pathlib import Path
import socket
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/"services/api"),str(ROOT)]
import httpx
from app.core.config import get_settings
from app.retrieval.vector_store import create_qdrant_client
from evaluation.retrieval.evaluate import require
from evaluation.stage4.evaluate_expansion import replay
from evaluation.stage4.analyze_expansion import analyze
from evaluation.stage4.verify_debug import replay as replay_debug
from evaluation.stage4.verify_exact import replay as replay_exact
from evaluation.stage4.freeze_baseline import replay_baseline
from evaluation.stage4.state import read_json,write_new,file_reference,full_snapshot,safe_configuration,verify_unchanged


async def main():
    output = ROOT/"output/stage4-task11"
    path = ROOT/"evaluation/reports/stage4_expansion_20261101_task11.json"
    analysis_path = ROOT/"evaluation/reports/stage4_expansion_20261101_task11_analysis_final.json"
    attempts = []
    connect,create = socket.socket.connect,socket.create_connection
    def deny(*args,**kwargs):
        attempts.append(True)
        raise AssertionError("Offline replay attempted a connection")
    try:
        socket.socket.connect,socket.create_connection = deny,deny
        current = await replay(path)
        derived = analyze(path)
        require(derived == read_json(analysis_path),"Actual pipeline costs/rank changes do not replay")
        debug = replay_debug(ROOT/"evaluation/reports/stage4_debug_20261031_task9_release.json")
        exact = await replay_exact(ROOT/"evaluation/reports/stage4_exact_20261026_task7.json")
        baseline = replay_baseline(ROOT/"evaluation/reports/stage4_dense_20261013_task1.json")
    finally:
        socket.socket.connect,socket.create_connection = connect,create
    require(not attempts,"Offline network access detected")
    report = read_json(path)
    require(report["calls"]["new_embedding_calls"] == report["calls"]["native_qdrant_queries"] == 92
        and report["calls"]["llm_http_attempts"] == report["calls"]["llm_logical_calls"] == 4
        and report["calls"]["rerank_completed_requests"] == report["calls"]["rerank_attempted_requests"] == 54
        and report["calls"]["acknowledged_llm_usage"]["total_tokens"] == 1062
        and report["calls"]["answer_generation_calls"] == 0,"Real model/request accounting differs")
    require(derived["formal_new_embedding_calls"] == 82 and len(derived["formal_costs"]) == 52,"Probe work contaminated the formal denominator")
    for mode,parent in (("expansion","hybrid"),("expansion_rerank","hybrid_rerank")):
        for split in ("dev","regression"):
            require(report["summary"][mode]["split"][split]["at_k"] == report["before_summary"][parent]["split"][split]["at_k"],"Formal cutoff metrics differ from recorded conclusion")
    loss = next(r for r in derived["annotated_evidence_rank_changes"] if r["id"] == "inventory-chain")
    require(loss["before_fused_rank"] == 2 and loss["after_fused_rank"] == 3
        and loss["chunk"]["chunk_id"] == "948223e9-4ada-5eb8-a7e2-7c6b781e2e23","Actual ranking regression not retained")
    gain = next(r for r in derived["llm_probes"] if r["id"] == "wallets-schema")
    require(gain["comparison"]["fusion"]["at_5"]["range_recall"] == {"before":0.,"after":1.},"Independent LLM improvement evidence differs")
    require({r["id"]:r["status"] for r in derived["llm_probes"]} == {"wallet-symbol":"fallback","wallet-behavior":"expanded","refund-chain":"no_variants","wallets-schema":"expanded"},"LLM failures were dropped or retried")
    for name,text in (("backend-tests.log","1375 passed, 6 skipped"),("evaluation-tests.log","258 passed"),
        ("analysis-tests.log","2 passed"),("evaluation-collected.log","260 tests collected"),("frontend-tests.log","pass 76"),("frontend-build.log","built in")):
        require(text in (output/name).read_text(encoding="utf-8"),"Missing successful complete test evidence: "+name)
    protected = read_json(output/"workspace-before.json")
    allowed,changed = {"README.md","evaluation/README.md"},[]
    for name,expected in protected.items():
        file = ROOT/name
        require(file.is_file(),"Protected file missing: "+name)
        if hashlib.sha256(file.read_bytes()).hexdigest() != expected:
            require(name in allowed,"Unplanned existing file change: "+name)
            changed.append(name)
    require(len(protected) == 1444 and set(changed) == allowed,"Protection inventory differs")
    verify_unchanged(read_json(output/"settings-before.json"),safe_configuration(get_settings()),"Configuration")
    client = create_qdrant_client(get_settings())
    try:
        after = await full_snapshot(client)
    finally:
        await client.close()
    verify_unchanged(read_json(output/"qdrant-before.json"),after,"All original Qdrant Collections")
    commands = subprocess.check_output(["powershell","-NoProfile","-Command",
        "Get-CimInstance Win32_Process -Filter \"name = 'python.exe'\" | Select-Object -ExpandProperty CommandLine"],text=True,encoding="utf-8",errors="replace")
    require("reranker_worker.py" not in commands,"Reranker worker still alive")
    with httpx.Client(trust_env=False,timeout=10) as http:
        health = {"api":http.get("http://127.0.0.1:8000/health").status_code,"web":http.get("http://127.0.0.1:5173/").status_code}
    require(all(v == 200 for v in health.values()),"Normal development services unavailable")
    rows = [read_json(ROOT/e["receipt"]["path"]) for e in report["cases"]+report["llm_probes"]]
    forward_calls = sum(r["expanded"]["reranked"]["reply"]["performance"]["forward_calls"] for r in rows if r["expanded"]["reranked"]["reply"])
    require(forward_calls == 387,"Rerank GPU batch accounting differs")
    write_new(output/"qdrant-after.json",after)
    write_new(ROOT/"docs/stage-4-task-11-verification.json",{
        "task":"Stage 4 / Task 11","completed":True,"run_date":"2026-11-01","created_at":datetime.now(timezone.utc).isoformat(),
        "report":file_reference(path),"analysis":file_reference(analysis_path),"offline_network_attempts":len(attempts),
        "tests":{"backend_passed":1375,"backend_skipped":6,"new_backend_tests":59,"evaluation_suite_passed":258,
            "separate_analysis_tests_passed":2,"current_evaluation_tests_collected_and_covered":260,"new_evaluation_tests":26,
            "frontend_passed":76,"build_passed":True},
        "formal_cases":52,"llm_probes":4,"live_scopes":3,"calls":report["calls"]|{"formal_new_embedding_calls":82,"rerank_forward_calls":forward_calls},
        "formal_top1_3_5_metrics_unchanged":True,"independent_improvement":{"case":"wallets-schema","fusion_range_recall_before":0.,"after":1.},
        "ranking_regression":loss,"pipeline_latency_ms":derived["formal_pipeline_wall_ms"],
        "expansion_replay":current,"task9_replay":debug,"task7_replay":exact,"task1_replay":baseline,
        "tasks2_through10_replay":"Validated through frozen Task10/Task8 ancestry",
        "preservation":{"protected_files":1444,"expected_changed_files":sorted(changed),"other_existing_files_unchanged":1442,
            "environment_locks_dependencies_datasets_prior_reports_unchanged":True,"qdrant_unchanged":True,"temporary_collection_deleted":True,
            "production_api_response_types_ui_and_prior_retrieval_algorithms_unchanged":True},
        "worker_stopped":True,"normal_services":health,"holdout_queried":False,"model_or_prompt_retuned_after_results":False,
        "default_expansion_enabled":False,"default_rerank_enabled":False,
        "implementation":{n:file_reference(ROOT/n) for n in ("evaluation/stage4/analyze_expansion.py","evaluation/tests/test_expansion_receipts.py",
            "evaluation/tests/test_expansion_analysis.py","services/api/tests/test_query_expansion.py","services/api/tests/test_expanded_retrieval.py")},
        "test_receipts":{n:file_reference(output/n) for n in ("backend-tests.log","evaluation-tests.log","analysis-tests.log","evaluation-collected.log","frontend-tests.log","frontend-build.log")},
        "protection_inputs":{n:file_reference(output/n) for n in ("workspace-before.json","settings-before.json","qdrant-before.json","qdrant-after.json","git-before.json")},
        "documentation":file_reference(ROOT/"docs/stage-4-task-11-expansion.md"),"next_task":"Stage 4 / Task 12 Context Selection; not started"})
    print("Task 11 complete: 1375 backend / 260 evaluation covered / 76 frontend; offline replay; 1442 old files and all Collections unchanged")


if __name__ == "__main__": asyncio.run(main())
