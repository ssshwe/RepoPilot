import json

import pytest

from app.core.exceptions import LLMProviderError
from app.llm.tool_types import ToolCall
from app.tools.registry import AVAILABLE_TOOLS, execute_tool_call
from app.tools.repository import get_repository_info


def test_get_repository_info_returns_stage_1_fixture() -> None:
    assert get_repository_info().model_dump() == {
        "name": "smart-community",
        "frontend": "Vue 3",
        "backend": "Spring Boot",
        "database": "MySQL",
    }


def test_repository_tool_schema_has_no_arguments() -> None:
    assert [tool.to_provider_tool() for tool in AVAILABLE_TOOLS] == [
        {
            "type": "function",
            "function": {
                "name": "get_repository_info",
                "description": (
                    "获取当前项目名称和技术栈。此工具没有任何参数，调用时必须"
                    "传入空对象 {}。不得猜测结果。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            },
        }
    ]


def test_execute_tool_call_returns_json_result() -> None:
    result = execute_tool_call(
        ToolCall(id="call-1", name="get_repository_info", arguments={})
    )

    assert json.loads(result) == get_repository_info().model_dump()


def test_execute_tool_call_rejects_unknown_tool() -> None:
    with pytest.raises(LLMProviderError, match="unsupported tool"):
        execute_tool_call(ToolCall(id="call-1", name="run_shell", arguments={}))


def test_execute_tool_call_validates_arguments() -> None:
    with pytest.raises(LLMProviderError, match="invalid tool arguments"):
        execute_tool_call(
            ToolCall(
                id="call-1",
                name="get_repository_info",
                arguments={"path": "C:\\"},
            )
        )
