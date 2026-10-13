"""Replay the new Task 13 receipts, check preservation and publish its functional acceptance."""
import asyncio
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services/api"))
sys.path.insert(0, str(ROOT))
from app.core.config import get_settings
from app.retrieval.vector_store import create_qdrant_client
from evaluation.retrieval.evaluate import require
from evaluation.stage3.evaluate import load_grounding_dataset
from evaluation.stage4.freeze_baseline import index_inventory, replay_inputs, verify_index_sources
from evaluation.stage4.state import (
    file_reference, full_snapshot, legacy_snapshot, read_json, runtime_identity,
    safe_configuration, verify_unchanged, write_new,
)

OUT = ROOT / "output/stage3-task13"
PATHS = {"retrieval": ROOT / "evaluation/reports/stage3_retrieval_20261013_task13.json",
         "grounding": ROOT / "evaluation/reports/stage3_grounding_20261013_task13_raw.json",
         "review": ROOT / "evaluation/reports/stage3_grounding_20261013_task13_review.json",
         "summary": ROOT / "evaluation/reports/stage3_grounding_20261013_task13_summary.json"}


async def main():
    before = read_json(OUT / "qdrant-before.json")
    settings = get_settings()
    config = safe_configuration(settings)
    verify_unchanged(read_json(OUT / "settings-before.json"), config, "Protected configuration")
    client = create_qdrant_client(settings)
    try:
        after = await full_snapshot(client)
    finally:
        await client.close()
    verify_unchanged(before, after, "All existing Qdrant collections")
    data, digest, base = load_grounding_dataset(ROOT / "evaluation/datasets/stage3_grounding.json", ROOT / "workspaces")
    retrieval, grounding = read_json(PATHS["retrieval"]), read_json(PATHS["grounding"])
    require(grounding["dataset_sha256"] == digest and grounding["qdrant_before"] == legacy_snapshot(before)
            and grounding["qdrant_unchanged"], "Grounding source/index binding mismatch")
    inventory = index_inventory(before, config, data["repository"])
    require(verify_index_sources(inventory, base.repository_root) == data["source_sha256"], "Indexed source set mismatch")
    _, reviewed = replay_inputs(data, base, retrieval, grounding, (PATHS["grounding"], PATHS["review"]), inventory)
    require(reviewed == read_json(PATHS["summary"]), "New review summary did not independently replay")
    for index, row in enumerate(grounding["cases"], 1):
        path = PATHS["grounding"].parent / f"{PATHS['grounding'].stem}_cases" / f"{index:02}-{row['id']}.json"
        require(read_json(path) == row, "Actual grounding receipt differs from report")
    # Stage 2 CLI preserves projected per-query rows in its report, not native HTTP bodies.
    receipt_dir = PATHS["retrieval"].parent / f"{PATHS['retrieval'].stem}_cases"
    for index, row in enumerate(retrieval["queries"], 1):
        path = receipt_dir / f"{index:02}-{row['id']}.json"
        receipt = {"kind": "projected query row exported from real HTTP retrieval report; not a native full HTTP body",
                   "report": file_reference(PATHS["retrieval"]), "case": row}
        if path.exists():
            require(read_json(path) == receipt, "Existing projected retrieval receipt differs")
        else:
            write_new(path, receipt)
    protected = read_json(OUT / "workspace-before.json")
    allowed = {"README.md", "evaluation/README.md", ".env.example", "apps/web/src/stores/retrieval.ts"}
    changed = []
    for name, digest in protected.items():
        path = ROOT / name
        require(path.is_file(), f"Protected file missing: {name}")
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            require(name in allowed, f"Unexpected protected file change: {name}")
            changed.append(name)
    logs = {name: (OUT / filename).read_text(encoding="utf-8") for name, filename in (
        ("backend", "backend-tests.log"), ("evaluation", "evaluation-tests.log"),
        ("frontend", "frontend-tests.log"), ("build", "frontend-build.log"))}
    require("1006 passed, 1 skipped" in logs["backend"], "Full backend/integration check not passed")
    require("81 passed" in logs["evaluation"] and "fail 0" in logs["frontend"]
            and "pass 46" in logs["frontend"] and "built in" in logs["build"], "Evaluation/frontend/build check not passed")
    ui_path = ROOT / "output/playwright/stage3-task13-verification.json"
    ui = read_json(ui_path)
    require(ui["passed"] is True and all(ui["checks"].values()), "Browser verification not complete")
    for reference in ui["artifacts"].values():
        require(file_reference(ROOT / reference["path"]) == reference, "Browser artifact changed")
    write_new(OUT / "qdrant-after.json", after)
    result = {"task": "Stage 3 / Task 13", "completed": True, "functional_acceptance": True,
        "answer_quality_accepted": False, "run_date": "2026-10-13", "created_at": datetime.now(timezone.utc).isoformat(),
        "session_started_at": read_json(OUT / "session.json")["started_at"],
        "configuration": config, "runtime": runtime_identity(), "reports": {k: file_reference(v) for k, v in PATHS.items()},
        "qdrant_snapshot": file_reference(OUT / "qdrant-before.json"), "qdrant_unchanged": True,
        "preservation": {"protected_files": len(protected), "expected_changed_files": sorted(changed),
                         "other_existing_files_unchanged": len(protected) - len(changed), "env_unchanged": True},
        "tests": {"backend_passed": 1006, "backend_skipped": 1, "real_integration_cases": 5,
                  "evaluation_passed": 81, "baseline_tests_included": 20, "frontend_passed": 46, "frontend_build_passed": True},
        "functional_fix": "Retrieval Debug ignores cancelled/timed-out stale responses and preserves newer request state; five regression tests added",
        "test_receipts": {k: file_reference(OUT / filename) for k, filename in (
            ("backend", "backend-tests.log"), ("evaluation", "evaluation-tests.log"),
            ("frontend", "frontend-tests.log"), ("build", "frontend-build.log"))},
        "browser_verification": file_reference(ui_path), "real_grounding_requests": 16,
        "native_generation_http_calls": grounding["native_generation_http_calls"],
        "retrieval_summary": grounding["retrieval_summary"], "review_summary": reviewed["review_summary"],
        "reviewer": reviewed["reviewer"], "offline_replay_passed": True,
        "next_task": "Stage 4 / Task 1 Dense baseline freeze only"}
    write_new(ROOT / "docs/stage-3-task-13-verification.json", result)
    print(f"Task 13 accepted: {len(protected) - len(changed)}/{len(protected)} protected files unchanged; Qdrant unchanged")


if __name__ == "__main__":
    asyncio.run(main())
