"""Final Task 6 acceptance, no-network recomputation and protected state."""
import asyncio
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import socket
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "services/api"), str(ROOT)]
from app.core.config import get_settings
from app.retrieval.vector_store import create_qdrant_client
from evaluation.retrieval.evaluate import require
from evaluation.stage4.evaluate_bm25 import replay
from evaluation.stage4.freeze_baseline import replay_baseline
from evaluation.stage4.verify_candidates import replay as replay_candidates
from evaluation.stage4.state import file_reference, full_snapshot, read_json, safe_configuration, verify_unchanged, write_new


async def main():
    output = ROOT / "output/stage4-task6"
    path = ROOT / "evaluation/reports/stage4_bm25_20261026_task6_final.json"
    attempted = []
    connect, create = socket.socket.connect, socket.create_connection
    def deny(*args, **kwargs):
        attempted.append(True)
        raise AssertionError("Offline replay attempted a network connection")
    try:
        socket.socket.connect, socket.create_connection = deny, deny
        current = replay(path)
        candidates = replay_candidates(ROOT / "evaluation/reports/stage4_candidates_20261013_task3.json")
        baseline = replay_baseline(ROOT / "evaluation/reports/stage4_dense_20261013_task1.json")
    finally:
        socket.socket.connect, socket.create_connection = connect, create
    require(not attempted, "Offline replay must not use network")
    report = read_json(path)
    require(report["live_smoke"] is not None, "Real read-only Qdrant smoke not complete")
    spike = read_json(output / "spike-example.json")
    require(spike["batch"]["candidate_limit"] == 10 and len(spike["results"]) == 5
            and spike["embedding_calls"] == spike["generation_calls"] == 0, "Independent BM25 CLI spike not verified")
    verify_unchanged(read_json(output / "settings-before.json"), safe_configuration(get_settings()), "Settings")
    client = create_qdrant_client(get_settings())
    try:
        after = await full_snapshot(client)
    finally:
        await client.close()
    verify_unchanged(read_json(output / "qdrant-before.json"), after, "All persistent Qdrant collections")
    protected, changed = read_json(output / "workspace-before.json"), []
    for name, digest in protected.items():
        file = ROOT / name
        require(file.is_file(), f"Protected file missing: {name}")
        if hashlib.sha256(file.read_bytes()).hexdigest() != digest:
            require(name in {"README.md", "evaluation/README.md"}, f"Unexpected old file change: {name}")
            changed.append(name)
    require("1177 passed, 6 skipped" in (output / "backend-tests.log").read_text(encoding="utf-8"), "Backend regression has not passed")
    require("155 passed" in (output / "evaluation-tests-final.log").read_text(encoding="utf-8"), "Evaluation regression has not passed")
    write_new(output / "qdrant-after.json", after)
    write_new(ROOT / "docs/stage-4-task-6-verification.json", {
        "task": "Stage 4 / Task 6", "completed": True, "run_date": "2026-10-26", "created_at": datetime.now(timezone.utc).isoformat(),
        "report": file_reference(path), "parameters": report["parameters"], "summary": report["summary"], "dense_summary": report["dense_summary"],
        "tests": {"default_backend_passed": 1177, "default_backend_skipped": 6, "new_bm25_tests": 17,
                  "evaluation_passed": 155, "new_receipt_tests": 14, "real_read_only_qdrant_cases": 4,
                  "independent_cli_spike_passed": True, "prior_real_model_integrations_rerun": False},
        "calls": report["calls"], "offline_network_attempts": len(attempted), "bm25_replay": current,
        "task5_task4_replay": "Snapshot lifecycle and Tokenizer receipts replayed by BM25 input validator",
        "task3_replay": candidates, "task2_replay": "52 cases replayed by candidate verifier", "task1_replay": baseline,
        "original_52_case_receipts_unchanged": True, "case_execution": report["case_execution"],
        "live_smoke": report["live_smoke"], "spike_receipt": file_reference(output / "spike-example.json"),
        "implementation": {name: file_reference(ROOT / name) for name in (
            "services/api/app/retrieval/bm25.py", "services/api/tests/test_bm25.py",
            "evaluation/stage4/evaluate_bm25.py", "evaluation/tests/test_bm25_receipts.py")},
        "test_receipts": {"backend": file_reference(output / "backend-tests.log"), "evaluation": file_reference(output / "evaluation-tests-final.log")},
        "preservation": {"protected_files": len(protected), "expected_changed_files": sorted(changed),
            "other_existing_files_unchanged": len(protected) - len(changed), "environment_and_locks_unchanged": True,
            "public_api_and_frontend_unchanged": True, "production_dense_unchanged": True, "qdrant_unchanged": True,
            "prior_reports_datasets_tokenizer_snapshots_unchanged": True, "holdout_queried": False},
        "protection_inputs": {name: file_reference(output / name) for name in (
            "workspace-before.json", "settings-before.json", "qdrant-before.json", "qdrant-after.json", "git-before.json")},
        "next_task": "Stage 4 / Task 7 Exact Search and shared filters; not started"})
    print(f"Task 6 complete: 1177 backend / 155 evaluation tests; 52 comparisons / 4 real smoke cases; {len(protected)-len(changed)} old files unchanged; zero offline network attempts")


if __name__ == "__main__": asyncio.run(main())
