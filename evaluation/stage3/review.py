"""Validate explicit source reviews and summarize grounding separately from retrieval/coordinates."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from evaluation.stage3.evaluate import ROOT, fingerprint
from evaluation.retrieval.evaluate import require

OUTCOMES = {"supported", "unsupported", "nonresponsive", "abstention", "generation_error"}


def summarize_reviews(report, reviews):
    rows = report["cases"]
    ids = [r["id"] for r in rows]
    judged = reviews["cases"]
    require(len(ids) == len(set(ids)) and [j["id"] for j in judged] == ids, "Review must cover each case exactly once in original order")
    for row, review in zip(rows, judged, strict=True):
        require(review.get("response_sha256") == fingerprint(row["response"]), "Review refers to a different actual response")
        outcome = review.get("outcome")
        require(outcome in OUTCOMES, "Invalid review outcome")
        require(type(review.get("answer_complete")) is bool and isinstance(review.get("reason"), str) and bool(review["reason"].strip()), "Complete flag and review explanation required")
        expected_supported = True if outcome == "supported" else None if outcome in {"abstention", "generation_error"} else False
        require(review.get("answer_supported") is expected_supported, "Support verdict must match outcome")
        require(not review["answer_complete"] or outcome in {"supported", "abstention"}, "Unsupported output cannot be complete")
        body = row["response"]["body"]
        selected = body.get("source_ids", [])
        support = review.get("supporting_source_ids")
        require(isinstance(support, list) and all(isinstance(s, str) for s in support) and len(support) == len(set(support)) and set(support).issubset(selected), "Review supporting IDs must be actual selected IDs")
        require(bool(support) == (outcome == "supported"), "A supported result requires actual supporting citation evidence")
        expected_batch = set(selected) == set(support) if selected else None
        require(review.get("citation_batch_supports_answer") is expected_batch,
                "Semantic citation batch verdict does not match selected/supporting IDs")
        require(outcome != "generation_error" or row["response"]["http_status"] != 200, "Generation error must retain error response")
        require(outcome != "abstention" or body.get("status") == "insufficient_evidence", "Abstention must reflect final API status")
        require(row["response"]["http_status"] == 200 or outcome == "generation_error", "Failed HTTP must remain an explicit error review")
        require(review.get("candidate_chain_supported") is None or row["mode"] == "issue_analysis" and type(review["candidate_chain_supported"]) is bool, "Chain verdict only applies to Issue outputs")
    by_id = {j["id"]: j for j in judged}
    groups = {}
    for group, selected in (("canonical", [r for r in rows if r["group"] == "canonical"]),
                            ("all", rows)):
        require(bool(selected), "Cannot summarize empty review group")
        verdicts = [by_id[r["id"]] for r in selected]
        # Only the explicitly unlabelled negative control is N/A. Errors and
        # model abstention on labelled questions stay in the scoring denominator.
        applicable = [by_id[r["id"]] for r in selected if r["retrieval_metrics"]["file"] is not None]
        cited_rows = [r for r in selected if r["citation_check"]["applicable"]]
        groups[group] = {"cases": len(selected), "outcomes": dict(Counter(j["outcome"] for j in verdicts)),
                         "supported_answers": sum(j["answer_supported"] is True for j in applicable),
                         "support_applicable": len(applicable),
                         "support_rate": sum(j["answer_supported"] is True for j in applicable) / len(applicable) if applicable else None,
                         "complete_responses": sum(j["answer_complete"] for j in verdicts),
                         "complete_substantive_answers": sum(j["answer_complete"] for j in applicable),
                         "support_unjudged_or_abstained": sum(j["answer_supported"] is None for j in applicable),
                         "citation_metadata": {"valid_batches": sum(r["citation_check"]["metadata_valid"] is True for r in cited_rows),
                                               "cited_batches": len(cited_rows),
                                               "citations": sum(r["citation_check"]["citation_count"] for r in cited_rows),
                                               "non_applicable_batches": len(selected) - len(cited_rows)},
                         "all_selected_citations_support_answer": sum(by_id[r["id"]]["citation_batch_supports_answer"] is True for r in cited_rows)}
    return groups


def review_report(report_path, review_path):
    report = json.loads(report_path.read_bytes())
    reviews = json.loads(review_path.read_bytes())
    require(reviews.get("report_sha256") == hashlib.sha256(report_path.read_bytes()).hexdigest(), "Raw report changed after review")
    require(reviews.get("dataset_sha256") == report["dataset_sha256"], "Review dataset mismatch")
    require(isinstance(reviews.get("reviewer"), dict) and reviews["reviewer"].get("role") in {"development_assistant", "human"}, "Explicit reviewer identity required")
    summary = summarize_reviews(report, reviews)
    return {"schema_version": 1, "kind": "source review summary; no inference rerun", "raw_report": report_path.relative_to(ROOT).as_posix(),
            "raw_report_sha256": reviews["report_sha256"], "review_file": review_path.relative_to(ROOT).as_posix(),
            "dataset_sha256": report["dataset_sha256"], "reviewer": reviews["reviewer"],
            "support_definition": reviews["support_definition"], "retrieval_summary": report["retrieval_summary"],
            "review_summary": summary, "reviews": reviews["cases"], "qdrant_unchanged": report["qdrant_unchanged"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    require(not args.output.exists(), "Existing review summary must not be overwritten")
    result = review_report(args.report.resolve(), args.review.resolve())
    with args.output.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result["review_summary"], ensure_ascii=True))


if __name__ == "__main__":
    main()
