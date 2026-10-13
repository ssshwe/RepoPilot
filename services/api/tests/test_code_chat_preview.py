import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import NAMESPACE_URL, uuid5

from pydantic import ValidationError
import pytest

from app.context import ContextBuilder
from app.rag.service import CodeChatService
from app.schemas.code_chat import CodeChatRequest, CodeChatResponse
from app.schemas.retrieval import RetrievalResult, RetrievalSearchResponse


QUESTION = "退款校验在哪？"


def run(*, budget=16000, empty=False, abstain=False):
    result = RetrievalResult(rank=1, score=0.5, chunk_id=str(uuid5(NAMESPACE_URL, "preview/refund")),
        repository="demo", path="refund.py", language="python", start_line=12, end_line=15,
        content='def refund(order_id):\n    if not order_id:\n        raise ValueError("required")\n    return True\n',
        symbol="refund", node_type="function_definition", parent_symbol=None,
        chunking_strategy="ast", chunking_version="ast-v1")
    retrieval = SimpleNamespace(search=AsyncMock(return_value=RetrievalSearchResponse(
        repository="demo", query=QUESTION, top_k=5, collection="unit", embedding_model="unit",
        embedding_dimension=3, retrieval_latency_ms=1, results=[] if empty else [result])))
    async def complete(messages, model):
        return model.model_validate({"answer": "refund 检查订单编号", "source_ids": [] if abstain else ["S1"]})
    provider = SimpleNamespace(model_name="unit", complete_structured=AsyncMock(side_effect=complete))
    response = asyncio.run(CodeChatService(retrieval, ContextBuilder(max_context_characters=budget), lambda: provider).answer(
        CodeChatRequest(repository="demo", question=QUESTION)))
    return response, result, provider


def test_preview_is_exact_cited_context_without_new_reads_or_retrieval():
    with patch("app.repository.scanner.RepositoryScanner.scan", side_effect=AssertionError("no scan")), patch(
        "app.repository.paths.read_bounded_file", side_effect=AssertionError("no read")):
        response, original, provider = run()
    assert response.question == QUESTION
    assert [s.model_dump() for s in response.sources] == [{"source_id": "S1", "language": "python", "content": original.content}]
    assert response.citations[0].start_line == 12 and response.citations[0].end_line == 15
    assert set(provider.complete_structured.call_args.args[1].model_json_schema()["properties"]) == {"answer", "source_ids"}


def test_preview_uses_truncated_range_instead_of_full_original_chunk():
    full, _, _ = run()
    response, original, _ = run(budget=full.retrieval.context_characters - 1)
    assert response.retrieval.budget_limited and response.sources
    citation, source = response.citations[0], response.sources[0]
    assert citation.end_line < original.end_line
    assert source.content == "".join(original.content.splitlines(keepends=True)[:citation.end_line - citation.start_line + 1])


@pytest.mark.parametrize("empty,abstain", [(True, False), (False, True)])
def test_empty_evidence_or_model_abstention_returns_no_preview(empty, abstain):
    response, _, provider = run(empty=empty, abstain=abstain)
    assert response.sources == response.citations == response.source_ids == []
    if empty:
        provider.complete_structured.assert_not_awaited()


@pytest.mark.parametrize("damage", ["missing", "duplicate", "id", "range", "blank", "surrogate", "extra"])
def test_preview_contract_rejects_missing_mismatched_or_invalid_content(damage):
    response, _, _ = run()
    data = deepcopy(response.model_dump())
    if damage == "missing":
        del data["sources"]
    elif damage == "duplicate":
        data["sources"].append(data["sources"][0])
    elif damage == "id":
        data["sources"][0]["source_id"] = "S2"
    elif damage == "range":
        data["sources"][0]["content"] = "one line\n"
    elif damage == "extra":
        data["sources"][0]["unexpected"] = True
    else:
        data["sources"][0]["content"] = " " if damage == "blank" else "\ud800"
    with pytest.raises(ValidationError):
        CodeChatResponse.model_validate(data)
