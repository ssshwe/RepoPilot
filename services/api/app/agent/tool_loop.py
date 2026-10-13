from collections.abc import Sequence

from app.core.exceptions import LLMProviderError
from app.llm.base import LLMProvider
from app.llm.tool_types import ToolLoopMessage
from app.schemas.chat import ChatMessage
from app.tools.registry import AVAILABLE_TOOLS, execute_tool_call


MAX_TOOL_ROUNDS = 4
TOOL_AGENT_SYSTEM_PROMPT = """你是 RepoPilot。用户询问项目、仓库或技术栈时，必须调用 get_repository_info。
该工具没有参数，arguments 必须严格为 {}。不要猜测工具结果。
收到 tool 消息后，直接根据其内容回答，不再调用工具。"""


class ToolCallingAgent:
    """Run the native LLM -> tool -> LLM loop without an agent framework."""

    def __init__(self, provider: LLMProvider) -> None:
        self._provider = provider

    async def run(self, messages: Sequence[ChatMessage]) -> str:
        history = [
            ToolLoopMessage(role="system", content=TOOL_AGENT_SYSTEM_PROMPT),
            *[
                ToolLoopMessage(role=message.role, content=message.content)
                for message in messages
            ],
        ]

        for _round in range(MAX_TOOL_ROUNDS):
            turn = await self._provider.complete_with_tools(
                history,
                AVAILABLE_TOOLS,
            )
            history.append(
                ToolLoopMessage(
                    role="assistant",
                    content=turn.content,
                    tool_calls=turn.tool_calls,
                )
            )

            if not turn.tool_calls:
                if not isinstance(turn.content, str) or not turn.content.strip():
                    raise LLMProviderError(
                        "The configured LLM provider returned no final answer."
                    )
                return turn.content

            for call in turn.tool_calls:
                history.append(
                    ToolLoopMessage(
                        role="tool",
                        content=execute_tool_call(call),
                        tool_call_id=call.id,
                    )
                )

        raise LLMProviderError("The LLM exceeded the tool-call round limit.")
