"""Tokenizer receipt integrity against saved code; all tests are offline."""
from copy import deepcopy
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services/api"))
sys.path.insert(0, str(ROOT))

from evaluation.retrieval.evaluate import EvaluationError
from evaluation.stage4.state import file_reference, read_json, write_new
from evaluation.stage4.verify_tokenizer import build, checked_reference, replay


@pytest.fixture(scope="module")
def receipt(tmp_path_factory):
    path = tmp_path_factory.mktemp("tokenizer-receipts") / "tokens.json"
    build(ROOT / "evaluation/reports/stage4_candidates_20261013_task3.json", path)
    return path, read_json(path)


def test_frozen_chunks_and_dev_queries_replay_without_holdout(receipt):
    path, report = receipt
    result = replay(path)
    assert result == {"offline_replay_passed": True, "chunks": 129, "queries": 52, "examples": 21,
                      "holdout_queried": False, "generation_calls": 0}
    assert report["summary"]["query_splits"] == {"dev": 40, "regression": 12}
    assert all(row["split"] != "holdout" for row in report["queries"])


@pytest.mark.parametrize("kind", ["reference", "index", "dataset", "implementation", "version", "unicode", "chunk", "frequency", "dropped_query", "calls"])
def test_changed_receipts_or_associations_are_rejected(receipt, tmp_path, kind):
    _, original = receipt
    report = deepcopy(original)
    if kind == "reference":
        report["reference_report"]["sha256"] = "0" * 64
    elif kind == "index":
        report["index_snapshot"]["sha256"] = "0" * 64
    elif kind == "dataset":
        report["dataset"]["sha256"] = "0" * 64
    elif kind == "implementation":
        report["implementation"] = file_reference(ROOT / "services/api/app/retrieval/candidates.py")
    elif kind == "version":
        report["tokenizer"]["version"] = "unknown"
    elif kind == "unicode":
        report["tokenizer"]["unicode_version"] = "unknown"
    elif kind == "chunk":
        report["chunks"][0]["repository"] = "other"
    elif kind == "frequency":
        report["queries"][0]["term_frequencies"] = {}
    elif kind == "dropped_query":
        report["queries"].pop()
    else:
        report["calls"]["embedding_provider_calls"] = 1
    path = tmp_path / "changed.json"
    write_new(path, report)
    with pytest.raises(EvaluationError):
        replay(path)


def test_input_drift_is_rejected(tmp_path):
    path = tmp_path / "input.json"
    write_new(path, {"before": True})
    reference = file_reference(path)
    path.write_bytes(b'{"after": true}')
    with pytest.raises(EvaluationError, match="fingerprint"):
        checked_reference(reference)


def test_existing_report_is_never_overwritten(tmp_path):
    path = tmp_path / "keep.json"
    path.write_bytes(b"original")
    with pytest.raises(EvaluationError, match="overwritten"):
        build(tmp_path / "missing.json", path)
    assert path.read_bytes() == b"original"
