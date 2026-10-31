import asyncio
from copy import deepcopy
from pathlib import Path
import socket
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/"services/api"), str(ROOT)]
from evaluation.retrieval.evaluate import EvaluationError
from evaluation.stage4 import evaluate_rerank as module
from evaluation.stage4.state import read_json, write_new

REPORT = ROOT/"evaluation/reports/stage4_rerank_20261031_task10_final.json"


@pytest.fixture(scope="module")
def context():
    report = read_json(REPORT)
    inputs = module.checked_inputs()
    _, _, _, cases, _, _ = inputs
    original = read_json(module.HYBRID)
    return report, cases, original, inputs


def test_complete_receipts_replay_without_model_runtime_or_network(monkeypatch):
    attempts = []
    def deny(*args, **kwargs):
        attempts.append(True)
        raise AssertionError("Model-free replay must not use network")
    async def run():
        # Windows creates a loopback socketpair for the event loop itself.
        # Start the loop first, then forbid every connection made by replay.
        with monkeypatch.context() as patch:
            patch.setattr(socket.socket, "connect", deny)
            patch.setattr(socket, "create_connection", deny)
            return await module.replay(REPORT)
    result = asyncio.run(run())
    assert result["offline_replay_passed"] and result["cases"] == 52 and result["performance_probes"] == 9
    assert result["new_embedding_calls"] == result["generation_calls"] == 0 and not attempts


@pytest.mark.parametrize("change", ["association", "coordinate", "source", "logit", "scope", "query", "policy", "context", "metric", "comparison", "manifest", "original_pool", "diagnosis"])
def test_case_is_bound_to_original_question_pool_model_and_actual_context(context, change):
    report, cases, original, _ = context
    ordinal = next(i for i, r in enumerate(report["cases"]) if r["id"] == "wallet-symbol")
    row = deepcopy(report["cases"][ordinal])
    assert row["rerank"]["status"] == "reranked"
    if change == "association": row["id"] = "other-case"
    if change == "coordinate": row["rerank"]["ordered"][0]["source_metadata"]["start_line"] += 1
    if change == "source": row["rerank"]["ordered"][0]["source_metadata"]["content"] += "changed"
    if change == "logit": row["rerank"]["reply"]["scores"][0] += .25
    if change == "scope": row["rerank"]["fused"]["scope_fingerprint"] = "sha256:"+"b"*64
    if change == "query": row["rerank"]["reply"]["query_sha256"] = "b"*64
    if change == "policy": row["rerank"]["policy"]["batch_size"] = 8
    if change == "context": row["measured"]["context"]["sources"] = []
    if change == "metric": row["measured"]["metrics"]["at_k"]["5"]["range_recall"] = -1
    if change == "comparison": row["comparison"]["positive"] = False
    if change == "manifest": row["rerank"]["reply"]["manifest_sha256"] = "b"*64
    if change == "original_pool": row["rerank"]["fused"]["candidates"] = []
    if change == "diagnosis": row["evidence_diagnostics"] = []
    with pytest.raises((ValueError, TypeError, EvaluationError)):
        module.check_row(cases[ordinal], row, original["cases"][ordinal], report["configuration"], report["model_manifest"]["sha256"])


@pytest.mark.parametrize("change", ["reference", "implementation", "policy", "holdout", "default", "inventory", "summary", "performance", "calls", "sweep"])
def test_report_association_denominators_cost_and_probe_scope_cannot_change(context, tmp_path, monkeypatch, change):
    report = deepcopy(context[0])
    if change == "reference": report["hybrid_reference"]["sha256"] = "b"*64
    if change == "implementation": report["implementations"] = {}
    if change == "policy": report["policy"]["candidate_limit"] = 60
    if change == "holdout": report["holdout_queried"] = True
    if change == "default": report["default_enabled"] = True
    if change == "inventory": report["cases"].pop()
    if change == "summary": report["summary"] = {}
    if change == "performance": report["performance"]["pairs"] += 1
    if change == "calls": report["calls"]["completed_forward_calls"] = 0
    if change == "sweep": report["sweep"].pop()
    output = tmp_path/"changed.json"
    write_new(output, report)
    original_artifacts = module.artifact_directory(REPORT)
    monkeypatch.setattr(module, "artifact_directory", lambda _: original_artifacts)
    # The full ancestry is independently replayed by the first test. These
    # focused mutations exercise Task 10 bindings without repeating that work.
    async def checked_ancestry(_):
        return None
    monkeypatch.setattr(module, "replay_hybrid", checked_ancestry)
    monkeypatch.setattr(module, "checked_inputs", lambda: context[3])
    with pytest.raises((ValueError, EvaluationError)):
        asyncio.run(module.replay(output))


@pytest.mark.parametrize("kind", ["report", "artifacts"])
def test_existing_outputs_are_rejected_before_any_models_or_reads(tmp_path, monkeypatch, kind):
    path = tmp_path/"protected.json"
    if kind == "report":
        path.write_bytes(b"preserve")
    else:
        module.artifact_directory(path).mkdir()
    calls = []
    async def forbidden(*args):
        calls.append(True)
        raise AssertionError("Must reject existing output first")
    monkeypatch.setattr(module, "replay_hybrid", forbidden)
    with pytest.raises(EvaluationError, match="overwritten"):
        asyncio.run(module.evaluate(path, tmp_path/"no-python", tmp_path/"no-model"))
    assert not calls
    if kind == "report":
        assert path.read_bytes() == b"preserve"


def test_performance_probes_and_faults_do_not_change_formal_denominator(context):
    report, cases, _, _ = context
    assert len(report["cases"]) == len(cases) == 52
    assert len(report["sweep"]) == 9
    assert len([c for c in cases if c["split"] == "regression"]) == 12
    assert len([c for c in cases if c["split"] == "dev" and c["category"] != "negative"]) == 32
    assert len([c for c in cases if c["category"] == "negative"]) == 8
    assert report["calls"]["interrupted_requests"] == 1
    assert report["calls"]["interrupted_forward_calls"].startswith("unknown")
