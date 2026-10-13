from fastapi import APIRouter
from pydantic import ValidationError

from app.agent.tool_loop import ToolCallingAgent
from app.core.exceptions import LLMProviderError
from app.llm.factory import get_llm_provider
from app.schemas.chat import ChatMessage, ChatRequest, ChatResponse


router = APIRouter(prefix="/api", tags=["agent"])


@router.post("/agent", response_model=ChatResponse)
async def agent_chat(request: ChatRequest) -> ChatResponse:
    # Resolve provider configuration only after request validation.
    provider = get_llm_provider()
    answer = await ToolCallingAgent(provider).run(request.messages)

    try:
        assistant_message = ChatMessage(role="assistant", content=answer)
    except ValidationError as exc:
        raise LLMProviderError(
            "The configured LLM provider returned an invalid assistant message."
        ) from exc

    return ChatResponse(message=assistant_message, model=provider.model_name)
