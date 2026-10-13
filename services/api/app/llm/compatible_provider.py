import json
from collections.abc import AsyncGenerator, Sequence
from urllib.parse import urlparse

from openai import APIError, AsyncOpenAI, DefaultAsyncHttpxClient
from pydantic import ValidationError

from app.core.exceptions import LLMProviderError
from app.llm.base import LLMProvider, StructuredResponseT
from app.llm.tool_types import (
    FunctionToolDefinition,
    ToolAssistantTurn,
    ToolCall,
    ToolLoopMessage,
)
from app.schemas.chat import ChatMessage


_UNSUPPORTED_GRAMMAR_KEYWORDS = frozenset(
    {"maxItems", "maxLength", "minItems", "minLength", "title"}
)


def _provider_json_schema(value: object) -> object:
    """Keep the Pydantic schema subset accepted by compatible providers.

    Ollama's grammar parser rejects several validation-only JSON Schema
    keywords. RepoPilot still applies the complete Pydantic model locally after
    generation, so removing them here does not weaken the API boundary.
    """
    if isinstance(value, dict):
        return {
            key: _provider_json_schema(item)
            for key, item in value.items()
            if key not in _UNSUPPORTED_GRAMMAR_KEYWORDS
        }
    if isinstance(value, list):
        return [_provider_json_schema(item) for item in value]
    return value


def _is_loopback_url(base_url: str) -> bool:
    return urlparse(base_url).hostname in {"127.0.0.1", "localhost", "::1"}


class OpenAICompatibleProvider(LLMProvider):
    """Chat Completions provider for APIs that follow the OpenAI protocol."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: float,
        client: AsyncOpenAI | None = None,
    ) -> None:
        self._model = model
        if client is not None:
            self._client = client
        elif _is_loopback_url(base_url):
            # A local inference server must never be sent through an inherited
            # corporate/system proxy. Besides leaking local traffic, that can
            # turn a healthy Ollama response into an unrelated proxy 502.
            self._client = AsyncOpenAI(
                api_key=api_key,
                base_url=base_url.rstrip("/"),
                timeout=timeout_seconds,
                http_client=DefaultAsyncHttpxClient(trust_env=False),
            )
        else:
            self._client = AsyncOpenAI(
                api_key=api_key,
                base_url=base_url.rstrip("/"),
                timeout=timeout_seconds,
            )

    @property
    def model_name(self) -> str:
        return self._model

    async def complete(self, messages: Sequence[ChatMessage]) -> str:
        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": message.role, "content": message.content}
                    for message in messages
                ],
                stream=False,
            )
        except APIError as exc:
            raise LLMProviderError(
                "The configured LLM provider request failed."
            ) from exc

        content = response.choices[0].message.content if response.choices else None
        if not isinstance(content, str) or not content.strip():
            raise LLMProviderError("The configured LLM provider returned no text.")

        return content

    async def complete_structured(
        self,
        messages: Sequence[ChatMessage],
        response_model: type[StructuredResponseT],
    ) -> StructuredResponseT:
        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": message.role, "content": message.content}
                    for message in messages
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": response_model.__name__,
                        "strict": True,
                        "schema": _provider_json_schema(
                            response_model.model_json_schema()
                        ),
                    },
                },
                max_tokens=512,
                stream=False,
                temperature=0,
            )
        except APIError as exc:
            raise LLMProviderError(
                "The configured LLM provider request failed."
            ) from exc

        content = response.choices[0].message.content if response.choices else None
        if not isinstance(content, str) or not content.strip():
            raise LLMProviderError(
                "The configured LLM provider returned invalid structured output."
            )

        try:
            return response_model.model_validate_json(content)
        except ValidationError as exc:
            raise LLMProviderError(
                "The configured LLM provider returned invalid structured output."
            ) from exc

    async def complete_with_tools(
        self,
        messages: Sequence[ToolLoopMessage],
        tools: Sequence[FunctionToolDefinition],
    ) -> ToolAssistantTurn:
        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[message.to_provider_message() for message in messages],
                tools=[tool.to_provider_tool() for tool in tools],
                tool_choice="auto",
                max_tokens=512,
                stream=False,
                temperature=0,
            )
        except APIError as exc:
            raise LLMProviderError(
                "The configured LLM provider request failed."
            ) from exc

        if not response.choices:
            raise LLMProviderError(
                "The configured LLM provider returned no tool response."
            )

        message = response.choices[0].message
        calls: list[ToolCall] = []
        try:
            for raw_call in message.tool_calls or []:
                raw_arguments = raw_call.function.arguments
                arguments = (
                    json.loads(raw_arguments)
                    if isinstance(raw_arguments, str)
                    else raw_arguments
                )
                if not isinstance(arguments, dict):
                    raise ValueError("tool arguments must be an object")
                calls.append(
                    ToolCall(
                        id=raw_call.id,
                        name=raw_call.function.name,
                        arguments=arguments,
                    )
                )
        except (
            AttributeError,
            json.JSONDecodeError,
            TypeError,
            ValueError,
            ValidationError,
        ) as exc:
            raise LLMProviderError(
                "The configured LLM provider returned an invalid tool call."
            ) from exc

        content = message.content if isinstance(message.content, str) else None
        if not calls and (content is None or not content.strip()):
            raise LLMProviderError(
                "The configured LLM provider returned no final answer."
            )
        return ToolAssistantTurn(content=content, tool_calls=calls)

    async def stream(
        self, messages: Sequence[ChatMessage]
    ) -> AsyncGenerator[str, None]:
        has_non_blank_text = False

        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": message.role, "content": message.content}
                    for message in messages
                ],
                stream=True,
            )

            async with response:
                async for chunk in response:
                    if not chunk.choices:
                        continue

                    content = chunk.choices[0].delta.content
                    if not isinstance(content, str) or not content:
                        continue

                    if content.strip():
                        has_non_blank_text = True
                    yield content
        except APIError as exc:
            raise LLMProviderError(
                "The configured LLM provider request failed."
            ) from exc

        if not has_non_blank_text:
            raise LLMProviderError("The configured LLM provider returned no text.")
