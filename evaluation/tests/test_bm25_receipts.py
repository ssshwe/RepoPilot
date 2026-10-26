from copy import deepcopy
from pathlib import Path
import sys
from types import SimpleNamespace
import asyncio

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "services/api"), str(ROOT)]

from app.retrieval.bm25 import BM25Parameters
from app.retrieval.candidates import SourceMetadata
from evaluation.retrieval.evaluate import EvaluationError
from evaluation.stage4.evaluate_bm25 import evaluate, inputs, replay, verify_row
from evaluation.stage4.state import file_reference, read_json, write_new

REPORT = ROOT / "evaluation/reports/stage4_bm25_20261026_task6_final.json"


@pytest.fixture(scope="module")
def context():
    report = read_json(REPORT)
    _, dense, _, cases, store, images = inputs()
    return report, dense, cases, store, images


def test_all_52_comparisons_recompute_without_model_calls():
    assert replay(REPORT) == {"offline_replay_passed": True, "cases": 52, "holdout_queried": False,
                              "embedding_calls": 0, "generation_calls": 0}


@pytest.mark.parametrize("change", ["score", "explanation", "coordinate", "rank", "request", "context", "metrics", "delta"])
def test_modified_bm25_case_receipts_are_rejected(context, change):
    report, dense, cases, store, images = context
    case = next(c for c in cases if c["id"] == "wallet-symbol")
    original = next(r for r in report["cases"] if r["id"] == case["id"])
    row = deepcopy(original)
    baseline = next(r for r in dense["cases"] if r["id"] == case["id"])
    if change == "score": row["batch"]["candidates"][0]["channel_scores"]["bm25"] += 1
    elif change == "explanation": next(iter(row["explanations"].values()))[0]["tf"] += 1
    elif change == "coordinate": row["batch"]["candidates"][0]["source_metadata"]["content"] = "forged()\n" * 3
    elif change == "rank": row["batch"]["candidates"][0]["channel_ranks"]["bm25"] = 2
    elif change == "request": row["retrieval"]["repository"] = "other"
    elif change == "context": row["context"]["sources"] = []
    elif change == "metrics": row["metrics"]["at_k"]["5"]["range_recall"] = 0
    else: row["delta_at_5"]["range_recall"] = 0
    snapshot = store.load_ready(images[case["repository"]].scope).snapshot
    with pytest.raises((EvaluationError, ValueError)):
        verify_row(case, row, snapshot, BM25Parameters(), report["configuration"], baseline)


def test_original_52_receipts_kept_and_negatives_not_in_positive_denominator(context):
    report, _, _, _, _ = context
    assert len(report["case_execution"]["source_receipts"]) == 52
    assert report["summary"]["split"]["dev"]["at_k"]["5"]["positive_cases"] == 32
    assert report["summary"]["split"]["dev"]["negative"]["cases"] == 8
    assert report["summary"]["split"]["regression"]["cases"] == 12


def test_existing_bm25_report_refused_before_provider_work(tmp_path):
    path = tmp_path / "existing.json"
    path.write_bytes(b"keep")
    with pytest.raises(EvaluationError, match="overwritten"):
        asyncio.run(evaluate(SimpleNamespace(output=path)))
    assert path.read_bytes() == b"keep"


@pytest.mark.parametrize("change", ["implementation", "execution", "version"])
def test_report_association_and_execution_definition_cannot_be_relabelled(context, tmp_path, change):
    report = deepcopy(context[0])
    if change == "implementation": report["implementation"] = file_reference(ROOT / "services/api/app/retrieval/candidates.py")
    elif change == "execution": report["case_execution"]["source_receipts"].pop()
    else: report["parameters"]["version"] = "unknown"
    path = tmp_path / "changed.json"
    write_new(path, report)
    with pytest.raises(EvaluationError): replay(path)
