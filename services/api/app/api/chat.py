import logging
from collections.abc import AsyncIterator
from contextlib import aclosing

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ValidationError

from app.core.exceptions import LLMProviderError
from app.llm.factory import get_llm_provider
from app.schemas.chat import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
    ChatStreamDelta,
    ChatStreamError,
    ChatStreamMetadata,
    MAX_CHAT_MESSAGE_LENGTH,
)


router = APIRouter(prefix="/api", tags=["chat"])
logger = logging.getLogger(__name__)


def _sse_event(event: str, payload: BaseModel) -> str:
    """Encode one JSON payload as a complete Server-Sent Event frame."""
    return f"event: {event}\ndata: {payload.model_dump_json()}\n\n"


@router.post("/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
) -> ChatResponse:
    # Resolve provider configuration only after FastAPI validates the request body.
    provider = get_llm_provider()
    answer = await provider.complete(request.messages)

    try:
        assistant_message = ChatMessage(role="assistant", content=answer)
    except ValidationError as exc:
        raise LLMProviderError(
            "The configured LLM provider returned an invalid assistant message."
        ) from exc

    return ChatResponse(
        message=assistant_message,
        model=provider.model_name,
    )


@router.post("/chat/stream", response_class=StreamingResponse)
async def chat_stream(request: ChatRequest) -> StreamingResponse:
    # Resolve configuration before response headers are sent so invalid setup can
    # still use the API's normal HTTP 503 error response.
    provider = get_llm_provider()

    async def event_source() -> AsyncIterator[str]:
        yield _sse_event(
            "metadata", ChatStreamMetadata(model=provider.model_name)
        )

        fragments: list[str] = []
        total_length = 0
        try:
            async with aclosing(provider.stream(request.messages)) as stream:
                async for fragment in stream:
                    total_length += len(fragment)
                    if total_length > MAX_CHAT_MESSAGE_LENGTH:
                        raise LLMProviderError(
                            "The configured LLM provider returned an invalid "
                            "assistant message."
                        )
                    fragments.append(fragment)
                    yield _sse_event("delta", ChatStreamDelta(content=fragment))

            assistant_message = ChatMessage(
                role="assistant", content="".join(fragments)
            )
            yield _sse_event(
                "done",
                ChatResponse(
                    message=assistant_message,
                    model=provider.model_name,
                ),
            )
        except LLMProviderError as exc:
            yield _sse_event("error", ChatStreamError(detail=str(exc)))
        except ValidationError:
            yield _sse_event(
                "error",
                ChatStreamError(
                    detail=(
                        "The configured LLM provider returned an invalid "
                        "assistant message."
                    )
                ),
            )
        except Exception:
            logger.exception("Unexpected error while streaming a chat response")
            yield _sse_event(
                "error",
                ChatStreamError(detail="The chat stream ended unexpectedly."),
            )

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
