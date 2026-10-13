"""Final Task 1 audit: offline replay with network denied, live preservation, report bindings."""
import asyncio
from collections import Counter
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
from evaluation.stage4.state import file_reference, full_snapshot, read_json, safe_configuration, verify_unchanged, write_new

BASELINE = ROOT / "evaluation/reports/stage4_dense_20261013_task1.json"


async def main():
    network_attempts = []
    original_connect = socket.socket.connect
    original_create = socket.create_connection
    def deny(*args, **kwargs):
        network_attempts.append(True)
        raise AssertionError("Offline replay attempted network access")
    try:
        socket.socket.connect = deny
        socket.create_connection = deny
        replay = replay_baseline(BASELINE)
    finally:
        socket.socket.connect = original_connect
        socket.create_connection = original_create
    require(not network_attempts, "Offline replay must not use network")
    baseline = read_json(BASELINE)
    acceptance = read_json(ROOT / baseline["acceptance"]["path"])
    settings = get_settings()
    verify_unchanged(baseline["configuration"], safe_configuration(settings), "Final configuration")
    client = create_qdrant_client(settings)
    try:
        after = await full_snapshot(client)
    finally:
        await client.close()
    verify_unchanged(read_json(ROOT / baseline["artifacts"]["index"]["path"]), after, "Final index")
    protected = read_json(ROOT / "output/stage3-task13/workspace-before.json")
    changed = []
    for name, digest in protected.items():
        path = ROOT / name
        require(path.is_file(), f"Protected file missing: {name}")
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            changed.append(name)
    require(sorted(changed) == acceptance["preservation"]["expected_changed_files"], "Protected files changed after Task 13")
    require(baseline["additional_calls"] == {"embedding_provider_calls": 6, "generation_calls": 0}, "Unexpected Task 1 calls")
    require(all(d["prefix_matches_original_top5"] for d in baseline["diagnostics"]), "This run's prefix consistency changed")
    categories = Counter(f["category"] for row in baseline["bad_cases"] for f in row["failures"])
    write_new(ROOT / "docs/stage-4-task-1-verification.json", {
        "task": "Stage 4 / Task 1", "completed": True, "run_date": "2026-10-13", "created_at": datetime.now(timezone.utc).isoformat(),
        "baseline": file_reference(BASELINE), "stage3_acceptance": baseline["acceptance"],
        "identity": baseline["identity"], "offline_replay": replay, "offline_network_attempts": len(network_attempts),
        "additional_calls": baseline["additional_calls"], "diagnostic_queries": len(baseline["diagnostics"]),
        "all_diagnostic_prefixes_match": True, "bad_case_count": sum(bool(r["failures"]) for r in baseline["bad_cases"]),
        "classification_observations": dict(categories), "index_or_recall_missing_observations": categories.get("index_or_recall_missing", 0),
        "summary": baseline["summary"], "tests": acceptance["tests"],
        "preservation": acceptance["preservation"] | {"qdrant_unchanged": True, "configuration_unchanged": True, "sources_unchanged": True},
        "artifacts": baseline["artifacts"], "next_task": "Stage 4 / Task 2; not started"})
    print(f"Task 1 verified: offline network attempts=0; {len(protected) - len(changed)} protected files unchanged; Qdrant unchanged")


if __name__ == "__main__":
    asyncio.run(main())
