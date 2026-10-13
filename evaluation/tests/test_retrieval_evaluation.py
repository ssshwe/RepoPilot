from copy import deepcopy
import hashlib
import json
from pathlib import Path
from urllib.error import HTTPError

import pytest

from evaluation.retrieval import evaluate as evaluator

ROOT = Path(__file__).resolve().parents[2]
DATASET_PATH = ROOT / "evaluation/datasets/stage2_retrieval.json"


def load_real_dataset():
    return evaluator.load_dataset(DATASET_PATH, ROOT / "workspaces")


def save_dataset(tmp_path, data):
    target = tmp_path / "dataset.json"
    target.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return target


def response(request, *, paths=()):
    results = []
    for rank, path in enumerate(paths, start=1):
        content = (ROOT / "workspaces/demo-repository" / path).read_bytes().decode("utf-8-sig")
        results.append({"rank": rank, "chunk_id": f"offline-chunk-{rank}",
                        "repository": request["repository"], "score": .9 - rank / 100,
                        "path": path, "language": "test", "start_line": 1,
                        "end_line": len(content.splitlines()), "content": content})
    return {**request, "collection": "offline-test", "embedding_model": "offline-test-model",
            "embedding_dimension": 3, "retrieval_latency_ms": 1.5, "results": results}


def test_hit_and_recall_are_different_for_multi_file_queries():
    metrics = evaluator.metrics_for_case(["a.java", "noise.java"], ["a.java", "b.java", "c.java"])
    assert metrics["1"] == {"hit": True, "recall": 1 / 3, "matched_paths": ["a.java"]}
    assert metrics["5"]["recall"] == 1 / 3


def test_duplicate_chunks_count_once_without_changing_the_chunk_cutoff():
    metrics = evaluator.metrics_for_case(["a.java", "a.java", "b.java"], ["a.java", "b.java"], (1, 2, 3))
    assert metrics["2"]["recall"] == .5
    assert metrics["3"]["recall"] == 1


def test_file_matching_is_exact_and_case_sensitive_and_empty_is_a_miss():
    assert not evaluator.metrics_for_case(["other/a.java", "A.java"], ["a.java"])["5"]["hit"]
    assert evaluator.metrics_for_case([], ["a.java"])["5"]["recall"] == 0


def test_macro_recall_averages_queries_rather_than_all_file_labels():
    rows = [{"metrics": evaluator.metrics_for_case(["a"], ["a"])},
            {"metrics": evaluator.metrics_for_case(["b"], ["b", "c", "d"])}]
    summary = evaluator.summarize(rows)
    assert summary["queries"] == 2
    assert summary["hit_at_k"]["1"]["rate"] == 1
    assert summary["macro_recall_at_k"]["1"] == pytest.approx(2 / 3)
    assert summary["macro_recall_at_k"]["1"] != .5  # Micro recall would be 2/4.


@pytest.mark.parametrize("expected,ks", [([], (1,)), (["a", "a"], (1,)), (["a"], (0,)), (["a"], (True,))])
def test_rejects_invalid_metric_denominators_or_k(expected, ks):
    with pytest.raises(evaluator.EvaluationError):
        evaluator.metrics_for_case(["a"], expected, ks)


def test_actual_dataset_labels_have_real_evidence_and_cover_all_indexed_files():
    dataset = load_real_dataset()
    assert len(dataset.data["cases"]) == 12
    assert len(dataset.data["source_sha256"]) == 8
    assert dataset.sha256 == hashlib.sha256(DATASET_PATH.read_bytes()).hexdigest()
    assert [len(case["expected_paths"]) for case in dataset.data["cases"]].count(1) == 10


@pytest.mark.parametrize("change", ["duplicate_id", "duplicate_expected", "missing_evidence", "invented_evidence", "changed_hash", "blank_query"])
def test_rejects_invalid_or_stale_labels(tmp_path, change):
    data = deepcopy(load_real_dataset().data)
    first = data["cases"][0]
    if change == "duplicate_id":
        data["cases"][1]["id"] = first["id"]
    elif change == "duplicate_expected":
        first["expected_paths"] *= 2
    elif change == "missing_evidence":
        first["evidence"] = []
    elif change == "invented_evidence":
        first["evidence"][0]["snippet"] = "nonexistent_source_symbol"
    elif change == "changed_hash":
        data["source_sha256"][first["expected_paths"][0]] = "0" * 64
    else:
        first["query"] = " \n"
    with pytest.raises(evaluator.EvaluationError):
        evaluator.load_dataset(save_dataset(tmp_path, data), ROOT / "workspaces")


@pytest.mark.parametrize("path", ["../README.md", "/README.md", "C:/README.md", "backend\\a.java", "missing.java"])
def test_rejects_unsafe_or_nonexistent_source_paths(path):
    with pytest.raises(evaluator.EvaluationError):
        evaluator.source_file(ROOT / "workspaces/demo-repository", path)


def test_evaluation_calls_once_per_query_with_top5_and_retains_empty_misses():
    dataset, calls = load_real_dataset(), []

    def search(body):
        calls.append(body)
        return response(body)  # Intentional offline empty results, not real metrics.

    report = evaluator.evaluate(dataset, search)
    assert len(calls) == 12 and {call["top_k"] for call in calls} == {5}
    assert [call["query"] for call in calls] == [case["query"] for case in dataset.data["cases"]]
    assert report["summary"]["hit_at_k"]["5"] == {"hits": 0, "total": 12, "rate": 0}
    assert report["summary"]["macro_recall_at_k"]["5"] == 0


@pytest.mark.parametrize("change", ["foreign_repo", "wrong_echo", "wrong_rank", "bad_score", "stale_source", "too_many", "duplicate_chunk"])
def test_rejects_invalid_api_results(change):
    request = {"repository": "demo-repository", "query": "refund", "top_k": 5}
    data = response(request, paths=["backend/service/RefundService.java"])
    if change == "foreign_repo":
        data["results"][0]["repository"] = "foreign"
    elif change == "wrong_echo":
        data["query"] = "different"
    elif change == "wrong_rank":
        data["results"][0]["rank"] = True
    elif change == "bad_score":
        data["results"][0]["score"] = float("nan")
    elif change == "stale_source":
        data["results"][0]["content"] = "different_source\n"
    elif change == "too_many":
        data["results"] *= 6
    else:
        data["results"].append({**data["results"][0], "rank": 2})
    with pytest.raises(evaluator.EvaluationError):
        evaluator.validate_response(data, request, ROOT / "workspaces/demo-repository")


def test_model_changes_abort_the_run():
    calls = 0

    def search(body):
        nonlocal calls
        calls += 1
        data = response(body)
        if calls > 1:
            data["embedding_model"] = "another-model"
        return data

    with pytest.raises(evaluator.EvaluationError, match="Model/collection changed"):
        evaluator.evaluate(load_real_dataset(), search)


def test_http_failure_is_safe_and_not_counted_as_a_successful_query(monkeypatch):
    class BrokenOpener:
        def open(self, *_args, **_kwargs):
            raise HTTPError("http://localhost:8000", 503, "private-upstream-detail", {}, None)

    monkeypatch.setattr(evaluator, "build_opener", lambda *_: BrokenOpener())
    with pytest.raises(evaluator.EvaluationError, match="HTTP 503") as error:
        evaluator.search_api("http://localhost:8000", {"query": "refund"})
    assert "private-upstream-detail" not in str(error.value)


def test_failed_run_writes_no_report_and_does_not_skip_failed_queries(tmp_path, monkeypatch):
    calls = 0

    def search(_url, body, _timeout):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise evaluator.EvaluationError("HTTP 503")
        return response(body)

    monkeypatch.setattr(evaluator, "search_api", search)
    output = tmp_path / "failed.json"
    assert evaluator.main(["--output", str(output)]) == 1
    assert calls == 2 and not output.exists()


def test_completed_report_is_written_once_and_existing_reports_are_preserved(tmp_path, monkeypatch):
    monkeypatch.setattr(evaluator, "search_api", lambda _url, body, _timeout: response(body))
    output = tmp_path / "complete.json"
    assert evaluator.main(["--output", str(output)]) == 0
    original = output.read_bytes()
    assert json.loads(original)["status"] == "complete"
    assert evaluator.main(["--output", str(output)]) == 1
    assert output.read_bytes() == original
