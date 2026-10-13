import asyncio
import json
from collections.abc import AsyncGenerator, Sequence
from typing import TypeVar

import pytest
from pydantic import BaseModel

from app.agent.tool_loop import (
    MAX_TOOL_ROUNDS,
    TOOL_AGENT_SYSTEM_PROMPT,
    ToolCallingAgent,
)
from app.core.exceptions import LLMProviderError
from app.llm.base import LLMProvider
from app.llm.tool_types import (
    FunctionToolDefinition,
    ToolAssistantTurn,
    ToolCall,
    ToolLoopMessage,
)
from app.schemas.chat import ChatMessage
from app.tools.repository import get_repository_info


ResponseModelT = TypeVar("ResponseModelT", bound=BaseModel)


class ToolLoopStubProvider(LLMProvider):
    def __init__(self, turns: list[ToolAssistantTurn]) -> None:
        self.turns = turns
        self.requests: list[
            tuple[list[ToolLoopMessage], list[FunctionToolDefinition]]
        ] = []

    @property
    def model_name(self) -> str:
        return "tool-loop-stub"

    async def complete(self, _messages: Sequence[ChatMessage]) -> str:
        raise AssertionError("plain completion is not used by the tool loop")

    async def complete_structured(
        self,
        _messages: Sequence[ChatMessage],
        _response_model: type[ResponseModelT],
    ) -> ResponseModelT:
        raise AssertionError("structured completion is not used by the tool loop")

    async def complete_with_tools(
        self,
        messages: Sequence[ToolLoopMessage],
        tools: Sequence[FunctionToolDefinition],
    ) -> ToolAssistantTurn:
        self.requests.append((list(messages), list(tools)))
        if not self.turns:
            raise AssertionError("stub has no remaining turns")
        return self.turns.pop(0)

    async def stream(
        self, _messages: Sequence[ChatMessage]
    ) -> AsyncGenerator[str, None]:
        raise AssertionError("streaming is not used by the tool loop")
        yield ""


def repository_call(call_id: str = "call-1") -> ToolCall:
    return ToolCall(
        id=call_id,
        name="get_repository_info",
        arguments={},
    )


def test_tool_loop_sends_result_back_to_llm_before_final_answer() -> None:
    provider = ToolLoopStubProvider(
        [
            ToolAssistantTurn(tool_calls=[repository_call()]),
            ToolAssistantTurn(
                content=(
                    "smart-community 使用 Vue 3、Spring Boot 和 MySQL。"
                )
            ),
        ]
    )
    messages = [ChatMessage(role="user", content="当前项目使用什么技术栈？")]

    answer = asyncio.run(ToolCallingAgent(provider).run(messages))

    assert answer == "smart-community 使用 Vue 3、Spring Boot 和 MySQL。"
    assert len(provider.requests) == 2
    first_history, first_tools = provider.requests[0]
    assert [message.model_dump() for message in first_history] == [
        {
            "role": "system",
            "content": TOOL_AGENT_SYSTEM_PROMPT,
            "tool_calls": [],
            "tool_call_id": None,
        },
        {
            "role": "user",
            "content": "当前项目使用什么技术栈？",
            "tool_calls": [],
            "tool_call_id": None,
        },
    ]
    assert [tool.name for tool in first_tools] == ["get_repository_info"]

    second_history, _second_tools = provider.requests[1]
    assert [message.role for message in second_history] == [
        "system",
        "user",
        "assistant",
        "tool",
    ]
    assert second_history[2].tool_calls == [repository_call()]
    assert second_history[3].tool_call_id == "call-1"
    assert json.loads(second_history[3].content or "") == (
        get_repository_info().model_dump()
    )


def test_tool_loop_can_return_without_calling_a_tool() -> None:
    provider = ToolLoopStubProvider([ToolAssistantTurn(content="你好！")])

    answer = asyncio.run(
        ToolCallingAgent(provider).run(
            [ChatMessage(role="user", content="你好")]
        )
    )

    assert answer == "你好！"
    assert len(provider.requests) == 1


def test_tool_loop_blocks_unregistered_tool() -> None:
    provider = ToolLoopStubProvider(
        [
            ToolAssistantTurn(
                tool_calls=[ToolCall(id="call-1", name="run_shell", arguments={})]
            )
        ]
    )

    with pytest.raises(LLMProviderError, match="unsupported tool"):
        asyncio.run(
            ToolCallingAgent(provider).run(
                [ChatMessage(role="user", content="运行命令")]
            )
        )


def test_tool_loop_has_a_hard_round_limit() -> None:
    provider = ToolLoopStubProvider(
        [
            ToolAssistantTurn(tool_calls=[repository_call(f"call-{index}")])
            for index in range(MAX_TOOL_ROUNDS)
        ]
    )

    with pytest.raises(LLMProviderError, match="round limit"):
        asyncio.run(
            ToolCallingAgent(provider).run(
                [ChatMessage(role="user", content="项目技术栈？")]
            )
        )

    assert len(provider.requests) == MAX_TOOL_ROUNDS
