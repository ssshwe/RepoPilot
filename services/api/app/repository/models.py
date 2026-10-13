from dataclasses import dataclass
from typing import Literal


SkipReason = Literal[
    "ignored_file", "ignored_directory", "unsupported_extension", "too_large",
    "binary", "invalid_encoding", "unreadable", "unsafe_link",
    "outside_repository", "not_regular_file", "changed_during_scan",
]


@dataclass(frozen=True, slots=True)
class RepositoryFile:
    repository: str
    path: str
    language: str
    size_bytes: int
    line_count: int
    content: str


@dataclass(frozen=True, slots=True)
class SkippedPath:
    path: str
    reason: SkipReason


@dataclass(frozen=True, slots=True)
class RepositoryScanResult:
    repository: str
    files: list[RepositoryFile]
    skipped: list[SkippedPath]
    pruned_directories: list[SkippedPath]

    @property
    def files_scanned(self) -> int:
        return len(self.files) + len(self.skipped)

    @property
    def total_bytes(self) -> int:
        return sum(file.size_bytes for file in self.files)
