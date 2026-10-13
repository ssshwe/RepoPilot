from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


REPOSITORY_ROOT = Path(__file__).resolve().parents[4]


class Settings(BaseSettings):
    """Values loaded from process environment variables or the root .env file."""

    model_config = SettingsConfigDict(
        env_file=REPOSITORY_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    api_cors_origins: str = "http://localhost:5173"
    llm_provider: str = "openai_compatible"
    llm_api_key: SecretStr = SecretStr("")
    llm_base_url: str = ""
    llm_model: str = ""
    llm_timeout_seconds: float = Field(default=60.0, gt=0, le=300)

    # Embedding configuration is independent; never inherit LLM credentials/model.
    embedding_provider: str = "openai_compatible"
    embedding_api_key: SecretStr = SecretStr("")
    embedding_base_url: str = ""
    embedding_model: str = ""
    embedding_timeout_seconds: float = Field(default=120.0, gt=0, le=300)
    embedding_batch_size: int = Field(default=16, ge=1, le=128)

    repository_max_file_bytes: int = Field(default=1_048_576, ge=1, le=10_485_760)
    repository_max_entries: int = Field(default=10_000, ge=1, le=100_000)
    repository_max_total_bytes: int = Field(default=20_971_520, ge=1, le=209_715_200)

    chunk_lines: int = Field(default=60, ge=1, le=200)
    chunk_overlap_lines: int = Field(default=10, ge=0, le=199)
    chunk_max_characters: int = Field(default=8_192, ge=1, le=65_536)
    chunk_max_chunks: int = Field(default=10_000, ge=1, le=20_000)

    # The prototype prepares all vectors before replacing an existing index.
    indexing_max_text_characters: int = Field(default=2_000_000, ge=1, le=20_000_000)
    indexing_max_vector_values: int = Field(default=2_000_000, ge=1, le=20_000_000)

    # The complete serialized repository context, including metadata/boundaries.
    # These are character/source limits, not a tokenizer or LLM window estimate.
    max_context_chunks: int = Field(default=5, ge=1, le=20)
    max_context_characters: int = Field(default=16_000, ge=1, le=200_000)

    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: SecretStr = SecretStr("")
    qdrant_collection: str = Field(
        default="repopilot_code", pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"
    )
    qdrant_timeout_seconds: float = Field(default=5.0, gt=0, le=60)

    @model_validator(mode="after")
    def validate_chunk_overlap(self) -> "Settings":
        if self.chunk_overlap_lines >= self.chunk_lines:
            raise ValueError("CHUNK_OVERLAP_LINES must be smaller than CHUNK_LINES")
        return self

    @field_validator("qdrant_url")
    @classmethod
    def validate_qdrant_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("QDRANT_URL must be an HTTP(S) URL without credentials or query")
        # Accessing port also rejects malformed or out-of-range port values.
        _ = parsed.port
        return value

    @property
    def cors_origins(self) -> list[str]:
        return [
            origin.strip()
            for origin in self.api_cors_origins.split(",")
            if origin.strip()
        ]


@lru_cache
def get_settings() -> Settings:
    return Settings()

