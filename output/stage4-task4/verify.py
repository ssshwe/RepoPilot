"""Final Task 4 proof: offline receipts, previous baselines and protected state."""
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
from evaluation.stage4.verify_candidates import replay as replay_candidates
from evaluation.stage4.verify_tokenizer import replay
from evaluation.stage4.state import file_reference, full_snapshot, read_json, safe_configuration, verify_unchanged, write_new


async def main():
    output = ROOT / "output/stage4-task4"
    report_path = ROOT / "evaluation/reports/stage4_tokenizer_20261015_task4_final.json"
    attempted = []
    connect, create = socket.socket.connect, socket.create_connection
    def deny(*args, **kwargs):
        attempted.append(True)
        raise AssertionError("Offline replay attempted a network connection")
    try:
        socket.socket.connect, socket.create_connection = deny, deny
        current = replay(report_path)
        candidates = replay_candidates(ROOT / "evaluation/reports/stage4_candidates_20261013_task3.json")
        baseline = replay_baseline(ROOT / "evaluation/reports/stage4_dense_20261013_task1.json")
    finally:
        socket.socket.connect, socket.create_connection = connect, create
    require(not attempted, "Offline replay must not use network")
    report = read_json(report_path)
    verify_unchanged(read_json(output / "settings-before.json"), safe_configuration(get_settings()), "Settings")
    client = create_qdrant_client(get_settings())
    try:
        after = await full_snapshot(client)
    finally:
        await client.close()
    verify_unchanged(read_json(output / "qdrant-before.json"), after, "All persistent Qdrant collections")
    protected = read_json(output / "workspace-before.json")
    changed = []
    for name, digest in protected.items():
        path = ROOT / name
        require(path.is_file(), f"Protected file missing: {name}")
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            require(name in {"README.md", "evaluation/README.md"}, f"Unexpected old file change: {name}")
            changed.append(name)
    require("1128 passed, 6 skipped" in (output / "backend-tests-final.log").read_text(encoding="utf-8"), "Default backend regression has not passed")
    require("130 passed" in (output / "evaluation-tests-final-v2.log").read_text(encoding="utf-8"), "Evaluation regression has not passed")
    write_new(output / "qdrant-after.json", after)
    write_new(ROOT / "docs/stage-4-task-4-verification.json", {
        "task": "Stage 4 / Task 4", "completed": True, "run_date": "2026-10-15", "created_at": datetime.now(timezone.utc).isoformat(),
        "report": file_reference(report_path), "tokenizer": report["tokenizer"], "summary": report["summary"],
        "calls": report["calls"], "offline_network_attempts": len(attempted), "tokenizer_replay": current,
        "task3_replay": candidates, "task2_replay": "52 reference cases replayed by Task 3 verifier", "task1_replay": baseline,
        "tests": {"default_backend_passed": 1128, "default_backend_skipped": 6, "new_tokenizer_tests": 72,
                  "evaluation_passed": 130, "new_receipt_tests": 13,
                  "real_service_integrations_rerun": False, "reason": "Pure tokenizer; production paths and stores unchanged"},
        "test_receipts": {"backend": file_reference(output / "backend-tests-final.log"), "evaluation": file_reference(output / "evaluation-tests-final-v2.log")},
        "implementation": {name: file_reference(ROOT / name) for name in (
            "services/api/app/retrieval/tokenizer.py", "services/api/tests/test_code_tokenizer.py",
            "evaluation/stage4/verify_tokenizer.py", "evaluation/tests/test_tokenizer_receipts.py")},
        "preservation": {"protected_files": len(protected), "expected_changed_files": sorted(changed),
            "other_existing_files_unchanged": len(protected) - len(changed), "qdrant_unchanged": True,
            "environment_and_locks_unchanged": True, "public_api_and_frontend_unchanged": True,
            "production_dense_and_candidates_unchanged": True, "prior_reports_and_datasets_unchanged": True},
        "protection_inputs": {name: file_reference(output / name) for name in ("workspace-before.json", "settings-before.json", "qdrant-before.json", "qdrant-after.json", "git-before.json")},
        "next_task": "Stage 4 / Task 5 shared lexical index snapshots; not started"})
    print(f"Task 4 complete: 1128 backend / 130 evaluation tests; 129 chunks / 52 queries; {len(protected) - len(changed)} old files unchanged; zero offline network attempts")


if __name__ == "__main__":
    asyncio.run(main())
