import asyncio
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

import pytest
from httpx import Request
from openai import APIError
from openai import AsyncOpenAI

from app.core.config import Settings
from app.core.exceptions import LLMConfigurationError, LLMProviderError
from app.llm.compatible_provider import OpenAICompatibleProvider
from app.llm.factory import ProviderFactory
from app.llm.tool_types import (
    FunctionToolDefinition,
    ToolLoopMessage,
)
from app.schemas.analysis import RequirementAnalysis
from app.schemas.chat import ChatMessage


class FakeStream:
    def __init__(
        self,
        chunks: list[str | None],
        *,
        fail_after: int | None = None,
    ) -> None:
        self.chunks = chunks
        self.fail_after = fail_after
        self.index = 0
        self.closed = False

    def __aiter__(self) -> "FakeStream":
        return self

    async def __aenter__(self) -> "FakeStream":
        return self

    async def __aexit__(
        self,
        _exception_type: object,
        _exception: object,
        _traceback: object,
    ) -> None:
        self.closed = True

    async def __anext__(self) -> Any:
        if self.fail_after is not None and self.index == self.fail_after:
            raise APIError(
                "private upstream failure",
                Request("POST", "https://example.test/v1/chat/completions"),
                body=None,
            )
        if self.index >= len(self.chunks):
            raise StopAsyncIteration

        content = self.chunks[self.index]
        self.index += 1
        return SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(content=content))]
        )


class FakeCompletions:
    def __init__(
        self,
        content: str | None = "你好！",
        *,
        stream_chunks: list[str | None] | None = None,
        stream_fail_after: int | None = None,
        tool_calls: list[Any] | None = None,
    ) -> None:
        self.content = content
        self.stream_chunks = (
            stream_chunks if stream_chunks is not None else ["你", "好！"]
        )
        self.stream_fail_after = stream_fail_after
        self.tool_calls = tool_calls
        self.arguments: dict[str, Any] = {}
        self.last_stream: FakeStream | None = None

    async def create(self, **kwargs: Any) -> Any:
        self.arguments = kwargs
        if kwargs.get("stream") is True:
            self.last_stream = FakeStream(
                self.stream_chunks,
                fail_after=self.stream_fail_after,
            )
            return self.last_stream
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=self.content,
                        tool_calls=self.tool_calls,
                    )
                )
            ]
        )


class FakeClient:
    def __init__(
        self,
        content: str | None = "你好！",
        *,
        stream_chunks: list[str | None] | None = None,
        stream_fail_after: int | None = None,
        tool_calls: list[Any] | None = None,
    ) -> None:
        self.completions = FakeCompletions(
            content,
            stream_chunks=stream_chunks,
            stream_fail_after=stream_fail_after,
            tool_calls=tool_calls,
        )
        self.chat = SimpleNamespace(completions=self.completions)


def test_compatible_provider_bypasses_system_proxy_for_loopback() -> None:
    fake_transport = object()

    with (
        patch(
            "app.llm.compatible_provider.DefaultAsyncHttpxClient",
            return_value=fake_transport,
        ) as transport_factory,
        patch("app.llm.compatible_provider.AsyncOpenAI") as client_factory,
    ):
        OpenAICompatibleProvider(
            api_key="ollama",
            base_url="http://127.0.0.1:11434/v1/",
            model="qwen2.5:0.5b",
            timeout_seconds=120,
        )

    transport_factory.assert_called_once_with(trust_env=False)
    client_factory.assert_called_once_with(
        api_key="ollama",
        base_url="http://127.0.0.1:11434/v1",
        timeout=120,
        http_client=fake_transport,
    )


def test_compatible_provider_sends_non_streaming_chat_completion() -> None:
    fake_client = FakeClient()
    provider = OpenAICompatibleProvider(
        api_key="test-key",
        base_url="https://example.test/v1",
        model="test-model",
        timeout_seconds=30,
        client=cast(AsyncOpenAI, fake_client),
    )

    messages = [
        ChatMessage(role="system", content="你是一名编程导师。"),
        ChatMessage(role="user", content="Service 是什么？"),
        ChatMessage(role="assistant", content="Service 承载业务逻辑。"),
        ChatMessage(role="user", content="那 Controller 呢？"),
    ]

    answer = asyncio.run(provider.complete(messages))

    assert answer == "你好！"
    assert fake_client.completions.arguments == {
        "model": "test-model",
        "messages": [message.model_dump() for message in messages],
        "stream": False,
    }


def test_compatible_provider_rejects_empty_text() -> None:
    provider = OpenAICompatibleProvider(
        api_key="test-key",
        base_url="https://example.test/v1",
        model="test-model",
        timeout_seconds=30,
        client=cast(AsyncOpenAI, FakeClient(content="   ")),
    )

    with pytest.raises(LLMProviderError, match="returned no text"):
        asyncio.run(
            provider.complete([ChatMessage(role="user", content="你好")])
        )


def test_compatible_provider_requests_and_validates_structured_output() -> None:
    content = (
        '{"summary":"实现登录功能","difficulty":"medium",'
        '"tasks":["设计接口","实现认证"],"risks":["密码泄露"]}'
    )
    fake_client = FakeClient(content=content)
    provider = OpenAICompatibleProvider(
        api_key="test-key",
        base_url="https://example.test/v1",
        model="test-model",
        timeout_seconds=30,
        client=cast(AsyncOpenAI, fake_client),
    )
    messages = [ChatMessage(role="user", content="实现登录功能")]

    result = asyncio.run(
        provider.complete_structured(messages, RequirementAnalysis)
    )

    assert result == RequirementAnalysis(
        summary="实现登录功能",
        difficulty="medium",
        tasks=["设计接口", "实现认证"],
        risks=["密码泄露"],
    )
    arguments = fake_client.completions.arguments
    assert arguments["model"] == "test-model"
    assert arguments["messages"] == [message.model_dump() for message in messages]
    assert arguments["stream"] is False
    assert arguments["temperature"] == 0
    assert arguments["max_tokens"] == 512
    assert arguments["response_format"]["type"] == "json_schema"
    assert arguments["response_format"]["json_schema"] == {
        "name": "RequirementAnalysis",
        "strict": True,
        "schema": {
            "additionalProperties": False,
            "properties": {
                "summary": {"type": "string"},
                "difficulty": {
                    "enum": ["easy", "medium", "hard"],
                    "type": "string",
                },
                "tasks": {
                    "items": {"type": "string"},
                    "type": "array",
                },
                "risks": {
                    "items": {"type": "string"},
                    "type": "array",
                },
            },
            "required": ["summary", "difficulty", "tasks", "risks"],
            "type": "object",
        },
    }


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        '{"summary":"缺少字段","difficulty":"easy","tasks":["实现"]}',
        (
            '{"summary":"错误枚举","difficulty":"unknown",'
            '"tasks":["实现"],"risks":[]}'
        ),
        (
            '{"summary":"额外字段","difficulty":"easy",'
            '"tasks":["实现"],"risks":[],"unexpected":true}'
        ),
    ],
)
def test_compatible_provider_rejects_invalid_structured_output(
    content: str,
) -> None:
    provider = OpenAICompatibleProvider(
        api_key="test-key",
        base_url="https://example.test/v1",
        model="test-model",
        timeout_seconds=30,
        client=cast(AsyncOpenAI, FakeClient(content=content)),
    )

    with pytest.raises(LLMProviderError, match="invalid structured output"):
        asyncio.run(
            provider.complete_structured(
                [ChatMessage(role="user", content="测试")],
                RequirementAnalysis,
            )
        )


def test_compatible_provider_parses_tool_call() -> None:
    raw_call = SimpleNamespace(
        id="call-1",
        function=SimpleNamespace(
            name="get_repository_info",
            arguments="{}",
        ),
    )
    fake_client = FakeClient(content="", tool_calls=[raw_call])
    provider = OpenAICompatibleProvider(
        api_key="test-key",
        base_url="https://example.test/v1",
        model="test-model",
        timeout_seconds=30,
        client=cast(AsyncOpenAI, fake_client),
    )
    messages = [ToolLoopMessage(role="user", content="项目技术栈？")]
    tools = [
        FunctionToolDefinition(
            name="get_repository_info",
            description="Get repository information.",
            parameters={"type": "object", "properties": {}},
        )
    ]

    turn = asyncio.run(provider.complete_with_tools(messages, tools))

    assert turn.model_dump() == {
        "content": "",
        "tool_calls": [
            {
                "id": "call-1",
                "name": "get_repository_info",
                "arguments": {},
            }
        ],
    }
    assert fake_client.completions.arguments == {
        "model": "test-model",
        "messages": [{"role": "user", "content": "项目技术栈？"}],
        "tools": [tool.to_provider_tool() for tool in tools],
        "tool_choice": "auto",
        "max_tokens": 512,
        "stream": False,
        "temperature": 0,
    }


@pytest.mark.parametrize("arguments", ["not-json", "[]"])
def test_compatible_provider_rejects_invalid_tool_arguments(
    arguments: str,
) -> None:
    raw_call = SimpleNamespace(
        id="call-1",
        function=SimpleNamespace(
            name="get_repository_info",
            arguments=arguments,
        ),
    )
    provider = OpenAICompatibleProvider(
        api_key="test-key",
        base_url="https://example.test/v1",
        model="test-model",
        timeout_seconds=30,
        client=cast(
            AsyncOpenAI,
            FakeClient(content="", tool_calls=[raw_call]),
        ),
    )

    with pytest.raises(LLMProviderError, match="invalid tool call"):
        asyncio.run(
            provider.complete_with_tools(
                [ToolLoopMessage(role="user", content="项目技术栈？")],
                [],
            )
        )


def test_compatible_provider_rejects_empty_tool_response() -> None:
    provider = OpenAICompatibleProvider(
        api_key="test-key",
        base_url="https://example.test/v1",
        model="test-model",
        timeout_seconds=30,
        client=cast(AsyncOpenAI, FakeClient(content=" ", tool_calls=[])),
    )

    with pytest.raises(LLMProviderError, match="no final answer"):
        asyncio.run(
            provider.complete_with_tools(
                [ToolLoopMessage(role="user", content="你好")],
                [],
            )
        )


def test_compatible_provider_streams_chat_completion_chunks() -> None:
    fake_client = FakeClient(stream_chunks=["你", None, "好", "！"])
    provider = OpenAICompatibleProvider(
        api_key="test-key",
        base_url="https://example.test/v1",
        model="test-model",
        timeout_seconds=30,
        client=cast(AsyncOpenAI, fake_client),
    )
    messages = [
        ChatMessage(role="system", content="你是一名编程导师。"),
        ChatMessage(role="user", content="你好"),
    ]

    async def collect() -> list[str]:
        return [chunk async for chunk in provider.stream(messages)]

    chunks = asyncio.run(collect())

    assert chunks == ["你", "好", "！"]
    assert fake_client.completions.arguments == {
        "model": "test-model",
        "messages": [message.model_dump() for message in messages],
        "stream": True,
    }
    assert fake_client.completions.last_stream is not None
    assert fake_client.completions.last_stream.closed is True


def test_compatible_provider_rejects_whitespace_only_stream() -> None:
    fake_client = FakeClient(stream_chunks=[" ", "\n"])
    provider = OpenAICompatibleProvider(
        api_key="test-key",
        base_url="https://example.test/v1",
        model="test-model",
        timeout_seconds=30,
        client=cast(
            AsyncOpenAI,
            fake_client,
        ),
    )

    async def collect() -> list[str]:
        return [
            chunk
            async for chunk in provider.stream(
                [ChatMessage(role="user", content="你好")]
            )
        ]

    with pytest.raises(LLMProviderError, match="returned no text"):
        asyncio.run(collect())
    assert fake_client.completions.last_stream is not None
    assert fake_client.completions.last_stream.closed is True


def test_compatible_provider_hides_midstream_api_error() -> None:
    fake_client = FakeClient(
        stream_chunks=["部分", "回答"], stream_fail_after=1
    )
    provider = OpenAICompatibleProvider(
        api_key="test-key",
        base_url="https://example.test/v1",
        model="test-model",
        timeout_seconds=30,
        client=cast(
            AsyncOpenAI,
            fake_client,
        ),
    )

    async def collect() -> list[str]:
        return [
            chunk
            async for chunk in provider.stream(
                [ChatMessage(role="user", content="你好")]
            )
        ]

    with pytest.raises(
        LLMProviderError,
        match="The configured LLM provider request failed.",
    ) as error:
        asyncio.run(collect())

    assert "private upstream failure" not in str(error.value)
    assert fake_client.completions.last_stream is not None
    assert fake_client.completions.last_stream.closed is True


def test_compatible_provider_rejects_stream_with_no_chunks() -> None:
    fake_client = FakeClient(stream_chunks=[])
    provider = OpenAICompatibleProvider(
        api_key="test-key",
        base_url="https://example.test/v1",
        model="test-model",
        timeout_seconds=30,
        client=cast(AsyncOpenAI, fake_client),
    )

    async def collect() -> list[str]:
        return [
            chunk
            async for chunk in provider.stream(
                [ChatMessage(role="user", content="你好")]
            )
        ]

    with pytest.raises(LLMProviderError, match="returned no text"):
        asyncio.run(collect())
    assert fake_client.completions.last_stream is not None
    assert fake_client.completions.last_stream.closed is True


def test_compatible_provider_closes_upstream_when_consumer_stops_early() -> None:
    fake_client = FakeClient(stream_chunks=["first", "second"])
    provider = OpenAICompatibleProvider(
        api_key="test-key",
        base_url="https://example.test/v1",
        model="test-model",
        timeout_seconds=30,
        client=cast(AsyncOpenAI, fake_client),
    )

    async def consume_one_chunk() -> str:
        stream = provider.stream([ChatMessage(role="user", content="你好")])
        first_chunk = await anext(stream)
        await stream.aclose()
        return first_chunk

    assert asyncio.run(consume_one_chunk()) == "first"
    assert fake_client.completions.last_stream is not None
    assert fake_client.completions.last_stream.closed is True


def test_factory_requires_credentials_and_model() -> None:
    settings = Settings(
        _env_file=None,
        llm_provider="openai_compatible",
        llm_api_key="",
        llm_model="",
        llm_base_url="",
    )

    with pytest.raises(LLMConfigurationError, match="LLM_API_KEY"):
        ProviderFactory.create(settings)


def test_factory_requires_base_url_for_compatible_provider() -> None:
    settings = Settings(
        _env_file=None,
        llm_provider="openai_compatible",
        llm_api_key="test-key",
        llm_model="test-model",
        llm_base_url="",
    )

    with pytest.raises(LLMConfigurationError, match="LLM_BASE_URL"):
        ProviderFactory.create(settings)


def test_factory_creates_official_openai_provider() -> None:
    settings = Settings(
        _env_file=None,
        llm_provider=" OpenAI ",
        llm_api_key="test-key",
        llm_model="test-model",
        llm_timeout_seconds=45,
    )

    with patch("app.llm.factory.OpenAIProvider") as provider_type:
        provider = ProviderFactory.create(settings)

    assert provider is provider_type.return_value
    provider_type.assert_called_once_with(
        api_key="test-key",
        model="test-model",
        timeout_seconds=45,
    )


def test_factory_creates_openai_compatible_provider() -> None:
    settings = Settings(
        _env_file=None,
        llm_provider="openai_compatible",
        llm_api_key="ollama",
        llm_base_url=" http://127.0.0.1:11434/v1 ",
        llm_model="qwen2.5:0.5b",
        llm_timeout_seconds=90,
    )

    with patch("app.llm.factory.OpenAICompatibleProvider") as provider_type:
        provider = ProviderFactory.create(settings)

    assert provider is provider_type.return_value
    provider_type.assert_called_once_with(
        api_key="ollama",
        base_url="http://127.0.0.1:11434/v1",
        model="qwen2.5:0.5b",
        timeout_seconds=90,
    )


def test_factory_rejects_unsupported_provider() -> None:
    settings = Settings(
        _env_file=None,
        llm_provider="unknown",
        llm_api_key="test-key",
        llm_model="test-model",
    )

    with pytest.raises(LLMConfigurationError, match="Unsupported LLM_PROVIDER"):
        ProviderFactory.create(settings)


def test_settings_repr_does_not_reveal_api_key() -> None:
    secret = "stage-1-test-secret"
    settings = Settings(
        _env_file=None,
        llm_api_key=secret,
    )

    assert secret not in repr(settings)
    assert settings.llm_api_key.get_secret_value() == secret
