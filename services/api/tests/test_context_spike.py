import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import NAMESPACE_URL, uuid5

import pytest

from app.context import ContextBuilder, ContextBuilderError
from app.core.exceptions import EmbeddingProviderError
from app.schemas.retrieval import RetrievalResult, RetrievalSearchRequest, RetrievalSearchResponse
from scripts import context_spike


@pytest.fixture
def fake_pipeline(monkeypatch):
    request = RetrievalSearchRequest(repository="demo", query="退款")
    item = RetrievalResult(rank=1, chunk_id=str(uuid5(NAMESPACE_URL, "context-cli-test")),
                           repository="demo", score=.8, path="refund.py", language="python",
                           start_line=10, end_line=11, content="def refund():\n    return 1\n")
    response = RetrievalSearchResponse(**request.model_dump(), collection="unit-only",
                                       embedding_model="unit-only", embedding_dimension=3,
                                       retrieval_latency_ms=1, results=[item])
    provider = SimpleNamespace(aclose=AsyncMock())
    client = SimpleNamespace(close=AsyncMock())
    service = SimpleNamespace(search=AsyncMock(return_value=response))
    monkeypatch.setattr(context_spike.EmbeddingProviderFactory, "create", lambda _: provider)
    monkeypatch.setattr(context_spike, "create_qdrant_client", lambda _: client)
    monkeypatch.setattr(context_spike, "RetrievalService", lambda *args: service)
    return request, response, provider, client, service


def test_retrieval_to_context_returns_metadata_and_closes_clients(fake_pipeline):
    request, _, provider, client, service = fake_pipeline
    output = asyncio.run(context_spike.retrieve_and_build(request, ContextBuilder()))
    service.search.assert_awaited_once_with(request)
    assert output["llm_called"] is False
    assert output["context"]["sources"][0]["source_id"] == "S1"
    assert output["context"]["sources"][0]["path"] == "refund.py"
    assert output["context"]["sources"][0]["start_line"] == 10
    assert output["context"]["sources"][0]["content"] == "def refund():\n    return 1\n"
    provider.aclose.assert_awaited_once()
    client.close.assert_awaited_once()


@pytest.mark.parametrize("failure", ["client_creation", "retrieval", "builder"])
def test_failures_still_close_created_resources(monkeypatch, fake_pipeline, failure):
    request, response, provider, client, service = fake_pipeline
    if failure == "client_creation":
        def fail(_):
            raise EmbeddingProviderError("PRIVATE_UPSTREAM")
        monkeypatch.setattr(context_spike, "create_qdrant_client", fail)
    elif failure == "retrieval":
        service.search.side_effect = EmbeddingProviderError("PRIVATE_UPSTREAM")
    else:
        response.results[0].repository = "other"
    expected = ContextBuilderError if failure == "builder" else EmbeddingProviderError
    with pytest.raises(expected):
        asyncio.run(context_spike.retrieve_and_build(request, ContextBuilder()))
    provider.aclose.assert_awaited_once()
    if failure != "client_creation":
        client.close.assert_awaited_once()


@pytest.mark.parametrize("arguments", [
    ["--top-k", "0"], ["--top-k", "21"], ["--max-chunks", "0"],
    ["--max-chunks", "21"], ["--max-characters", "0"],
    ["--max-characters", "200001"], ["--repository", "../other"],
])
def test_cli_rejects_bad_request_before_any_provider_creation(monkeypatch, capsys, arguments):
    def forbidden(_):
        raise AssertionError("Provider must not be opened")
    monkeypatch.setattr(context_spike.EmbeddingProviderFactory, "create", forbidden)
    assert context_spike.main(["--query", "退款", *arguments]) == 1
    captured = capsys.readouterr()
    assert not captured.out and "Invalid request or context budget" in captured.err


def test_cli_success_is_json_and_stops_at_context(fake_pipeline, capsys):
    assert context_spike.main(["--repository", "demo", "--query", "退款"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["llm_called"] is False and "answer" not in output
    assert output["context"]["text"].startswith("[SOURCE S1]\n")
    assert output["context"]["characters_used"] == len(output["context"]["text"])


def test_cli_failure_emits_no_partial_context_or_upstream_detail(fake_pipeline, capsys):
    fake_pipeline[-1].search.side_effect = EmbeddingProviderError("PRIVATE_KEY PRIVATE_CODE")
    assert context_spike.main(["--repository", "demo", "--query", "退款"]) == 1
    captured = capsys.readouterr()
    assert not captured.out and "PRIVATE" not in captured.err
    assert "EmbeddingProviderError" in captured.err


def test_cli_output_file_preserves_unicode_without_shell_transcoding(fake_pipeline, tmp_path, capsys):
    output_path = tmp_path / "上下文.json"
    fake_pipeline[1].results[0].content = "def refund():\n    return '退款到钱包'\n"
    query = "退款在哪里？"
    assert context_spike.main(["--repository", "demo", "--query", query,
                               "--output", str(output_path)]) == 0
    assert not capsys.readouterr().out
    raw = output_path.read_bytes()
    output = json.loads(raw.decode("utf-8"))
    assert output["request"]["query"] == query
    assert output["context"]["sources"][0]["content"] == fake_pipeline[1].results[0].content
    assert output["context"]["characters_used"] == len(output["context"]["text"])
    assert not raw.startswith(b"\xef\xbb\xbf")


def test_cli_failure_preserves_existing_output_file(fake_pipeline, tmp_path, capsys):
    output_path = tmp_path / "existing.json"
    output_path.write_bytes(b"existing report")
    fake_pipeline[-1].search.side_effect = EmbeddingProviderError("PRIVATE_CODE")
    assert context_spike.main(["--repository", "demo", "--query", "退款",
                               "--output", str(output_path)]) == 1
    assert output_path.read_bytes() == b"existing report"
    assert not capsys.readouterr().out


def test_cli_output_write_failure_emits_no_partial_context(fake_pipeline, tmp_path, capsys):
    assert context_spike.main(["--repository", "demo", "--query", "退款",
                               "--output", str(tmp_path / "missing" / "report.json")]) == 1
    captured = capsys.readouterr()
    assert not captured.out and "FileNotFoundError" in captured.err
