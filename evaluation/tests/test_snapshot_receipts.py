"""Offline Task 5 lifecycle receipt associations and completeness."""
from copy import deepcopy
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "services/api"), str(ROOT)]

from evaluation.retrieval.evaluate import EvaluationError
from evaluation.stage4.state import read_json, write_new
from evaluation.stage4.verify_snapshots import replay

REPORT = ROOT / "evaluation/reports/stage4_snapshots_20261026_task5.json"


def test_real_lifecycle_receipts_replay_offline():
    assert replay(REPORT) == {"offline_replay_passed": True, "frozen_chunks": 129, "phases": 14, "restored_scopes": 7, "generation_calls": 0}


@pytest.mark.parametrize("change", ["database", "implementation", "scope", "phase_drop", "phase_reorder", "frozen_name", "partial_state", "target", "final_revision", "calls"])
def test_forged_lifecycle_associations_rejected(tmp_path, change):
    report = deepcopy(read_json(REPORT))
    if change == "database": report["database"]["sha256"] = "0" * 64
    elif change == "implementation": next(iter(report["implementations"].values()))["sha256"] = "0" * 64
    elif change == "scope": report["phases"][0]["scope"]["repository"] = "other"
    elif change == "phase_drop": report["phases"].pop()
    elif change == "phase_reorder": report["phases"][0], report["phases"][1] = report["phases"][1], report["phases"][0]
    elif change == "frozen_name": report["phases"][0]["name"] = "unverified-alternative"
    elif change == "partial_state": next(p for p in report["phases"] if p["name"] == "failed-rebuild-blocked")["state"]["state"] = "ready"
    elif change == "target": report["phases"][0]["state"]["target_sources"] = "sha256:" + "0" * 64
    elif change == "final_revision": report["final_revisions"][0] = "sha256:" + "0" * 64
    else: report["calls"]["real_embedding_calls"] = 1
    path = tmp_path / "changed.json"
    write_new(path, report)
    with pytest.raises(EvaluationError): replay(path)
