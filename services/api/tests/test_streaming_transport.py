import asyncio
import json
import socket
import threading
import time
from collections.abc import AsyncGenerator, Sequence
from typing import Any
from unittest.mock import patch

import httpx
import uvicorn
from pydantic import BaseModel

from app.llm.base import LLMProvider
from app.llm.tool_types import (
    FunctionToolDefinition,
    ToolAssistantTurn,
    ToolLoopMessage,
)
from app.main import app
from app.schemas.chat import ChatMessage


class GatedStreamProvider(LLMProvider):
    def __init__(self, release_second_chunk: threading.Event) -> None:
        self.release_second_chunk = release_second_chunk

    @property
    def model_name(self) -> str:
        return "live-transport-test-model"

    async def complete(self, messages: Sequence[ChatMessage]) -> str:
        return messages[-1].content

    async def complete_structured(
        self,
        _messages: Sequence[ChatMessage],
        _response_model: type[BaseModel],
    ) -> BaseModel:
        raise AssertionError("structured completion is not used by this test")

    async def complete_with_tools(
        self,
        _messages: Sequence[ToolLoopMessage],
        _tools: Sequence[FunctionToolDefinition],
    ) -> ToolAssistantTurn:
        raise AssertionError("tool completion is not used by this test")

    async def stream(
        self, _messages: Sequence[ChatMessage]
    ) -> AsyncGenerator[str, None]:
        yield "first"
        while not self.release_second_chunk.is_set():
            await asyncio.sleep(0.01)
        yield "second"


def test_uvicorn_flushes_first_delta_before_provider_finishes() -> None:
    release_second_chunk = threading.Event()
    watchdog_fired = threading.Event()
    provider = GatedStreamProvider(release_second_chunk)

    listen_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listen_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listen_socket.bind(("127.0.0.1", 0))
    listen_socket.listen(8)
    port = listen_socket.getsockname()[1]

    server = uvicorn.Server(
        uvicorn.Config(app, log_level="critical", lifespan="off")
    )
    server_thread = threading.Thread(
        target=server.run,
        kwargs={"sockets": [listen_socket]},
        daemon=True,
    )

    def release_from_watchdog() -> None:
        watchdog_fired.set()
        release_second_chunk.set()

    watchdog = threading.Timer(3.0, release_from_watchdog)

    with patch("app.api.chat.get_llm_provider", return_value=provider):
        server_thread.start()
        deadline = time.monotonic() + 5
        while not server.started and server_thread.is_alive():
            if time.monotonic() >= deadline:
                break
            time.sleep(0.01)

        try:
            assert server.started
            watchdog.start()
            deltas: list[str] = []
            done_payload: dict[str, Any] | None = None
            saw_first_before_watchdog = False
            current_event = "message"

            with httpx.Client(timeout=5, trust_env=False) as client:
                with client.stream(
                    "POST",
                    f"http://127.0.0.1:{port}/api/chat/stream",
                    headers={"Accept": "text/event-stream"},
                    json={
                        "messages": [
                            {"role": "user", "content": "transport test"}
                        ]
                    },
                ) as response:
                    assert response.status_code == 200
                    for line in response.iter_lines():
                        if line.startswith("event:"):
                            current_event = line.removeprefix("event:").strip()
                        elif line.startswith("data:") and current_event == "delta":
                            payload = json.loads(
                                line.removeprefix("data:").lstrip()
                            )
                            deltas.append(payload["content"])
                            if len(deltas) == 1:
                                saw_first_before_watchdog = not watchdog_fired.is_set()
                                release_second_chunk.set()
                        elif line.startswith("data:") and current_event == "done":
                            done_payload = json.loads(
                                line.removeprefix("data:").lstrip()
                            )
                            break

            assert saw_first_before_watchdog is True
            assert deltas == ["first", "second"]
            assert done_payload is not None
            assert done_payload["message"]["content"] == "firstsecond"
        finally:
            watchdog.cancel()
            release_second_chunk.set()
            server.should_exit = True
            server_thread.join(timeout=5)
            listen_socket.close()

    assert not server_thread.is_alive()
