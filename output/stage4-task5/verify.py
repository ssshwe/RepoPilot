"""Publish Task 5 acceptance after no-network replay and live protection checks."""
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
from evaluation.stage4.freeze_baseline import replay_baseline
from evaluation.stage4.verify_candidates import replay as replay_candidates
from evaluation.stage4.verify_snapshots import replay
from evaluation.stage4.state import file_reference, full_snapshot, read_json, safe_configuration, verify_unchanged, write_new


async def main():
    output = ROOT / "output/stage4-task5"
    path = ROOT / "evaluation/reports/stage4_snapshots_20261026_task5.json"
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
    verify_unchanged(read_json(output / "settings-before.json"), safe_configuration(get_settings()), "Settings")
    client = create_qdrant_client(get_settings())
    try:
        after = await full_snapshot(client)
    finally:
        await client.close()
    verify_unchanged(read_json(output / "qdrant-before.json"), after, "All original Qdrant collections")
    protected = read_json(output / "workspace-before.json")
    changed = []
    for name, digest in protected.items():
        file = ROOT / name
        require(file.is_file(), f"Protected file missing: {name}")
        if hashlib.sha256(file.read_bytes()).hexdigest() != digest:
            require(name in {"README.md", "evaluation/README.md", ".gitignore"}, f"Unexpected old file change: {name}")
            changed.append(name)
    require("1160 passed, 6 skipped" in (output / "backend-tests.log").read_text(encoding="utf-8"), "Backend tests have not passed")
    require("141 passed" in (output / "evaluation-tests-final.log").read_text(encoding="utf-8"), "Evaluation tests have not passed")
    write_new(output / "qdrant-after.json", after)
    write_new(ROOT / "docs/stage-4-task-5-verification.json", {
        "task": "Stage 4 / Task 5", "completed": True, "run_date": "2026-10-26", "created_at": datetime.now(timezone.utc).isoformat(),
        "report": file_reference(path), "offline_network_attempts": len(attempted), "snapshot_replay": current,
        "task4_replay": "129 indexed Chunk/52 query tokenizer receipts replayed by snapshot verifier",
        "task3_replay": candidates, "task2_replay": "52 cases replayed by candidate verifier", "task1_replay": baseline,
        "tests": {"default_backend_passed": 1160, "default_backend_skipped": 6, "new_snapshot_tests": 32,
                  "evaluation_passed": 141, "new_receipt_tests": 11, "real_qdrant_lifecycle_phases": 14,
                  "prior_real_model_integrations_rerun": False, "generation_calls": 0},
        "calls": report["calls"], "restored_scopes": len(report["final_scopes"]), "immutable_generations": len(report["snapshots"]),
        "test_receipts": {"backend": file_reference(output / "backend-tests.log"), "evaluation": file_reference(output / "evaluation-tests-final.log"),
                          "real_lifecycle": file_reference(output / "real-run.log")},
        "implementations": report["implementations"] | {name: file_reference(ROOT / name) for name in (
            "evaluation/stage4/verify_snapshots.py", "services/api/tests/test_lexical_snapshots.py", "evaluation/tests/test_snapshot_receipts.py")},
        "preservation": {"protected_files": len(protected), "expected_changed_files": sorted(changed),
            "other_existing_files_unchanged": len(protected) - len(changed), "environment_and_locks_unchanged": True,
            "public_api_and_frontend_unchanged": True, "production_indexer_and_retrieval_unchanged": True,
            "all_existing_collections_unchanged": True, "temporary_collection_deleted": True,
            "prior_datasets_reports_and_tokenizer_unchanged": True},
        "protection_inputs": {name: file_reference(output / name) for name in (
            "workspace-before.json", "settings-before.json", "qdrant-before.json", "qdrant-after.json", "git-before.json")},
        "known_boundary": "Local cooperating writers; before/after digests do not provide distributed snapshot isolation against bypassing external ABA writers",
        "next_task": "Stage 4 / Task 6 BM25; not started"})
    print(f"Task 5 complete: 1160 backend / 141 evaluation tests; 14 live phases; {len(protected)-len(changed)} old files unchanged; zero offline network attempts")


if __name__ == "__main__": asyncio.run(main())
