"""Offline evaluator boundary fixtures; these inputs are not real model results."""
from copy import deepcopy

import pytest

from evaluation.retrieval.evaluate import EvaluationError
from evaluation.stage3.evaluate import ROOT, fingerprint, load_grounding_dataset, measure_retrieval, summarize_retrieval
from evaluation.stage3.review import summarize_reviews


def case():
    return {"expected_paths": ["a.java"], "evidence": [{"path": "a.java", "start_line": 10, "end_line": 12}],
            "expected_symbols": [{"path": "a.java", "symbol": "A.do"}]}


def chunk(start=10, end=12, symbol="A.do", path="a.java"):
    return {"path": path, "start_line": start, "end_line": end, "symbol": symbol}


def test_file_hit_does_not_imply_method_or_line_coverage():
    metrics = measure_retrieval(case(), [chunk(1, 3, "A")], {"sources": [chunk(1, 3, "A")]})
    assert metrics["file"]["5"]["hit"]
    assert metrics["annotated_lines"]["5"]["recall"] == 0
    assert metrics["symbols"]["5"]["recall"] == 0
    assert metrics["context_annotated_lines"]["recall"] == 0


def test_retrieved_full_method_can_be_truncated_out_of_context():
    metrics = measure_retrieval(case(), [chunk()], {"sources": [chunk(end=11)]})
    assert metrics["annotated_lines"]["5"]["recall"] == 1
    assert metrics["context_annotated_lines"]["recall"] == 0


def test_symbol_names_are_scoped_by_exact_path():
    metrics = measure_retrieval(case(), [chunk(path="other.java")], {"sources": []})
    assert metrics["symbols"]["5"]["recall"] == 0


def test_k_is_a_chunk_cutoff_before_deduplication():
    entries = [chunk(1, 3, "A"), chunk(4, 5, "A"), chunk(6, 7, "A"), chunk()]
    metrics = measure_retrieval(case(), entries, {"sources": entries})
    assert metrics["symbols"]["3"]["recall"] == 0
    assert metrics["symbols"]["5"]["recall"] == 1


def test_empty_negative_control_is_na_not_a_perfect_retrieval_score():
    control = {"expected_paths": [], "evidence": [], "expected_symbols": []}
    assert all(value is None for value in measure_retrieval(control, [], {"sources": []}).values())


def test_unlabelled_fallback_symbol_is_not_a_symbol_failure():
    label = case() | {"expected_symbols": []}
    assert measure_retrieval(label, [chunk(symbol=None)], {"sources": [chunk(symbol=None)]})["symbols"] is None


def test_supplementary_cases_do_not_inflate_canonical_retrieval_denominator():
    metrics = measure_retrieval(case(), [chunk()], {"sources": [chunk()]})
    rows = [{"group": "canonical", "retrieval_metrics": deepcopy(metrics)} for _ in range(12)]
    rows.append({"group": "supplementary", "retrieval_metrics": {}})
    assert summarize_retrieval(rows)["file"]["at_k"]["5"]["total"] == 12
    with pytest.raises(EvaluationError):
        summarize_retrieval(rows[1:])


def review_fixture(outcome="supported", selected=None, supporting=None, error=False, control=False):
    selected = ["S1"] if selected is None else selected
    supporting = (["S1"] if outcome == "supported" else []) if supporting is None else supporting
    response = {"http_status": 502 if error else 200, "body": {"source_ids": selected, "answer": "fixture text",
        "status": "insufficient_evidence" if outcome == "abstention" else "answered"}}
    row = {"id": "fixture", "mode": "code_chat", "group": "canonical", "response": response,
           "citation_check": {"applicable": bool(selected), "metadata_valid": True if selected else None,
                              "citation_count": len(selected)}, "retrieval_metrics": {"file": None if control else {}}}
    review = {"id": row["id"], "response_sha256": fingerprint(response), "outcome": outcome,
              "answer_supported": True if outcome == "supported" else None if outcome in {"abstention", "generation_error"} else False,
              "answer_complete": outcome == "supported", "reason": "explicit offline fixture", "supporting_source_ids": supporting,
              "citation_batch_supports_answer": set(selected) == set(supporting) if selected else None,
              "candidate_chain_supported": None}
    return {"cases": [row]}, {"cases": [review]}


def test_valid_coordinates_do_not_turn_unsupported_answer_into_a_pass():
    report, review = review_fixture("unsupported")
    score = summarize_reviews(report, review)["canonical"]
    assert score["supported_answers"] == 0 and score["citation_metadata"]["valid_batches"] == 1


def test_supported_but_incomplete_is_not_a_complete_answer():
    report, review = review_fixture()
    review["cases"][0]["answer_complete"] = False
    score = summarize_reviews(report, review)["canonical"]
    assert score["supported_answers"] == 1 and score["complete_substantive_answers"] == 0


def test_unrelated_extra_citations_fail_batch_support_without_erasing_supported_fact():
    report, review = review_fixture(selected=["S1", "S2"])
    score = summarize_reviews(report, review)["canonical"]
    assert score["supported_answers"] == 1 and score["all_selected_citations_support_answer"] == 0


@pytest.mark.parametrize("outcome,error", [("generation_error", True), ("abstention", False)])
def test_errors_and_abstentions_on_labelled_queries_remain_in_support_denominator(outcome, error):
    report, review = review_fixture(outcome, selected=[], error=error)
    score = summarize_reviews(report, review)["canonical"]
    assert score["support_applicable"] == 1 and score["support_rate"] == 0
    assert score["support_unjudged_or_abstained"] == 1


def test_negative_control_has_no_citation_accuracy_or_support_rate():
    report, review = review_fixture("abstention", selected=[], control=True)
    score = summarize_reviews(report, review)["canonical"]
    assert score["support_rate"] is None
    assert score["citation_metadata"]["cited_batches"] == 0


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "changed_response", "unknown_id", "false_complete"])
def test_review_rejects_missing_cases_reused_output_and_inconsistent_grades(mutation):
    report, review = review_fixture("unsupported")
    if mutation == "missing":
        review["cases"] = []
    elif mutation == "duplicate":
        review["cases"].append(deepcopy(review["cases"][0]))
    elif mutation == "changed_response":
        report["cases"][0]["response"]["body"]["answer"] = "altered response"
    elif mutation == "unknown_id":
        review["cases"][0]["supporting_source_ids"] = ["S99"]
    else:
        review["cases"][0]["answer_complete"] = True
    with pytest.raises(EvaluationError):
        summarize_reviews(report, review)


def test_frozen_stage3_labels_match_original_queries_and_source_files():
    data, _, _ = load_grounding_dataset(ROOT / "evaluation/datasets/stage3_grounding.json", ROOT / "workspaces")
    assert len(data["cases"]) == 16
    assert sum(c["group"] == "canonical" for c in data["cases"]) == 12
    assert sum(c["mode"] == "issue_analysis" for c in data["cases"]) == 2
