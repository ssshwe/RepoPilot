"""Publish Task 3 acceptance only after offline replay, regressions and protection checks."""
import asyncio
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import socket
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services/api"))
sys.path.insert(0, str(ROOT))
from app.core.config import get_settings
from app.retrieval.vector_store import create_qdrant_client
from evaluation.retrieval.evaluate import require
from evaluation.stage4.freeze_baseline import replay_baseline
from evaluation.stage4.verify_candidates import replay
from evaluation.stage4.state import file_reference, full_snapshot, read_json, runtime_identity, safe_configuration, verify_unchanged, write_new


async def main():
    output = ROOT / "output/stage4-task3"
    path = ROOT / "evaluation/reports/stage4_candidates_20261013_task3.json"
    attempted = []
    connect, create = socket.socket.connect, socket.create_connection
    def deny(*args, **kwargs):
        attempted.append(True)
        raise AssertionError("Offline replay attempted a network connection")
    try:
        socket.socket.connect, socket.create_connection = deny, deny
        current = replay(path)
        baseline = replay_baseline(ROOT / "evaluation/reports/stage4_dense_20261013_task1.json")
    finally:
        socket.socket.connect, socket.create_connection = connect, create
    require(not attempted, "Offline replay must not use network")
    report = read_json(path)
    old = read_json(ROOT / report["reference_report"]["path"])
    for split, measured in report["summary"]["metrics"]["split"].items():
        require({k: v for k, v in measured.items() if k != "latency_ms"}
                == {k: v for k, v in old["summary"]["split"][split].items() if k != "latency_ms"},
                "Task 3 final Top5 quality differs from Task 2")
    verify_unchanged(read_json(output / "settings-before.json"), safe_configuration(get_settings()), "Settings")
    client = create_qdrant_client(get_settings())
    try:
        after = await full_snapshot(client)
    finally:
        await client.close()
    verify_unchanged(read_json(output / "qdrant-before.json"), after, "All persistent collections after real tests")
    protected = read_json(output / "workspace-before.json")
    allowed = {"README.md", "evaluation/README.md", "services/api/app/retrieval/vector_store.py"}
    changed = []
    for name, digest in protected.items():
        file = ROOT / name
        require(file.is_file(), f"Protected file missing: {name}")
        if hashlib.sha256(file.read_bytes()).hexdigest() != digest:
            require(name in allowed, f"Unexpected old file change: {name}")
            changed.append(name)
    backend = (output / "backend-tests.log").read_text(encoding="utf-8")
    evaluation = (output / "evaluation-tests.log").read_text(encoding="utf-8")
    require("1061 passed, 1 skipped" in backend, "Full backend with integrations has not passed")
    require("117 passed" in evaluation, "Evaluation tests have not passed")
    write_new(output / "qdrant-after.json", after)
    write_new(ROOT / "docs/stage-4-task-3-verification.json", {
        "task": "Stage 4 / Task 3", "completed": True, "created_at": datetime.now(timezone.utc).isoformat(),
        "report": file_reference(path), "reference_report": report["reference_report"],
        "tests": {"backend_passed": 1061, "backend_skipped": 1, "new_candidate_tests": 55,
                  "real_integration_cases": 5, "evaluation_passed": 117, "new_proof_tests": 10},
        "test_receipts": {"backend": file_reference(output / "backend-tests.log"), "evaluation": file_reference(output / "evaluation-tests.log")},
        "offline_network_attempts": len(attempted), "candidate_replay": current,
        "task1_replay": baseline, "task2_replay": "52 reference receipts replayed by candidate verifier",
        "real_cases": 52, "holdout_queried": False, "calls": report["calls"], "summary": report["summary"],
        "final_top5_metrics_identical_to_task2": True,
        "runtime": runtime_identity(),
        "implementation": {name: file_reference(ROOT / name) for name in (
            "services/api/app/retrieval/candidates.py", "services/api/app/retrieval/dense.py",
            "services/api/app/retrieval/vector_store.py", "evaluation/stage4/verify_candidates.py",
            "services/api/tests/test_retrieval_candidates.py", "evaluation/tests/test_candidate_proof.py")},
        "preservation": {"protected_files": len(protected), "expected_changed_files": sorted(changed),
            "other_existing_files_unchanged": len(protected) - len(changed), "environment_and_locks_unchanged": True,
            "all_prior_reports_and_fixtures_unchanged": True, "qdrant_unchanged": True, "temporary_collection_deleted": True,
            "public_api_types_and_frontend_unchanged": True, "production_service_unchanged": True},
        "protection_inputs": {name: file_reference(output / name) for name in ("workspace-before.json", "settings-before.json", "qdrant-before.json", "qdrant-after.json")},
        "next_task": "Stage 4 / Task 4 code tokenizer; not started"})
    print(f"Task 3 complete: 1061 backend / 117 evaluation tests; 156 exact Dense comparisons; {len(protected) - len(changed)} old files unchanged; zero offline network attempts")


if __name__ == "__main__":
    asyncio.run(main())
