from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator, Sequence
from typing import TypeVar

from pydantic import BaseModel

from app.llm.tool_types import (
    FunctionToolDefinition,
    ToolAssistantTurn,
    ToolLoopMessage,
)
from app.schemas.chat import ChatMessage


StructuredResponseT = TypeVar("StructuredResponseT", bound=BaseModel)


class LLMProvider(ABC):
    """Small provider contract used by application routes."""

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Return the configured model identifier."""

    @abstractmethod
    async def complete(self, messages: Sequence[ChatMessage]) -> str:
        """Generate one non-streaming response from the supplied conversation."""

    @abstractmethod
    async def complete_structured(
        self,
        messages: Sequence[ChatMessage],
        response_model: type[StructuredResponseT],
    ) -> StructuredResponseT:
        """Generate and validate one response against a Pydantic model."""

    @abstractmethod
    async def complete_with_tools(
        self,
        messages: Sequence[ToolLoopMessage],
        tools: Sequence[FunctionToolDefinition],
    ) -> ToolAssistantTurn:
        """Return either an assistant answer or validated function calls."""

    @abstractmethod
    def stream(
        self, messages: Sequence[ChatMessage]
    ) -> AsyncGenerator[str, None]:
        """Yield text fragments as the provider produces them."""
