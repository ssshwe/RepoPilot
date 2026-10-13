from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


MessageRole = Literal["system", "user", "assistant"]
MAX_CHAT_MESSAGE_LENGTH = 20_000


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: MessageRole
    content: str = Field(min_length=1, max_length=MAX_CHAT_MESSAGE_LENGTH)

    @field_validator("content")
    @classmethod
    def content_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message content must not be blank")
        return value


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    messages: list[ChatMessage] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def latest_message_must_be_from_user(self) -> Self:
        if self.messages[-1].role != "user":
            raise ValueError("the latest message must have the user role")
        return self


class ChatResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: ChatMessage
    model: str


class ChatStreamMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str


class ChatStreamDelta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str


class ChatStreamError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    detail: str
