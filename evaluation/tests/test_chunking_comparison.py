import asyncio

import pytest

from evaluation.retrieval.compare_chunking import SharedQueryEmbedding, evidence_metrics
from evaluation.retrieval.evaluate import EvaluationError


def chunk(path, start, end):
    return dict(path=path, start_line=start, end_line=end)


def test_correct_file_with_wrong_lines_does_not_cover_evidence():
    evidence = [chunk("refund.java", 12, 18)]
    metrics = evidence_metrics([chunk("refund.java", 1, 4)], evidence)
    assert not metrics["5"]["hit"] and metrics["5"]["recall"] == 0


def test_adjacent_chunks_can_cover_evidence_only_after_the_chunk_cutoff():
    evidence = [chunk("refund.java", 12, 18)]
    results = [chunk("refund.java", 12, 14), chunk("noise.java", 1, 30), chunk("refund.java", 15, 18)]
    metrics = evidence_metrics(results, evidence)
    assert metrics["1"]["recall"] == 0
    assert metrics["3"]["recall"] == 1


def test_a_line_gap_is_not_complete_coverage_and_paths_match_exactly():
    evidence = [chunk("refund.java", 12, 18)]
    assert not evidence_metrics([chunk("refund.java", 12, 14), chunk("refund.java", 16, 18)], evidence)["5"]["hit"]
    assert not evidence_metrics([chunk("Refund.java", 1, 99)], evidence)["5"]["hit"]


def test_multi_file_recall_deduplicates_repeated_chunks_and_keeps_missing_evidence():
    evidence = [chunk("refund.java", 12, 18), chunk("wallet.java", 10, 18)]
    results = [chunk("refund.java", 12, 18), chunk("refund.java", 10, 20), chunk("wallet.java", 1, 6)]
    metrics = evidence_metrics(results, evidence)
    assert metrics["3"]["hit"]
    assert metrics["3"]["recall"] == .5
    assert metrics["3"]["matched_paths"] == ["refund.java"]


def test_empty_retrieval_remains_a_miss():
    assert evidence_metrics([], [chunk("refund.java", 12, 18)])["5"]["recall"] == 0


def test_shared_query_vector_is_not_modified_and_cannot_be_used_for_another_query():
    vector = [1., 2., 3.]
    shared = SharedQueryEmbedding("model", "退款", vector)
    output = asyncio.run(shared.embed_texts(["退款"]))
    output[0][0] = 100
    assert vector == [1., 2., 3.]
    assert shared.model_name == "model"
    with pytest.raises(EvaluationError):
        asyncio.run(shared.embed_texts(["other"]))


@pytest.mark.parametrize("failure", ["snapshot", "client_creation"])
def test_comparison_closes_clients_when_initialization_fails(monkeypatch, failure):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from evaluation.retrieval import compare_chunking as comparison
    from evaluation.retrieval.evaluate import load_dataset
    dataset_path = comparison.ROOT / "evaluation/datasets/stage2_retrieval.json"
    dataset = load_dataset(dataset_path, comparison.ROOT / "workspaces")
    provider = SimpleNamespace(aclose=AsyncMock())
    client = SimpleNamespace(close=AsyncMock(), get_collections=AsyncMock(side_effect=RuntimeError("failure")))
    monkeypatch.setattr(comparison.EmbeddingProviderFactory, "create", lambda _: provider)
    def create(_):
        if failure == "client_creation":
            raise RuntimeError("failure")
        return client
    monkeypatch.setattr(comparison, "create_qdrant_client", create)
    with pytest.raises(RuntimeError):
        asyncio.run(comparison.compare(dataset, dataset_path))
    provider.aclose.assert_awaited_once()
    if failure == "snapshot":
        client.close.assert_awaited_once()


@pytest.mark.parametrize("deletion_failure", [False, True])
def test_failed_indexing_cleans_only_private_collections_and_attempts_both(monkeypatch, deletion_failure):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from evaluation.retrieval import compare_chunking as comparison
    original = {"production": {"points_sha256": "unchanged"}}
    snapshots = AsyncMock(return_value=original)
    monkeypatch.setattr(comparison, "snapshot", snapshots)
    client = SimpleNamespace(
        get_collections=AsyncMock(return_value=SimpleNamespace(collections=[SimpleNamespace(name="production")])),
        collection_exists=AsyncMock(return_value=True),
        delete_collection=AsyncMock(side_effect=[False, True] if deletion_failure else [True, True]))
    indexer = SimpleNamespace(index=AsyncMock(side_effect=RuntimeError("private-upstream-body")))
    monkeypatch.setattr(comparison, "RepositoryIndexer", lambda *_: indexer)
    error = EvaluationError if deletion_failure else RuntimeError
    with pytest.raises(error) as caught:
        asyncio.run(comparison._compare_with_clients(
            None, None, None, None,
            {"simple": SimpleNamespace(chunks=[1]), "ast": SimpleNamespace(chunks=[1])},
            {}, None, client, lambda *_, **__: None))
    names = [call.args[0] for call in client.delete_collection.await_args_list]
    assert len(names) == 2
    assert all(name.startswith("repopilot_chunk_compare_") for name in names)
    assert "production" not in names
    if deletion_failure:
        assert "private-upstream-body" not in str(caught.value)
        assert names[0] in str(caught.value)
    else:
        assert snapshots.await_count == 2
