import asyncio
from copy import deepcopy
from dataclasses import replace
import json

import httpx
import pytest

from app.core.config import Settings
from app.retrieval.expansion import (ExpansionPlan, ExpansionPolicy, QueryExpander, deterministic_proposals,
    expansion_messages, normalized, protected_values, select_variants, ExpansionSuggestions)
from app.retrieval.expansion_llm import StructuredQueryGenerator


class Generator:
    def __init__(self, queries=(), *, raw=None, hang=False):
        self.queries, self.raw, self.hang, self.calls, self.last_receipt = queries, raw, hang, 0, None
    async def generate(self, messages, policy):
        self.calls += 1
        self.last_receipt = {"model": "test", "request": {"messages": messages, "max_tokens": 512, "temperature": 0,
            "schema": ExpansionSuggestions.model_json_schema()}, "http_attempts": 1, "status": "cancelled" if self.hang else "completed",
            "raw_content": self.raw if self.raw is not None else json.dumps({"queries": self.queries}, ensure_ascii=False),
            "finish_reason": "stop", "usage": None, "latency_ms": 0.}
        if self.hang:
            await asyncio.sleep(60)
        return self.last_receipt


def test_original_query_and_every_locked_symbol_path_error_are_preserved():
    async def run():
        original = '  WalletService.credit 在 backend/wallet/WalletService.java 抛出 "Amount  must be positive"？  '
        plan = await QueryExpander().expand(original, enabled=True)
        assert plan.original == plan.queries[0] == original
        assert 1 < len(plan.queries) <= 3
        assert '"Amount  must be positive"' in plan.to_dict()["protected_values"]
        for query in plan.queries:
            assert all(value in query for value in protected_values(original))
        assert plan.llm_calls == 0
        assert ExpansionPlan.from_dict(plan.to_dict()) == plan
    asyncio.run(run())


def test_unicode_normalization_does_not_rewrite_locked_text_or_quoted_spaces():
    text = 'ＷalletService.credit　在哪里？ "a  b"'
    value = normalized(text)
    assert "ＷalletService.credit" in value and '"a  b"' in value
    assert "在哪里?" in value and "　" not in value
    accepted, _ = select_variants(text, deterministic_proposals(text), ExpansionPolicy())
    assert accepted and accepted[0] == ("normalized", value)


@pytest.mark.parametrize("kwargs", [{"max_extra_queries": 3}, {"max_extra_queries": True}, {"max_llm_calls": 2}, {"max_candidate_slots": 211},
    {"max_original_characters": 4001}, {"max_variant_characters": 0}, {"max_total_characters": 12001},
    {"llm_timeout_seconds": float("nan")}, {"llm_timeout_seconds": 61}, {"variant_timeout_seconds": 181}])
def test_hard_expansion_budgets(kwargs):
    with pytest.raises(ValueError):
        ExpansionPolicy(**kwargs)


@pytest.mark.parametrize("queries,reason", [(["where is another method"], "protected_missing"),
    (["WalletService.credit"], "duplicate"), ([" WalletService.credit  "], "duplicate"), ([""], "blank"),
    (["WalletService.credit"+"x"*2100], "variant_length")])
def test_llm_suggestions_are_rejected_without_rewriting_original(queries, reason):
    async def run():
        generator = Generator(queries)
        plan = await QueryExpander(generator).expand("WalletService.credit", enabled=True, method="llm")
        assert plan.queries == ("WalletService.credit",) and plan.rejections[0]["reason"] == reason
        assert generator.calls == plan.llm_calls == 1
        assert ExpansionPlan.from_dict(plan.to_dict()) == plan
    asyncio.run(run())


def test_two_extra_queries_total_length_and_duplicate_caps_are_explicit():
    original = "WalletService.credit"
    proposals = (("llm", original+" wallet"), ("llm", original+"  wallet"), ("llm", original+" service"), ("llm", original+" more"))
    accepted, rejected = select_variants(original, proposals, ExpansionPolicy())
    assert len(accepted) == 2 and [r["reason"] for r in rejected] == ["duplicate", "query_budget"]
    accepted, rejected = select_variants(original, proposals[:1], ExpansionPolicy(max_total_characters=len(original)+1))
    assert not accepted and rejected[0]["reason"] == "total_length"


@pytest.mark.parametrize("raw", ['{"queries":["a","b","c"]}', '{"queries":[1]}', '{"queries":[],"instructions":"evil"}', 'not json'])
def test_invalid_structured_output_falls_back_once_and_is_retained(raw):
    async def run():
        generator = Generator(raw=raw)
        plan = await QueryExpander(generator).expand("credit", enabled=True, method="llm")
        assert plan.reason == "invalid_output" and plan.queries == ("credit",)
        assert plan.llm_receipt["raw_content"] == raw and generator.calls == 1
        assert ExpansionPlan.from_dict(plan.to_dict()) == plan
    asyncio.run(run())


def test_timeout_busy_cancellation_and_length_skip_are_bounded():
    async def run():
        generator = Generator(hang=True)
        expander = QueryExpander(generator)
        task = asyncio.create_task(expander.expand("credit", enabled=True, method="llm"))
        await asyncio.sleep(.01)
        assert (await expander.expand("credit", enabled=True, method="llm")).reason == "busy"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not expander.busy
        timed = await expander.expand("credit", enabled=True, method="llm", policy=ExpansionPolicy(llm_timeout_seconds=.01))
        assert timed.reason == "llm_timeout" and timed.queries == ("credit",)
        before = generator.calls
        assert (await expander.expand("x"*2001, enabled=True, method="llm")).reason == "input_limit"
        assert (await expander.expand("credit", enabled=True, method="llm", policy=ExpansionPolicy(max_llm_calls=0))).reason == "llm_disabled"
        assert (await expander.expand("credit")).reason == "disabled"
        assert generator.calls == before
    asyncio.run(run())


@pytest.mark.parametrize("change", ["original", "anchor", "queries", "calls", "accepted", "request", "attempts"])
def test_plan_response_and_preservation_bindings_reject_tampering(change):
    async def run():
        plan = await QueryExpander(Generator(["credit wallet"])).expand("credit", enabled=True, method="llm")
        data = deepcopy(plan.to_dict())
        if change == "original": data["original"] = "other"
        if change == "anchor": data["protected_values"] = []
        if change == "queries": data["queries"].append("extra")
        if change == "calls": data["llm_calls"] = 0
        if change == "accepted": data["accepted"] = []
        if change == "request": data["llm_receipt"]["request"]["max_tokens"] = 1024
        if change == "attempts": data["llm_receipt"]["http_attempts"] = 3
        with pytest.raises((ValueError, TypeError)):
            ExpansionPlan.from_dict(data)
    asyncio.run(run())


@pytest.mark.parametrize("status", [200, 503])
def test_real_sdk_transport_has_one_attempt_structured_schema_and_closes(status):
    requests = []
    async def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        if status == 503:
            return httpx.Response(503, json={"error": {"message": "private upstream details"}})
        return httpx.Response(200, json={"id": "test", "object": "chat.completion", "created": 0, "model": "test",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": '{"queries":["credit wallet"]}'}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}})
    async def run():
        settings = Settings(_env_file=None, llm_provider="openai_compatible", llm_base_url="http://localhost:11434/v1", llm_model="test", llm_api_key="test-placeholder")
        generator = StructuredQueryGenerator(settings, transport=httpx.MockTransport(handler))
        plan = await QueryExpander(generator).expand("credit", enabled=True, method="llm")
        assert len(requests) == plan.llm_receipt["http_attempts"] == 1
        assert requests[0]["max_tokens"] == 512 and requests[0]["response_format"]["type"] == "json_schema"
        assert "private upstream details" not in json.dumps(plan.to_dict())
        assert plan.queries == (("credit", "credit wallet") if status == 200 else ("credit",))
    asyncio.run(run())


def test_transport_cancellation_has_no_retry_or_late_variants():
    attempts, cancelled = [], []
    async def handler(request):
        attempts.append(True)
        try:
            await asyncio.sleep(60)
        finally:
            cancelled.append(True)
    async def run():
        settings = Settings(_env_file=None, llm_provider="openai_compatible", llm_base_url="http://localhost:11434/v1", llm_model="test", llm_api_key="test-placeholder")
        generator = StructuredQueryGenerator(settings, transport=httpx.MockTransport(handler))
        plan = await QueryExpander(generator).expand("credit", enabled=True, method="llm", policy=ExpansionPolicy(llm_timeout_seconds=.1))
        assert plan.queries == ("credit",) and plan.reason == "llm_timeout"
        assert len(attempts) == len(cancelled) == plan.llm_receipt["http_attempts"] == 1
        assert plan.llm_receipt["status"] == "cancelled"
        assert ExpansionPlan.from_dict(plan.to_dict()) == plan
    asyncio.run(run())


def test_stale_generator_receipt_does_not_break_original_only_fallback():
    class Stale:
        last_receipt = {"request": {"messages": []}}
        async def generate(self, messages, policy):
            raise RuntimeError("private details")
    async def run():
        plan = await QueryExpander(Stale()).expand("credit", enabled=True, method="llm")
        assert plan.queries == ("credit",) and plan.reason == "invalid_output" and plan.llm_calls == 1 and plan.llm_receipt is None
    asyncio.run(run())
