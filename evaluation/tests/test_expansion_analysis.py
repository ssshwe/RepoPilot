from copy import deepcopy
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/"services/api"),str(ROOT)]
from evaluation.retrieval.evaluate import EvaluationError
from evaluation.stage4.analyze_expansion import analyze,main
from evaluation.stage4.state import read_json,write_new

REPORT = ROOT/"evaluation/reports/stage4_expansion_20261101_task11.json"
ANALYSIS = ROOT/"evaluation/reports/stage4_expansion_20261101_task11_analysis_final.json"


def test_actual_pipeline_cost_and_rank_loss_recompute_without_adding_probe_denominators():
    saved = read_json(ANALYSIS)
    assert analyze(REPORT) == saved
    assert saved["formal_denominator"] == len(saved["formal_costs"]) == 52
    assert saved["formal_new_embedding_calls"] == 82
    assert saved["formal_pipeline_wall_ms"]["p50"] > 0
    assert saved["calls"]["new_embedding_calls"] == 92
    assert saved["calls"]["llm_http_attempts"] == 4
    assert len(saved["llm_probes"]) == 4 and not saved["changed_cases"]
    loss = next(r for r in saved["annotated_evidence_rank_changes"] if r["id"] == "inventory-chain")
    assert loss["before_fused_rank"] == 2 and loss["after_fused_rank"] == 3
    assert loss["required_range"]["start_line"] == 7 and loss["required_range"]["end_line"] == 9


def test_mutated_pipeline_cost_sidecar_is_rejected(tmp_path,monkeypatch):
    value = deepcopy(read_json(ANALYSIS))
    value["formal_pipeline_wall_ms"]["p50"] = 0
    path = tmp_path/"changed.json"
    write_new(path,value)
    monkeypatch.setattr(sys,"argv",["analyze_expansion",str(REPORT),"--output",str(path),"--verify-only"])
    with pytest.raises(EvaluationError,match="differs"):
        main()
