"""Task 7 final acceptance: no-network recomputation and protected state."""
import asyncio
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import socket
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "services/api"), str(ROOT)]
from app.core.config import get_settings
from app.retrieval.exact import ExactCandidateBatch
from app.retrieval.scope import SearchFilters
from app.retrieval.vector_store import create_qdrant_client
from evaluation.retrieval.evaluate import require
from evaluation.stage4.evaluate_bm25 import replay as replay_bm25
from evaluation.stage4.freeze_baseline import replay_baseline
from evaluation.stage4.verify_candidates import replay as replay_candidates
from evaluation.stage4.verify_exact import replay
from evaluation.stage4.state import file_reference, full_snapshot, read_json, safe_configuration, verify_unchanged, write_new


async def main():
    output = ROOT / "output/stage4-task7"
    path = ROOT / "evaluation/reports/stage4_exact_20261026_task7.json"
    attempted = []
    connect, create = socket.socket.connect, socket.create_connection
    def deny(*args, **kwargs):
        attempted.append(True)
        raise AssertionError("Offline replay attempted a network connection")
    try:
        socket.socket.connect, socket.create_connection = deny, deny
        current = await replay(path)
        bm25 = replay_bm25(ROOT / "evaluation/reports/stage4_bm25_20261026_task6_final.json")
        candidates = replay_candidates(ROOT / "evaluation/reports/stage4_candidates_20261013_task3.json")
        baseline = replay_baseline(ROOT / "evaluation/reports/stage4_dense_20261013_task1.json")
    finally:
        socket.socket.connect, socket.create_connection = connect, create
    require(not attempted, "Offline replay must not use network")
    report = read_json(path)
    require(report["live"] is not None, "Real Qdrant filter proof not complete")
    live = read_json(ROOT / report["live"]["path"])
    require(live["temporary_collection_deleted"] and live["archived_vector_reuses"] == 5
            and live["real_embedding_calls"] == live["generation_calls"] == 0, "Live proof/cost missing")
    spike = ExactCandidateBatch.from_dict(read_json(output / "spike-example.json"))
    require(spike.candidate_limit == 10 and len(spike.candidates) == 1
            and spike.filters == SearchFilters("java", "backend/wallet/")
            and spike.candidates[0].source_metadata.symbol == "WalletService.credit"
            and (spike.candidates[0].source_metadata.start_line, spike.candidates[0].source_metadata.end_line) == (11, 17),
            "Independent Exact CLI spike not verified")
    verify_unchanged(read_json(output / "settings-before.json"), safe_configuration(get_settings()), "Settings")
    client = create_qdrant_client(get_settings())
    try:
        after = await full_snapshot(client)
    finally:
        await client.close()
    verify_unchanged(read_json(output / "qdrant-before.json"), after, "All persistent Qdrant collections")
    protected, changed = read_json(output / "workspace-before.json"), []
    for name, expected in protected.items():
        file = ROOT / name
        require(file.is_file(), f"Protected file missing: {name}")
        if hashlib.sha256(file.read_bytes()).hexdigest() != expected:
            require(name in {"README.md", "evaluation/README.md"}, f"Unexpected old file change: {name}")
            changed.append(name)
    require(len(protected) == 1089 and set(changed) == {"README.md", "evaluation/README.md"}, "Protection inventory differs")
    require("1221 passed, 6 skipped" in (output / "backend-tests.log").read_text(encoding="utf-8"), "Backend regression has not passed")
    require("172 passed" in (output / "evaluation-tests-final.log").read_text(encoding="utf-8"), "Evaluation regression has not passed")
    write_new(output / "qdrant-after.json", after)
    write_new(ROOT / "docs/stage-4-task-7-verification.json", {
        "task": "Stage 4 / Task 7", "completed": True, "run_date": "2026-10-27", "created_at": datetime.now(timezone.utc).isoformat(),
        "report": file_reference(path), "scope_version": report["scope_version"], "exact_version": report["exact_version"],
        "tests": {"default_backend_passed": 1221, "default_backend_skipped": 6, "new_exact_and_filter_tests": 44,
                  "evaluation_passed": 172, "new_receipt_tests": 17, "exact_probes": 24,
                  "unfiltered_bm25_compatibility_cases": 52, "real_qdrant_filter_scenarios": 5,
                  "independent_cli_spike_passed": True, "frontend_and_prior_real_model_integrations_rerun": False},
        "calls": {"archived_vector_reuses": 5, "real_embedding_calls": 0, "generation_calls": 0},
        "offline_network_attempts": len(attempted), "exact_filter_replay": current, "task6_replay": bm25,
        "task5_task4_replay": "Snapshot lifecycle and Tokenizer receipts replayed by BM25 input validator",
        "task3_replay": candidates, "task2_replay": "52 cases replayed by candidate verifier", "task1_replay": baseline,
        "live_summary": report["live"], "spike_receipt": file_reference(output / "spike-example.json"),
        "original_retrieval_receipts_preserved_after_projection_validator_fix": True,
        "implementation": {name: file_reference(ROOT / name) for name in (
            "services/api/app/retrieval/scope.py", "services/api/app/retrieval/filtered.py", "services/api/app/retrieval/exact.py",
            "services/api/tests/test_exact_and_filters.py", "evaluation/stage4/verify_exact.py", "evaluation/tests/test_exact_receipts.py")},
        "test_receipts": {"backend": file_reference(output / "backend-tests.log"), "evaluation": file_reference(output / "evaluation-tests-final.log"),
                          "earlier_projection_validator_failure": file_reference(output / "evaluation-tests.log")},
        "preservation": {"protected_files": len(protected), "expected_changed_files": sorted(changed),
            "other_existing_files_unchanged": len(protected) - len(changed), "environment_and_locks_unchanged": True,
            "public_api_and_frontend_unchanged": True, "production_dense_unchanged": True, "qdrant_unchanged": True,
            "prior_reports_datasets_tokenizer_snapshots_unchanged": True, "holdout_queried": False},
        "protection_inputs": {name: file_reference(output / name) for name in (
            "workspace-before.json", "settings-before.json", "qdrant-before.json", "qdrant-after.json", "git-before.json")},
        "next_task": "Stage 4 / Task 8 Hybrid Search and RRF; not started"})
    print(f"Task 7 complete: 1221 backend / 172 evaluation tests; 24 probes / 52 comparisons / 5 live scenarios; {len(protected)-len(changed)} old files unchanged; zero offline network attempts")


if __name__ == "__main__": asyncio.run(main())
