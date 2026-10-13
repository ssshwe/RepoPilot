from pydantic import ValidationError

from app.core.exceptions import LLMProviderError
from app.llm.tool_types import FunctionToolDefinition, ToolCall
from app.tools.repository import (
    GET_REPOSITORY_INFO_DEFINITION,
    GetRepositoryInfoArguments,
    get_repository_info,
)


AVAILABLE_TOOLS: tuple[FunctionToolDefinition, ...] = (
    GET_REPOSITORY_INFO_DEFINITION,
)


def execute_tool_call(call: ToolCall) -> str:
    """Validate and dispatch one model-requested call to an allowlisted tool."""
    if call.name != GET_REPOSITORY_INFO_DEFINITION.name:
        raise LLMProviderError("The LLM requested an unsupported tool.")

    try:
        GetRepositoryInfoArguments.model_validate(call.arguments)
    except ValidationError as exc:
        raise LLMProviderError(
            "The LLM requested invalid tool arguments."
        ) from exc

    return get_repository_info().model_dump_json()
