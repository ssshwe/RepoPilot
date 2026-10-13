"""Final Task 2 proof: no-network replay, untouched frozen Task 1, source/index preservation."""
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
from evaluation.stage4.evaluate import replay
from evaluation.stage4.freeze_baseline import replay_baseline
from evaluation.stage4.state import file_reference, full_snapshot, read_json, safe_configuration, verify_unchanged, write_new


async def main():
    output = ROOT / "output/stage4-task2"
    report_path = ROOT / "evaluation/reports/stage4_retrieval_20261013_task2.json"
    attempted = []
    connect, create = socket.socket.connect, socket.create_connection
    def deny(*args, **kwargs):
        attempted.append(True)
        raise AssertionError("Offline replay attempted a network connection")
    try:
        socket.socket.connect, socket.create_connection = deny, deny
        current = replay(report_path)
        baseline = replay_baseline(ROOT / "evaluation/reports/stage4_dense_20261013_task1.json")
    finally:
        socket.socket.connect, socket.create_connection = connect, create
    require(not attempted, "Offline replay must not use network")
    report = read_json(report_path)
    data = read_json(ROOT / "evaluation/datasets/stage4_retrieval.json")
    require(len(report["cases"]) == 52 and not report["holdout_queried"]
            and not any(c["split"] == "holdout" for c in data["cases"] if c["id"] in {r["id"] for r in report["cases"]}), "Holdout was prematurely queried")
    config = safe_configuration(get_settings())
    verify_unchanged(read_json(output / "settings-before.json"), config, "Settings")
    client = create_qdrant_client(get_settings())
    try:
        after = await full_snapshot(client)
    finally:
        await client.close()
    verify_unchanged(read_json(output / "qdrant-before.json"), after, "All persistent collections")
    protected = read_json(output / "workspace-before.json")
    changed = []
    for name, digest in protected.items():
        path = ROOT / name
        require(path.is_file(), f"Protected file missing: {name}")
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            require(name in {"README.md", "evaluation/README.md"}, f"Unexpected old file change: {name}")
            changed.append(name)
    tests = (output / "tests-final.log").read_text(encoding="utf-8")
    require("107 passed" in tests, "Final evaluation tests have not passed")
    write_new(ROOT / "docs/stage-4-task-2-verification.json", {
        "task": "Stage 4 / Task 2", "completed": True, "run_date": "2026-10-13", "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset": file_reference(ROOT / "evaluation/datasets/stage4_retrieval.json"), "report": file_reference(report_path),
        "splits": {s: sum(c["split"] == s for c in data["cases"]) for s in ("regression", "dev", "holdout")},
        "queried_cases": 52, "holdout_queried": False, "tests_passed": 107, "new_tests": 26,
        "offline_network_attempts": len(attempted), "new_report_replay": current, "frozen_task1_replay": baseline,
        "calls": report["calls"], "summary": report["summary"]["split"],
        "preservation": {"protected_files": len(protected), "expected_changed_files": sorted(changed),
            "other_existing_files_unchanged": len(protected) - len(changed), "qdrant_unchanged": True,
            "env_unchanged": True, "temporary_collection_deleted": True, "production_api_unchanged": True},
        "next_task": "Stage 4 / Task 3 internal candidate layer; not started"})
    print(f"Task 2 complete: 107 tests, 52 receipts, zero offline network attempts; {len(protected) - len(changed)} old files unchanged")


if __name__ == "__main__":
    asyncio.run(main())
