from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class CodeChunk:
    chunk_id: str
    repository: str
    path: str
    language: str
    start_line: int
    end_line: int
    content: str
    symbol: str | None = None
    node_type: str | None = None
    parent_symbol: str | None = None
    chunking_strategy: Literal["simple", "ast", "fallback"] = "simple"
    chunking_version: str = "line-window-v1"
