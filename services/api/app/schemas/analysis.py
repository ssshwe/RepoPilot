from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


MAX_REQUIREMENT_LENGTH = 20_000
MAX_ANALYSIS_ITEM_LENGTH = 2_000


class AnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirement: str = Field(min_length=1, max_length=MAX_REQUIREMENT_LENGTH)

    @field_validator("requirement")
    @classmethod
    def requirement_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("requirement must not be blank")
        return value


class RequirementAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=MAX_ANALYSIS_ITEM_LENGTH)
    difficulty: Literal["easy", "medium", "hard"]
    tasks: list[str] = Field(min_length=1, max_length=20)
    risks: list[str] = Field(max_length=20)

    @field_validator("summary")
    @classmethod
    def summary_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("summary must not be blank")
        return value

    @field_validator("tasks", "risks")
    @classmethod
    def list_items_must_be_usable(cls, values: list[str]) -> list[str]:
        if any(
            not isinstance(value, str)
            or not value.strip()
            or len(value) > MAX_ANALYSIS_ITEM_LENGTH
            for value in values
        ):
            raise ValueError("analysis list items must be non-blank strings")
        return values
