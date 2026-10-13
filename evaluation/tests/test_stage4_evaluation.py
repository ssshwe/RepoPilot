"""Metric/label integrity fixtures; synthetic unit inputs are not retrieval-quality results."""
from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services/api"))
sys.path.insert(0, str(ROOT))
from evaluation.retrieval.evaluate import EvaluationError
from evaluation.stage4.dataset import load_dataset
from evaluation.stage4.evaluate import check_holdout
from evaluation.stage4.metrics import case_metrics, evidence_coverage, percentile, summarize


def chunk(path="a.py", start=1, end=3, symbol="A.run"):
    return {"repository": "fixture", "path": path, "start_line": start, "end_line": end, "symbol": symbol}


def case():
    return {"category": "natural_language", "repository": "fixture", "expected_paths": ["a.py"],
            "expected_symbols": [{"path": "a.py", "symbol": "A.run"}],
            "evidence": [{"path": "a.py", "start_line": 1, "end_line": 3}]}


def test_partial_lines_are_not_a_complete_range():
    measured = evidence_coverage([chunk(end=2)], case()["evidence"])
    assert measured["line_coverage"] == 2 / 3 and measured["range_recall"] == 0 and not measured["all_evidence"]


def test_adjacent_chunks_jointly_cover_an_inclusive_range():
    measured = evidence_coverage([chunk(end=1), chunk(start=2)], case()["evidence"])
    assert measured["range_recall"] == measured["line_coverage"] == 1


def test_gap_in_ranges_remains_uncovered():
    measured = evidence_coverage([chunk(end=1), chunk(start=3)], case()["evidence"])
    assert measured["covered_lines"] == 2 and measured["covered_ranges"] == 0


def test_overlapping_annotations_and_duplicate_chunks_count_required_lines_once():
    evidence = [{"path": "a.py", "start_line": 1, "end_line": 3}, {"path": "a.py", "start_line": 2, "end_line": 4}]
    measured = evidence_coverage([chunk(), chunk()], evidence)
    assert measured["required_lines"] == 4 and measured["covered_lines"] == 3
    assert measured["required_ranges"] == 2 and measured["range_recall"] == .5


def test_file_deduplication_happens_after_the_chunk_cutoff():
    label = case() | {"expected_paths": ["a.py", "b.py"], "evidence": case()["evidence"] + [{"path": "b.py", "start_line": 1, "end_line": 3}]}
    measured = case_metrics(label, [chunk(), chunk(), chunk("b.py")], [])
    assert measured["at_k"]["1"]["file_recall"] == .5
    assert measured["at_k"]["3"]["file_recall"] == 1


def test_symbol_requires_its_path_and_unlabelled_symbols_are_na():
    measured = case_metrics(case(), [chunk("other.py")], [])
    assert measured["at_k"]["5"]["symbol_recall"] == 0
    measured = case_metrics(case() | {"expected_symbols": []}, [chunk()], [])
    assert measured["at_k"]["5"]["symbol_hit"] is None


def test_mrr_is_first_evidence_overlap_not_any_chunk_from_a_relevant_file():
    results = [chunk(start=10, end=12), chunk(end=1)]
    measured = case_metrics(case(), results, [])
    assert measured["at_k"]["1"]["evidence_mrr"] == 0
    assert measured["at_k"]["3"]["evidence_mrr"] == .5
    assert measured["at_k"]["3"]["range_recall"] == 0


def test_actual_context_can_have_less_coverage_than_topk():
    measured = case_metrics(case(), [chunk()], [chunk(end=1)])
    assert measured["at_k"]["5"]["all_evidence"]
    assert not measured["context"]["all_evidence"] and measured["context"]["line_coverage"] == 1 / 3


def test_negative_controls_are_na_and_do_not_inflate_positive_denominators():
    positive = {"metrics": case_metrics(case(), [], []), "retrieval": {"retrieval_latency_ms": 10}}
    negative_case = case() | {"category": "negative", "negative_kind": "empty_repository"}
    negative = {"metrics": case_metrics(negative_case, [], []), "retrieval": {"retrieval_latency_ms": 20}}
    summary = summarize([positive, negative])
    assert summary["at_k"]["5"]["positive_cases"] == 1 and summary["at_k"]["5"]["file_hit"] == 0
    assert summary["negative"]["empty_results"] == 1 and summary["latency_ms"]["p50"] == 15


def test_dense_irrelevant_results_fail_empty_negative_control_without_a_recall_score():
    negative = case() | {"category": "negative", "negative_kind": "missing_symbol"}
    measured = case_metrics(negative, [chunk()], [])
    assert measured["at_k"] is None and not measured["negative"]["empty_result"]


@pytest.mark.parametrize("samples,fraction,expected", [([10], .95, 10), ([0, 100], .95, 95), ([3, 1, 2], .5, 2)])
def test_latency_quantiles_have_explicit_small_sample_interpolation(samples, fraction, expected):
    assert percentile(samples, fraction) == expected


@pytest.mark.parametrize("samples", [[], [float("nan")], [-1], [True]])
def test_invalid_latency_samples_fail(samples):
    with pytest.raises(EvaluationError):
        percentile(samples, .5)


def test_holdout_requires_a_frozen_matching_configuration(tmp_path):
    with pytest.raises(EvaluationError):
        check_holdout("holdout", None, "dataset", {})
    path = tmp_path / "frozen.json"
    path.write_text(json.dumps({"dataset_sha256": "other", "configuration": {}}))
    with pytest.raises(EvaluationError):
        check_holdout("holdout", path, "dataset", {})
    check_holdout("dev", None, "dataset", {})


def test_frozen_dataset_has_72_grounded_cases_and_disjoint_family_splits():
    data, _ = load_dataset(ROOT / "evaluation/datasets/stage4_retrieval.json", ROOT / "workspaces")
    assert {split: sum(c["split"] == split for c in data["cases"]) for split in ("regression", "dev", "holdout")} == {"regression": 12, "dev": 40, "holdout": 20}


@pytest.mark.parametrize("mutation", ["group_leak", "query_leak", "invalid_symbol", "bad_snippet", "source_identity", "historical_change", "duplicate_range"])
def test_label_corruption_and_leakage_are_rejected(tmp_path, mutation):
    data = json.loads((ROOT / "evaluation/datasets/stage4_retrieval.json").read_bytes())
    dev = next(c for c in data["cases"] if c["split"] == "dev" and c["category"] != "negative")
    held = next(c for c in data["cases"] if c["split"] == "holdout")
    if mutation == "group_leak": held["group_id"] = dev["group_id"]
    elif mutation == "query_leak": held["query"] = dev["query"]
    elif mutation == "invalid_symbol": dev["expected_symbols"][0]["symbol"] = "Fake.missing"
    elif mutation == "bad_snippet": dev["evidence"][0]["snippet"] = "not in source"
    elif mutation == "source_identity": dev["source_hash"] = "0" * 64
    elif mutation == "historical_change": data["cases"][0]["query"] += " changed"
    else: dev["evidence"].append(deepcopy(dev["evidence"][0]))
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(EvaluationError):
        load_dataset(path, ROOT / "workspaces")
