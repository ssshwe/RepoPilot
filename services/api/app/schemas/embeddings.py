from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from app.embedding.validation import MAX_EMBEDDING_DIMENSION, MAX_EMBEDDING_TEXT_LENGTH


VECTOR_PREVIEW_LENGTH = 8
EmbeddingText = Annotated[
    str, StringConstraints(strict=True, min_length=1, max_length=MAX_EMBEDDING_TEXT_LENGTH)
]


class EmbeddingDebugRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: EmbeddingText

    @field_validator("text")
    @classmethod
    def non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Text must not be blank")
        return value


class EmbeddingSimilarityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text_a: EmbeddingText
    text_b: EmbeddingText

    @field_validator("text_a", "text_b")
    @classmethod
    def non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Text must not be blank")
        return value


class EmbeddingDebugResponse(BaseModel):
    model: str
    dimension: int = Field(ge=1, le=MAX_EMBEDDING_DIMENSION)
    preview: list[float] = Field(min_length=1, max_length=VECTOR_PREVIEW_LENGTH)


class EmbeddingSimilarityResponse(BaseModel):
    model: str
    dimension: int = Field(ge=1, le=MAX_EMBEDDING_DIMENSION)
    similarity: float = Field(ge=-1, le=1)
