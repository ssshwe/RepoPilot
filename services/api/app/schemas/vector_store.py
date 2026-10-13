from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class EnsureCollectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension: int = Field(strict=True, ge=1, le=65_536)


class QdrantHealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    collection: str
    collection_exists: bool


class CollectionInfoResponse(BaseModel):
    collection: str
    dimension: int
    distance: Literal["Cosine"]
    points_count: int | None
    status: str


class EnsureCollectionResponse(CollectionInfoResponse):
    created: bool
