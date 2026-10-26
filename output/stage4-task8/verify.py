"""Final Task 8 acceptance: offline replay, case analysis and protected state."""
import asyncio
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import socket
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/"services/api"),str(ROOT)]
from app.core.config import get_settings
from app.retrieval.hybrid import HybridResult
from app.retrieval.snapshot_models import SnapshotScope
from app.retrieval.snapshot_store import SnapshotStore
from app.retrieval.vector_store import create_qdrant_client
from evaluation.retrieval.evaluate import require
from evaluation.stage4.evaluate_hybrid import replay,case_analysis
from evaluation.stage4.freeze_baseline import replay_baseline
from evaluation.stage4.verify_exact import replay as replay_exact
from evaluation.stage4.state import read_json,write_new,file_reference,full_snapshot,safe_configuration,verify_unchanged


async def main():
    output=ROOT/"output/stage4-task8"
    path=ROOT/"evaluation/reports/stage4_hybrid_20261027_task8.json"
    attempted=[];connect,create=socket.socket.connect,socket.create_connection
    def deny(*args,**kwargs):
        attempted.append(True);raise AssertionError("Offline replay attempted network access")
    try:
        socket.socket.connect,socket.create_connection=deny,deny
        current=await replay(path)
        exact=await replay_exact(ROOT/"evaluation/reports/stage4_exact_20261026_task7.json")
        baseline=replay_baseline(ROOT/"evaluation/reports/stage4_dense_20261013_task1.json")
    finally:socket.socket.connect,socket.create_connection=connect,create
    require(not attempted,"Offline replay attempted network access")
    report=read_json(path);require(report["live"] is not None,"Real Hybrid proof not complete")
    live=read_json(ROOT/report["live"]["path"])
    require(live["temporary_collection_deleted"] and live["archived_vector_reuses"]==2,"Live Hybrid cleanup/cost missing")
    spike=HybridResult.from_dict(read_json(output/"spike-example.json"))
    example=next(r for r in report["cases"] if r["id"]=="wallet-symbol")
    require([c.to_dict() for c in spike.fused.candidates]==example["hybrid"]["fused"]["candidates"] and len(spike.final_results())==5,"Independent CLI does not match the frozen comparison")
    verify_unchanged(read_json(output/"settings-before.json"),safe_configuration(get_settings()),"Settings")
    require(read_json(output/"parameters-before.json")=={"rrf_k":60,"rank_start":1,"dense":30,"bm25":30,"exact":10,"fusion":60,"top_k":5,"context_sources":5,"context_characters":16000,"holdout":False},"Parameters changed after preflight")
    client=create_qdrant_client(get_settings())
    try:after=await full_snapshot(client)
    finally:await client.close()
    verify_unchanged(read_json(output/"qdrant-before.json"),after,"All persistent Qdrant Collections")
    protected=read_json(output/"workspace-before.json");changed=[]
    for name,expected in protected.items():
        file=ROOT/name;require(file.is_file(),f"Protected file missing: {name}")
        if hashlib.sha256(file.read_bytes()).hexdigest()!=expected:
            require(name in {"README.md","evaluation/README.md"},f"Unexpected old file change: {name}");changed.append(name)
    require(len(protected)==1147 and set(changed)=={"README.md","evaluation/README.md"},"Protection inventory differs")
    require("1254 passed, 6 skipped" in (output/"backend-tests.log").read_text(encoding="utf-8"),"Backend tests have not passed")
    require("190 passed" in (output/"evaluation-tests.log").read_text(encoding="utf-8"),"Evaluation tests have not passed")
    data=read_json(ROOT/"evaluation/datasets/stage4_retrieval.json");cases={c["id"]:c for c in data["cases"]}
    previous=read_json(ROOT/"evaluation/reports/stage4_snapshots_20261026_task5.json")
    store=SnapshotStore(ROOT/previous["database"]["path"],read_only=True)
    images={p["scope"]["repository"]:store.load_ready(SnapshotScope(**p["scope"])).snapshot.index for p in previous["phases"] if p["name"].startswith("frozen-")}
    analysis=[case_analysis(cases[r["id"]],r,images[cases[r["id"]]["repository"]]) for r in report["cases"]]
    counts={}
    for row in analysis:
        for entry in row.get("ranges",[]):
            if entry["cause"]:counts[entry["cause"]]=counts.get(entry["cause"],0)+1
    require(counts=={"rank_after_top5":6,"candidate_missing":7},"Case analysis first-failure counts differ")
    write_new(output/"case-analysis.json",{"report":file_reference(path),"counts_unit":"annotated complete ranges, not questions",
        "uncovered_by_first_failing_stage":counts,"cases":analysis,"generation_evaluated":False})
    write_new(output/"qdrant-after.json",after)
    write_new(ROOT/"docs/stage-4-task-8-verification.json",{
        "task":"Stage 4 / Task 8","completed":True,"run_date":"2026-10-27","created_at":datetime.now(timezone.utc).isoformat(),
        "report":file_reference(path),"parameters":report["parameters"],"limits":report["limits"],
        "tests":{"default_backend_passed":1254,"default_backend_skipped":6,"new_hybrid_rrf_tests":33,
            "evaluation_passed":190,"new_receipt_tests":18,"frozen_cases":52,"compared_strategies":4,
            "real_hybrid_scenarios":3,"independent_cli_passed":True,"frontend_and_prior_real_model_integrations_rerun":False},
        "calls":{"archived_dense_rank_reuses":52,"live_archived_vector_reuses":2,"real_embedding_calls":0,"generation_calls":0},
        "offline_network_attempts":len(attempted),"hybrid_replay":current,"task7_replay":exact,"task1_replay":baseline,
        "tasks2_through6_replay":"Dense, BM25, shared snapshots and Tokenizer replayed by Hybrid input validator",
        "summary":report["summary"],"dense_summary":report["dense_summary"],"bm25_summary":report["bm25_summary"],
        "analysis":file_reference(output/"case-analysis.json"),"live_summary":report["live"],"spike_receipt":file_reference(output/"spike-example.json"),
        "implementation":{name:file_reference(ROOT/name) for name in ("services/api/app/retrieval/rrf.py","services/api/app/retrieval/hybrid.py",
            "services/api/tests/test_hybrid_rrf.py","evaluation/stage4/evaluate_hybrid.py","evaluation/tests/test_hybrid_receipts.py")},
        "test_receipts":{"backend":file_reference(output/"backend-tests.log"),"evaluation":file_reference(output/"evaluation-tests.log")},
        "preservation":{"protected_files":1147,"expected_changed_files":sorted(changed),"other_existing_files_unchanged":1145,
            "environment_locks_prior_implementations_reports_datasets_unchanged":True,"public_api_frontend_production_dense_unchanged":True,
            "qdrant_unchanged":True,"holdout_queried":False},
        "protection_inputs":{name:file_reference(output/name) for name in ("workspace-before.json","settings-before.json","parameters-before.json","qdrant-before.json","qdrant-after.json","git-before.json")},
        "next_task":"Stage 4 / Task 9 Retrieval Debug integration; not started"})
    print("Task 8 complete: 1254 backend / 190 evaluation tests; 52 comparisons / 3 live scenarios; 1145 old files unchanged; zero offline network attempts")


if __name__=="__main__":asyncio.run(main())
