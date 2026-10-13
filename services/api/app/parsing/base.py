from dataclasses import dataclass
from typing import Protocol

from app.repository.models import RepositoryFile


class CodeParsingError(Exception):
    """A source cannot produce trustworthy syntax metadata."""


class UnsupportedLanguageError(CodeParsingError):
    """No grammar is registered for the source language."""


class SourceSyntaxError(CodeParsingError):
    """Tree-sitter recovered syntax errors; do not trust its partial symbols."""


@dataclass(frozen=True, slots=True)
class SyntaxSymbol:
    node_type: str
    name: str
    symbol: str
    parent_symbol: str | None
    # RepoPilot lines are one-based and inclusive; bytes are UTF-8, end-exclusive.
    start_line: int
    end_line: int
    start_byte: int
    end_byte: int
    # Zero-based Tree-sitter positions in the parse buffer, not citation lines.
    # A lone CR becomes LF there without changing original UTF-8 byte offsets.
    start_point: tuple[int, int]
    end_point: tuple[int, int]


@dataclass(frozen=True, slots=True)
class ParsedSource:
    repository: str
    path: str
    language: str
    root_node_type: str
    symbols: tuple[SyntaxSymbol, ...]


class CodeParser(Protocol):
    @property
    def language(self) -> str: ...

    def parse(self, file: RepositoryFile) -> ParsedSource:
        """Parse already-decoded source without filesystem or service access."""
        ...
