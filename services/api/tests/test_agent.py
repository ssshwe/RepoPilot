from collections.abc import AsyncGenerator, Sequence
from typing import TypeVar
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.core.exceptions import LLMConfigurationError
from app.llm.base import LLMProvider
from app.llm.tool_types import (
    FunctionToolDefinition,
    ToolAssistantTurn,
    ToolCall,
    ToolLoopMessage,
)
from app.main import app
from app.schemas.chat import ChatMessage


client = TestClient(app)
ResponseModelT = TypeVar("ResponseModelT", bound=BaseModel)


class AgentStubProvider(LLMProvider):
    def __init__(self, *, final_answer: str = "项目技术栈来自工具结果。") -> None:
        self.final_answer = final_answer
        self.call_count = 0

    @property
    def model_name(self) -> str:
        return "agent-stub"

    async def complete(self, _messages: Sequence[ChatMessage]) -> str:
        raise AssertionError("plain completion is not used by the agent endpoint")

    async def complete_structured(
        self,
        _messages: Sequence[ChatMessage],
        _response_model: type[ResponseModelT],
    ) -> ResponseModelT:
        raise AssertionError("structured completion is not used by this endpoint")

    async def complete_with_tools(
        self,
        _messages: Sequence[ToolLoopMessage],
        _tools: Sequence[FunctionToolDefinition],
    ) -> ToolAssistantTurn:
        self.call_count += 1
        if self.call_count == 1:
            return ToolAssistantTurn(
                tool_calls=[
                    ToolCall(
                        id="call-1",
                        name="get_repository_info",
                        arguments={},
                    )
                ]
            )
        return ToolAssistantTurn(content=self.final_answer)

    async def stream(
        self, _messages: Sequence[ChatMessage]
    ) -> AsyncGenerator[str, None]:
        raise AssertionError("streaming is not used by the agent endpoint")
        yield ""


def test_agent_endpoint_completes_tool_loop() -> None:
    provider = AgentStubProvider()
    with patch("app.api.agent.get_llm_provider", return_value=provider):
        response = client.post(
            "/api/agent",
            json={
                "messages": [
                    {"role": "user", "content": "当前项目使用什么技术栈？"}
                ]
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "message": {
            "role": "assistant",
            "content": "项目技术栈来自工具结果。",
        },
        "model": "agent-stub",
    }
    assert provider.call_count == 2


def test_agent_endpoint_validates_before_resolving_provider() -> None:
    with patch(
        "app.api.agent.get_llm_provider",
        side_effect=LLMConfigurationError("LLM is not configured."),
    ) as provider_resolver:
        response = client.post("/api/agent", json={"messages": []})

    assert response.status_code == 422
    provider_resolver.assert_not_called()


def test_agent_endpoint_returns_configuration_error() -> None:
    with patch(
        "app.api.agent.get_llm_provider",
        side_effect=LLMConfigurationError("LLM is not configured."),
    ):
        response = client.post(
            "/api/agent",
            json={"messages": [{"role": "user", "content": "技术栈？"}]},
        )

    assert response.status_code == 503
    assert response.json() == {"detail": "LLM is not configured."}


def test_agent_endpoint_rejects_oversized_final_answer() -> None:
    provider = AgentStubProvider(final_answer="x" * 20_001)
    with patch("app.api.agent.get_llm_provider", return_value=provider):
        response = client.post(
            "/api/agent",
            json={"messages": [{"role": "user", "content": "技术栈？"}]},
        )

    assert response.status_code == 502
    assert response.json() == {
        "detail": (
            "The configured LLM provider returned an invalid assistant message."
        )
    }
