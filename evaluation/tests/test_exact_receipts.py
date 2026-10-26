"""Offline proof integrity for Task 7 scopes, reasons and native query evidence."""
import asyncio
from copy import deepcopy
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "services/api"), str(ROOT)]

from app.retrieval.scope import SearchFilters
from app.retrieval.snapshot_models import IndexedRepository, SnapshotScope
from evaluation.retrieval.evaluate import EvaluationError
from evaluation.stage4.state import file_reference, read_json, write_new
from evaluation.stage4.verify_exact import PROBES, evaluate, inputs, replay, validate_live_row, validate_probe

REPORT = ROOT / "evaluation/reports/stage4_exact_20261026_task7.json"


@pytest.fixture(scope="module")
def context():
    report = read_json(REPORT)
    _, _, _, _, store, images = inputs()
    return report, store, images


def test_all_exact_filters_and_52_bm25_queries_recompute_offline():
    assert asyncio.run(replay(REPORT)) == {
        "offline_replay_passed": True, "exact_probes": 24, "bm25_compatibility_cases": 52,
        "live_filter_scenarios": 5, "generation_calls": 0}


@pytest.mark.parametrize("change", ["reason", "score", "scope", "source", "eligibility", "missing_result"])
def test_modified_exact_probe_is_rejected(context, change):
    report, store, images = context
    definition = next(p for p in PROBES if p["id"] == "formal-filter")
    row = deepcopy(next(r for r in report["probes"] if r["id"] == definition["id"]))
    if change == "reason": next(iter(row["exact"]["reasons"].values()))["start_line"] += 1
    elif change == "score": row["exact"]["candidates"][0]["channel_scores"]["exact"] += 1
    elif change == "scope": row["exact"]["filters"]["path_prefix"] = "backend/legacy/"
    elif change == "source": row["exact"]["candidates"][0]["source_metadata"]["path"] = "backend/wallet/forged.java"
    elif change == "eligibility": row["eligible_ids"].pop()
    else:
        row["exact"]["candidates"].clear()
        row["exact"]["reasons"].clear()
    snapshot = store.load_ready(images[definition["repository"]].scope).snapshot
    with pytest.raises((ValueError, EvaluationError)):
        validate_probe(definition, row, snapshot)


@pytest.fixture(scope="module")
def live_context(context):
    live = read_json(ROOT / context[0]["live"]["path"])
    archive = read_json(ROOT / live["archive"]["path"])
    selected = next(r for r in archive["cases"] if r["id"] == live["archived_case_id"])
    state = read_json(ROOT / live["index"]["path"])
    scope = SnapshotScope(**live["scope"])
    image = IndexedRepository.from_records(scope, {
        "dimension": 1024, "distance": "Cosine", "embedding": state["config"]["metadata"]["repopilot_index"],
        "chunking": state["config"]["metadata"]["repopilot_chunking"][scope.repository]},
        [p for p in state["points"] if p["payload"]["repository"] == scope.repository])
    return live, image, selected, {str(p["id"]): p for p in state["points"]}


@pytest.mark.parametrize("change", ["native_filter", "native_limit", "native_result", "dense_score", "bm25_score", "exact_set"])
def test_native_pre_limit_and_all_three_live_channels_are_bound(live_context, change):
    live, image, selected, points = live_context
    row = deepcopy(next(r for r in live["rows"] if r["id"] == "formal"))
    if change == "native_filter": row["native_dense_query"][0]["filter"]["must"].pop()
    elif change == "native_limit": row["native_dense_query"][0]["limit"] = 60
    elif change == "native_result": row["native_dense_query"][0]["returned_points"].pop()
    elif change == "dense_score": row["dense"]["candidates"][0]["channel_scores"]["dense"] += .1
    elif change == "bm25_score": row["bm25"]["candidates"][0]["channel_scores"]["bm25"] += .1
    else:
        row["exact"]["candidates"].clear()
        row["exact"]["reasons"].clear()
    with pytest.raises((ValueError, EvaluationError)):
        validate_live_row(row, SearchFilters(**row["filters"]), image, selected["query_vector"], points, selected["batch"]["query"])


@pytest.mark.parametrize("change", ["association", "implementation", "version"])
def test_report_identity_and_associations_cannot_be_relabelled(context, tmp_path, change):
    report = deepcopy(context[0])
    if change == "association": report["bm25_reference"] = file_reference(REPORT)
    elif change == "implementation": report["implementations"]["services/api/app/retrieval/exact.py"] = file_reference(ROOT / "services/api/app/retrieval/bm25.py")
    else: report["scope_version"] = "unknown"
    path = tmp_path / "changed.json"
    write_new(path, report)
    with pytest.raises(EvaluationError): asyncio.run(replay(path))


def test_existing_exact_output_is_refused_before_provider_work(tmp_path):
    path = tmp_path / "existing.json"
    path.write_bytes(b"keep")
    with pytest.raises(EvaluationError, match="overwritten"):
        asyncio.run(evaluate(SimpleNamespace(output=path)))
    assert path.read_bytes() == b"keep"
