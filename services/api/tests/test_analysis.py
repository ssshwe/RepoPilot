from collections.abc import AsyncGenerator, Sequence
from typing import Any, TypeVar
from unittest.mock import patch

from fastapi.testclient import TestClient
from httpx import Response
from pydantic import BaseModel

from app.api.analysis import ANALYSIS_SYSTEM_PROMPT
from app.core.exceptions import LLMConfigurationError, LLMProviderError
from app.llm.base import LLMProvider
from app.llm.tool_types import (
    FunctionToolDefinition,
    ToolAssistantTurn,
    ToolLoopMessage,
)
from app.main import app
from app.schemas.analysis import RequirementAnalysis
from app.schemas.chat import ChatMessage


client = TestClient(app)
ResponseModelT = TypeVar("ResponseModelT", bound=BaseModel)


class AnalysisStubProvider(LLMProvider):
    def __init__(self, *, should_fail: bool = False) -> None:
        self.should_fail = should_fail
        self.received_messages: list[ChatMessage] = []
        self.received_response_model: type[BaseModel] | None = None

    @property
    def model_name(self) -> str:
        return "analysis-stub"

    async def complete(self, _messages: Sequence[ChatMessage]) -> str:
        raise AssertionError("plain completion is not used by analysis tests")

    async def complete_structured(
        self,
        messages: Sequence[ChatMessage],
        response_model: type[ResponseModelT],
    ) -> ResponseModelT:
        self.received_messages = list(messages)
        self.received_response_model = response_model
        if self.should_fail:
            raise LLMProviderError(
                "The configured LLM provider returned invalid structured output."
            )
        return response_model.model_validate(
            {
                "summary": "构建带账号密码认证的登录功能",
                "difficulty": "medium",
                "tasks": ["设计登录接口", "实现密码校验", "添加接口测试"],
                "risks": ["密码存储不安全", "暴力破解"],
            }
        )

    async def complete_with_tools(
        self,
        _messages: Sequence[ToolLoopMessage],
        _tools: Sequence[FunctionToolDefinition],
    ) -> ToolAssistantTurn:
        raise AssertionError("tool completion is not used by analysis tests")

    async def stream(
        self, _messages: Sequence[ChatMessage]
    ) -> AsyncGenerator[str, None]:
        raise AssertionError("streaming is not used by analysis tests")
        yield ""


def post_analyze(payload: dict[str, Any], provider: AnalysisStubProvider) -> Response:
    with patch("app.api.analysis.get_llm_provider", return_value=provider):
        return client.post("/api/analyze", json=payload)


def test_analyze_returns_validated_structured_output() -> None:
    provider = AnalysisStubProvider()
    requirement = "为管理后台实现账号密码登录，并限制连续失败次数。"

    response = post_analyze({"requirement": requirement}, provider)

    assert response.status_code == 200
    assert response.json() == {
        "summary": "构建带账号密码认证的登录功能",
        "difficulty": "medium",
        "tasks": ["设计登录接口", "实现密码校验", "添加接口测试"],
        "risks": ["密码存储不安全", "暴力破解"],
    }
    assert provider.received_response_model is RequirementAnalysis
    assert [message.model_dump() for message in provider.received_messages] == [
        {"role": "system", "content": ANALYSIS_SYSTEM_PROMPT},
        {"role": "user", "content": requirement},
    ]


def test_analyze_validates_request_before_resolving_provider() -> None:
    invalid_payloads = [
        {"requirement": "   "},
        {"requirement": "需求", "unexpected": True},
        {},
    ]

    with patch(
        "app.api.analysis.get_llm_provider",
        side_effect=LLMConfigurationError("LLM is not configured."),
    ) as provider_resolver:
        for payload in invalid_payloads:
            response = client.post("/api/analyze", json=payload)
            assert response.status_code == 422

    provider_resolver.assert_not_called()


def test_analyze_returns_503_for_configuration_failure() -> None:
    with patch(
        "app.api.analysis.get_llm_provider",
        side_effect=LLMConfigurationError("LLM is not configured."),
    ):
        response = client.post("/api/analyze", json={"requirement": "需求"})

    assert response.status_code == 503
    assert response.json() == {"detail": "LLM is not configured."}


def test_analyze_returns_safe_error_for_invalid_provider_output() -> None:
    response = post_analyze(
        {"requirement": "需求"}, AnalysisStubProvider(should_fail=True)
    )

    assert response.status_code == 502
    assert response.json() == {
        "detail": (
            "The configured LLM provider returned invalid structured output."
        )
    }
