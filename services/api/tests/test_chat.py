import json
from collections.abc import AsyncGenerator, Sequence
from typing import Any
from unittest.mock import patch

from fastapi.testclient import TestClient
from httpx import Response
from pydantic import BaseModel

from app.core.exceptions import LLMConfigurationError, LLMProviderError
from app.llm.base import LLMProvider
from app.llm.tool_types import (
    FunctionToolDefinition,
    ToolAssistantTurn,
    ToolLoopMessage,
)
from app.main import app
from app.schemas.chat import ChatMessage


client = TestClient(app)


class StubProvider(LLMProvider):
    def __init__(
        self,
        *,
        should_fail: bool = False,
        answer: str | None = None,
        stream_chunks: list[str] | None = None,
        fail_stream_after_chunks: bool = False,
        fail_stream_unexpectedly: bool = False,
    ) -> None:
        self.should_fail = should_fail
        self.answer = answer
        self.stream_chunks = stream_chunks
        self.fail_stream_after_chunks = fail_stream_after_chunks
        self.fail_stream_unexpectedly = fail_stream_unexpectedly
        self.received_messages: list[ChatMessage] = []

    @property
    def model_name(self) -> str:
        return "stub-model"

    async def complete(self, messages: Sequence[ChatMessage]) -> str:
        self.received_messages = list(messages)
        if self.should_fail:
            raise LLMProviderError("The configured LLM provider request failed.")
        if self.answer is not None:
            return self.answer
        return f"模型收到：{messages[-1].content}"

    async def complete_structured(
        self,
        _messages: Sequence[ChatMessage],
        _response_model: type[BaseModel],
    ) -> BaseModel:
        raise AssertionError("structured completion is not used by chat tests")

    async def complete_with_tools(
        self,
        _messages: Sequence[ToolLoopMessage],
        _tools: Sequence[FunctionToolDefinition],
    ) -> ToolAssistantTurn:
        raise AssertionError("tool completion is not used by chat tests")

    async def stream(
        self, messages: Sequence[ChatMessage]
    ) -> AsyncGenerator[str, None]:
        self.received_messages = list(messages)
        if self.should_fail:
            raise LLMProviderError("The configured LLM provider request failed.")

        chunks = self.stream_chunks
        if chunks is None:
            chunks = [self.answer or f"模型收到：{messages[-1].content}"]

        for chunk in chunks:
            yield chunk

        if self.fail_stream_after_chunks:
            raise LLMProviderError("The configured LLM provider request failed.")
        if self.fail_stream_unexpectedly:
            raise RuntimeError("private provider detail")


def post_chat(payload: dict[str, Any], provider: StubProvider) -> Response:
    with patch("app.api.chat.get_llm_provider", return_value=provider):
        return client.post("/api/chat", json=payload)


def post_chat_stream(payload: dict[str, Any], provider: StubProvider) -> Response:
    with patch("app.api.chat.get_llm_provider", return_value=provider):
        return client.post(
            "/api/chat/stream",
            json=payload,
            headers={"Accept": "text/event-stream"},
        )


def parse_sse(body: str) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    normalized = body.replace("\r\n", "\n").strip()

    for block in normalized.split("\n\n"):
        event_name = "message"
        data_lines: list[str] = []
        for line in block.splitlines():
            if line.startswith("event:"):
                event_name = line.removeprefix("event:").strip()
            elif line.startswith("data:"):
                data_lines.append(line.removeprefix("data:").lstrip())

        events.append((event_name, json.loads("\n".join(data_lines))))

    return events


def test_chat_forwards_multi_turn_context_and_returns_assistant_message() -> None:
    provider = StubProvider()

    messages = [
        {"role": "system", "content": "你是一名编程导师。"},
        {"role": "user", "content": "Spring Boot 的 Service 是什么？"},
        {"role": "assistant", "content": "Service 用于承载业务逻辑。"},
        {"role": "user", "content": "那 Controller 呢？"},
    ]

    response = post_chat({"messages": messages}, provider)

    assert response.status_code == 200
    assert response.json() == {
        "message": {
            "role": "assistant",
            "content": "模型收到：那 Controller 呢？",
        },
        "model": "stub-model",
    }
    assert [message.model_dump() for message in provider.received_messages] == messages


def test_chat_rejects_blank_message_content() -> None:
    response = post_chat(
        {"messages": [{"role": "user", "content": "   "}]},
        StubProvider(),
    )

    assert response.status_code == 422


def test_chat_rejects_empty_message_list() -> None:
    with patch(
        "app.api.chat.get_llm_provider",
        side_effect=LLMConfigurationError("LLM is not configured."),
    ) as provider_resolver:
        response = client.post("/api/chat", json={"messages": []})

    assert response.status_code == 422
    provider_resolver.assert_not_called()


def test_chat_rejects_task_2_legacy_request_shape() -> None:
    with patch(
        "app.api.chat.get_llm_provider",
        side_effect=LLMConfigurationError("LLM is not configured."),
    ) as provider_resolver:
        response = client.post("/api/chat", json={"message": "你好"})

    assert response.status_code == 422
    provider_resolver.assert_not_called()


def test_chat_preserves_message_whitespace() -> None:
    content = "  请解释：\n    if ready:\n        run()  "
    message = ChatMessage(role="user", content=content)

    assert message.content == content


def test_chat_rejects_unknown_fields() -> None:
    response = post_chat(
        {
            "messages": [
                {"role": "user", "content": "你好", "unexpected": True}
            ]
        },
        StubProvider(),
    )

    assert response.status_code == 422


def test_chat_requires_latest_message_to_be_from_user() -> None:
    response = post_chat(
        {
            "messages": [
                {"role": "user", "content": "你好"},
                {"role": "assistant", "content": "你好！"},
            ]
        },
        StubProvider(),
    )

    assert response.status_code == 422


def test_chat_rejects_unknown_message_role() -> None:
    response = post_chat(
        {"messages": [{"role": "tool", "content": "not yet supported"}]},
        StubProvider(),
    )

    assert response.status_code == 422


def test_chat_hides_provider_failure_details() -> None:
    response = post_chat(
        {"messages": [{"role": "user", "content": "你好"}]},
        StubProvider(should_fail=True),
    )

    assert response.status_code == 502
    assert response.json() == {
        "detail": "The configured LLM provider request failed."
    }


def test_chat_converts_invalid_provider_output_to_safe_error() -> None:
    response = post_chat(
        {"messages": [{"role": "user", "content": "你好"}]},
        StubProvider(answer="x" * 20_001),
    )

    assert response.status_code == 502
    assert response.json() == {
        "detail": (
            "The configured LLM provider returned an invalid assistant message."
        )
    }


def test_chat_stream_forwards_context_and_finishes_with_full_message() -> None:
    provider = StubProvider(stream_chunks=["你", "好，", "世界！"])
    messages = [
        {"role": "system", "content": "你是一名编程导师。"},
        {"role": "user", "content": "先解释 Service。"},
        {"role": "assistant", "content": "Service 承载业务逻辑。"},
        {"role": "user", "content": "再解释 Controller。"},
    ]

    response = post_chat_stream({"messages": messages}, provider)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"
    assert "content-length" not in response.headers
    assert parse_sse(response.text) == [
        ("metadata", {"model": "stub-model"}),
        ("delta", {"content": "你"}),
        ("delta", {"content": "好，"}),
        ("delta", {"content": "世界！"}),
        (
            "done",
            {
                "message": {"role": "assistant", "content": "你好，世界！"},
                "model": "stub-model",
            },
        ),
    ]
    assert [message.model_dump() for message in provider.received_messages] == messages


def test_chat_stream_reports_midstream_provider_failure_without_done() -> None:
    provider = StubProvider(
        stream_chunks=["已经", "收到一部分"],
        fail_stream_after_chunks=True,
    )

    response = post_chat_stream(
        {"messages": [{"role": "user", "content": "你好"}]}, provider
    )
    events = parse_sse(response.text)

    assert [event for event, _payload in events] == [
        "metadata",
        "delta",
        "delta",
        "error",
    ]
    assert events[-1][1] == {
        "detail": "The configured LLM provider request failed."
    }
    assert all(event != "done" for event, _payload in events)


def test_chat_stream_hides_unexpected_failure_details() -> None:
    response = post_chat_stream(
        {"messages": [{"role": "user", "content": "你好"}]},
        StubProvider(fail_stream_unexpectedly=True),
    )

    assert parse_sse(response.text)[-1] == (
        "error",
        {"detail": "The chat stream ended unexpectedly."},
    )
    assert "private provider detail" not in response.text


def test_chat_stream_rejects_invalid_complete_message() -> None:
    response = post_chat_stream(
        {"messages": [{"role": "user", "content": "你好"}]},
        StubProvider(stream_chunks=["x" * 20_001]),
    )

    events = parse_sse(response.text)
    assert [event for event, _payload in events] == ["metadata", "error"]
    assert events[-1][1] == {
        "detail": (
            "The configured LLM provider returned an invalid assistant message."
        )
    }


def test_chat_stream_validates_request_before_resolving_provider() -> None:
    with patch(
        "app.api.chat.get_llm_provider",
        side_effect=LLMConfigurationError("LLM is not configured."),
    ) as provider_resolver:
        response = client.post("/api/chat/stream", json={"messages": []})

    assert response.status_code == 422
    provider_resolver.assert_not_called()


def test_chat_stream_returns_http_error_for_preflight_configuration_failure() -> None:
    with patch(
        "app.api.chat.get_llm_provider",
        side_effect=LLMConfigurationError("LLM is not configured."),
    ):
        response = client.post(
            "/api/chat/stream",
            json={"messages": [{"role": "user", "content": "你好"}]},
        )

    assert response.status_code == 503
    assert response.json() == {"detail": "LLM is not configured."}
