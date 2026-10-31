"""Final Task 10 acceptance: offline lineage, actual receipts and protected state."""
import asyncio
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re
import socket
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/"services/api"), str(ROOT)]
import httpx
from app.core.config import get_settings
from app.retrieval.vector_store import create_qdrant_client
from evaluation.retrieval.evaluate import require
from evaluation.stage4.evaluate_rerank import replay
from evaluation.stage4.verify_debug import replay as replay_debug
from evaluation.stage4.verify_exact import replay as replay_exact
from evaluation.stage4.freeze_baseline import replay_baseline
from evaluation.stage4.state import read_json, write_new, file_reference, full_snapshot, safe_configuration, verify_unchanged


async def main():
    output = ROOT/"output/stage4-task10"
    path = ROOT/"evaluation/reports/stage4_rerank_20261031_task10_final.json"
    attempts = []
    connect, create = socket.socket.connect, socket.create_connection
    def deny(*args, **kwargs):
        attempts.append(True)
        raise AssertionError("Offline replay attempted network access")
    try:
        socket.socket.connect, socket.create_connection = deny, deny
        current = await replay(path)
        debug = replay_debug(ROOT/"evaluation/reports/stage4_debug_20261031_task9_release.json")
        exact = await replay_exact(ROOT/"evaluation/reports/stage4_exact_20261026_task7.json")
        baseline = replay_baseline(ROOT/"evaluation/reports/stage4_dense_20261013_task1.json")
    finally:
        socket.socket.connect, socket.create_connection = connect, create
    require(not attempts, "Offline network access detected")
    report = read_json(path)
    require(report["performance"]["successful_queries"] == 50 and report["performance"]["empty_queries"] == 2
            and report["performance"]["degraded_queries"] == 0, "Formal case outcomes differ")
    require(report["calls"]["completed_forward_calls"] == 484 and report["calls"]["new_embedding_calls"] == report["calls"]["generation_calls"] == 0,
            "Formal/probe model accounting differs")
    first = read_json(output/"first-run-failure.json")
    require(first["completed_rerank_requests"] == 1 and first["raw_score_receipt_saved"] is False and first["case_id"] == "refund-service",
            "Initial tool failure cost/limitation omitted")
    old = read_json(ROOT/"evaluation/reports/stage4_rerank_20261031_task10_artifacts/worker-ready.json")
    require(old["manifest_sha256"] == report["model_manifest"]["sha256"], "Failed run used another model")
    require(file_reference(ROOT/".tools/reranker-model/manifest.json")["sha256"] == report["model_manifest"]["sha256"], "Local model manifest drift")
    manifest = read_json(ROOT/report["model_manifest"]["path"])
    for name, item in manifest["files"].items():
        with (ROOT/".tools/reranker-model"/name).open("rb") as stream:
            require(hashlib.file_digest(stream, "sha256").hexdigest() == item["sha256"], "Local model bytes drift")
    test_logs = {"backend-tests-final.log": (1316, "passed"), "evaluation-tests-final.log": (234, "passed"), "frontend-tests.log": (76, "pass")}
    for name, (count, word) in test_logs.items():
        text = (output/name).read_text(encoding="utf-8")
        require(bool(re.search(rf"\b{count} {word}\b", text) if word == "passed" else re.search(rf"\bpass {count}\b", text)), "Expected complete test result missing: "+name)
    require("6 skipped" in (output/"backend-tests-final.log").read_text(encoding="utf-8"), "Integration opt-in skip inventory differs")
    require("built in" in (output/"frontend-build.log").read_text(encoding="utf-8"), "Frontend build missing")
    require("No broken requirements found" in (output/"worker-pip-check.txt").read_text(encoding="utf-8"), "Isolated dependency check failed")
    protected = read_json(output/"workspace-before.json")
    changed = []
    allowed = {"README.md", "evaluation/README.md"}
    for name, expected in protected.items():
        file = ROOT/name
        require(file.is_file(), "Protected file missing: "+name)
        if hashlib.sha256(file.read_bytes()).hexdigest() != expected:
            require(name in allowed, "Unplanned existing file change: "+name)
            changed.append(name)
    require(len(protected) == 1329 and set(changed) == allowed, "Protection inventory differs")
    verify_unchanged(read_json(output/"settings-before.json"), safe_configuration(get_settings()), "Configuration")
    client = create_qdrant_client(get_settings())
    try:
        after = await full_snapshot(client)
    finally:
        await client.close()
    verify_unchanged(read_json(output/"qdrant-before.json"), after, "All existing Qdrant Collections")
    # No inference process may remain after the timeout/close proof.
    import subprocess
    process_rows = subprocess.check_output(["powershell", "-NoProfile", "-Command",
        "Get-CimInstance Win32_Process -Filter \"name = 'python.exe'\" | Select-Object -ExpandProperty CommandLine"], text=True, encoding="utf-8", errors="replace")
    require("reranker_worker.py" not in process_rows, "Reranker worker still alive")
    with httpx.Client(trust_env=False, timeout=10) as http:
        health = {"api": http.get("http://127.0.0.1:8000/health").status_code,
                  "web": http.get("http://127.0.0.1:5173/").status_code}
    require(all(code == 200 for code in health.values()), "Normal development services unavailable")
    write_new(output/"qdrant-after.json", after)
    write_new(ROOT/"docs/stage-4-task-10-verification.json", {
        "task": "Stage 4 / Task 10", "completed": True, "run_date": "2026-10-31", "created_at": datetime.now(timezone.utc).isoformat(),
        "report": file_reference(path), "tests": {"backend_passed": 1316, "backend_skipped": 6, "new_backend_tests": 37,
            "evaluation_passed": 234, "new_evaluation_tests": 27, "frontend_passed": 76, "build_passed": True},
        "offline_network_attempts": len(attempts), "rerank_replay": current, "task9_replay": debug, "task7_replay": exact, "task1_replay": baseline,
        "tasks2_through8_replay": "Recomputed through the frozen Hybrid report ancestry",
        "calls": {"formal_completed_rerank_requests": 50, "empty_queries_without_inference": 2, "formal_scored_pairs": 1320,
            "formal_forward_calls": 358, "completed_sweep_requests": 9, "sweep_forward_calls": 126,
            "timeout_interrupted_requests": 1, "interrupted_forward_calls": "unknown",
            "initial_tool_failure_completed_rerank_requests": 1, "initial_tool_failure_forward_calls": "unknown; raw response was not saved",
            "all_implementation_completed_rerank_requests": 60, "new_embedding_calls": 0, "generation_calls": 0},
        "initial_tool_failure": file_reference(output/"first-run-failure.json"), "worker_stopped": True,
        "formal_parameters_retuned": False, "holdout_queried": False, "default_enabled": False,
        "performance": report["performance"], "normal_services": health,
        "preservation": {"protected_files": 1329, "expected_changed_files": sorted(changed), "other_existing_files_unchanged": 1327,
            "actual_environment_dependencies_locks_datasets_prior_reports_unchanged": True, "all_qdrant_collections_unchanged": True,
            "production_api_types_ui_and_existing_retrieval_algorithms_unchanged": True},
        "test_receipts": {n: file_reference(output/n) for n in (*test_logs, "frontend-build.log", "worker-pip-check.txt", "worker-freeze.txt")},
        "protection_inputs": {n: file_reference(output/n) for n in ("workspace-before.json", "settings-before.json", "qdrant-before.json", "qdrant-after.json", "git-before.json")},
        "implementation": {n: file_reference(ROOT/n) for n in ("services/api/tests/test_rerank.py", "services/api/tests/fixtures/reranker_worker_stub.py",
            "evaluation/tests/test_rerank_receipts.py", "evaluation/stage4/download_reranker.py", "evaluation/stage4/reranker-requirements.txt")},
        "documentation": file_reference(ROOT/"docs/stage-4-task-10-reranker.md"), "next_task": "Stage 4 / Task 11 Query Expansion; not started"})
    print("Task 10 complete: 1316 backend / 234 evaluation / 76 frontend; offline replay passed; 1327 old files and all Collections unchanged")


if __name__ == "__main__":
    asyncio.run(main())
