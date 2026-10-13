"""Offline fixtures for baseline integrity; not model-quality evidence."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services/api"))
sys.path.insert(0, str(ROOT))

from app.context import ContextBuilder
from app.core.config import Settings
from app.schemas.retrieval import RetrievalResult
from evaluation.retrieval.evaluate import EvaluationError
from evaluation.stage3.evaluate import measure_retrieval, summarize_retrieval
from evaluation.stage4.freeze_baseline import (
    check_report_links, classify_case, enriched_results, main, range_covered, verify_index_sources,
)
from evaluation.stage4.state import file_reference, safe_configuration, verify_unchanged, write_new


def result(start=10, end=12, **updates):
    return {"chunk_id": str(uuid4()), "rank": 1, "score": 0.5, "repository": "demo",
            "path": "a.java", "language": "java", "start_line": start, "end_line": end,
            "content": "    call();\n" * (end - start + 1), "symbol": "A.call", "node_type": "method_declaration",
            "parent_symbol": "A", "chunking_strategy": "ast", "chunking_version": "ast-v1"} | updates


def case():
    return {"id": "fixture", "group": "canonical", "expected_paths": ["a.java"],
            "expected_symbols": [{"path": "a.java", "symbol": "A.call"}],
            "evidence": [{"path": "a.java", "start_line": 10, "end_line": 12}]}


def row(retrieved, sources):
    return {"trace": {"retrieval": [{"results": retrieved}], "contexts": [{"sources": sources}]},
            "response": {"body": {"source_ids": ["S1"]}}}


def review(**updates):
    return {"outcome": "supported", "answer_complete": True, "response_sha256": "frozen-response",
            "reason": "offline fixture"} | updates


def test_safe_configuration_excludes_credentials_and_endpoint_urls():
    settings = Settings(_env_file=None, llm_api_key="secret-llm", embedding_api_key="secret-embedding",
                        qdrant_api_key="secret-qdrant", embedding_base_url="https://endpoint.invalid/v1?token=private")
    serialized = json.dumps(safe_configuration(settings))
    assert all(secret not in serialized for secret in ("secret-llm", "secret-embedding", "secret-qdrant", "private", "endpoint.invalid"))
    assert "embedding_base_url_sha256" in serialized


def test_report_binding_rejects_another_report_and_changed_report(tmp_path):
    path = tmp_path / "report.json"
    write_new(path, {"actual": True})
    acceptance = {"completed": True, "functional_acceptance": True, "reports": {"retrieval": file_reference(path)}}
    check_report_links(acceptance, {"retrieval": path})
    path.write_text('{"different": true}', encoding="utf-8")
    with pytest.raises(EvaluationError, match="does not bind"):
        check_report_links(acceptance, {"retrieval": path})


def test_acceptance_must_precede_baseline_publication():
    with pytest.raises(EvaluationError, match="acceptance must finish"):
        check_report_links({"completed": False}, {})


@pytest.mark.parametrize("kind", ["Source", "Configuration", "Qdrant index"])
def test_drift_is_a_failure_not_a_new_baseline(kind):
    with pytest.raises(EvaluationError, match="changed"):
        verify_unchanged({"before": 1}, {"after": 2}, kind)


def test_old_output_is_never_overwritten(tmp_path):
    path = tmp_path / "baseline.json"
    write_new(path, {"old": True})
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        write_new(path, {"new": True})
    assert path.read_bytes() == before
    assert main(["--output", str(path)]) == 1
    assert path.read_bytes() == before


def test_union_covers_a_complete_range_without_double_counting_overlap():
    first, second = result(10, 11), result(11, 12, rank=2)
    label = case()
    assert range_covered([first, second], label["evidence"][0])
    assert not range_covered([first], label["evidence"][0])
    assert measure_retrieval(label, [first, second], {"sources": [first]})["context_annotated_lines"]["recall"] == 0


def test_no_symbol_label_is_excluded_from_the_symbol_denominator():
    chunk = result()
    labelled = measure_retrieval(case(), [chunk], {"sources": [chunk]})
    unlabelled = measure_retrieval(case() | {"expected_symbols": []}, [chunk], {"sources": [chunk]})
    rows = [{"group": "canonical", "retrieval_metrics": deepcopy(labelled)} for _ in range(7)]
    rows.extend({"group": "canonical", "retrieval_metrics": deepcopy(unlabelled)} for _ in range(5))
    summary = summarize_retrieval(rows)
    assert summary["symbols"]["labelled_queries"] == 7
    assert summary["file"]["at_k"]["5"]["total"] == 12


def test_symbol_matching_requires_the_exact_path():
    assert measure_retrieval(case(), [result(path="b.java")], {"sources": []})["symbols"]["5"]["recall"] == 0


def test_truncated_context_uses_actual_source_coordinates():
    chunk = result(10, 30)
    full = ContextBuilder().build("demo", [RetrievalResult.model_validate(chunk)])
    budget = len(ContextBuilder.format_source(full.sources[0])) - 50
    truncated = ContextBuilder(max_context_characters=budget).build("demo", [RetrievalResult.model_validate(chunk)])
    source = truncated.sources[0]
    assert source.end_line < source.original_end_line == 30
    assert source.budget_truncated and len(source.content.splitlines()) == source.end_line - source.start_line + 1


@pytest.mark.parametrize("mutation", ["content", "path", "repository", "chunk_id"])
def test_results_must_belong_to_the_identical_frozen_chunk(mutation):
    chunk = result()
    inventory = {chunk["chunk_id"]: {k: v for k, v in chunk.items() if k not in {"rank", "score"}}}
    changed = chunk | {mutation: "foreign"}
    with pytest.raises(EvaluationError):
        enriched_results([changed], inventory)


def test_source_change_and_stale_coordinates_are_rejected(tmp_path):
    (tmp_path / "a.java").write_bytes(b"old\n")
    chunk = result(1, 1, content="old\n")
    inventory = {chunk["chunk_id"]: chunk}
    assert verify_index_sources(inventory, tmp_path)["a.java"] == hashlib.sha256(b"old\n").hexdigest()
    (tmp_path / "a.java").write_bytes(b"new\n")
    with pytest.raises(EvaluationError, match="coordinates differ"):
        verify_index_sources(inventory, tmp_path)


def test_top20_diagnostic_does_not_replace_original_top5():
    target = result()
    original = [result(path="other.java")]
    snapshot = deepcopy(original)
    diagnosis = {"response": {"results": [target]}}
    classifications = classify_case(case(), row(original, original), review(), {target["chunk_id"]: target}, diagnosis)
    assert classifications["failures"][0]["category"] == "ranking_low"
    assert original == snapshot
    assert measure_retrieval(case(), original, {"sources": original})["annotated_lines"]["5"]["recall"] == 0


def test_index_gap_is_not_reported_as_ranking_failure():
    failure = classify_case(case(), row([], []), review(), {}, {"response": {"results": []}})["failures"][0]
    assert failure["category"] == "index_or_recall_missing" and not failure["indexed_range_complete"]


def test_context_loss_and_wrong_generation_can_both_be_recorded():
    target, partial = result(), result(10, 11, source_id="S1")
    findings = classify_case(case(), row([target], [partial]), review(outcome="unsupported", answer_complete=False), {target["chunk_id"]: target})
    assert [f["category"] for f in findings["failures"]] == ["context_missing", "generation_error"]


def test_empty_negative_control_is_not_a_bad_generation():
    label = case() | {"expected_paths": [], "evidence": [], "expected_symbols": []}
    findings = classify_case(label, row([], []), review(outcome="abstention"), {})
    assert findings["failures"] == []
