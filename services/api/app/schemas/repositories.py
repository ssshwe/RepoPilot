from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.exceptions import RepositoryPathError
from app.repository.models import RepositoryScanResult, SkipReason
from app.repository.paths import validate_repository_name


class RepositoryScanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repository: str = Field(strict=True, min_length=1, max_length=64)

    @field_validator("repository")
    @classmethod
    def valid_name(cls, value: str) -> str:
        try:
            return validate_repository_name(value)
        except RepositoryPathError as exc:
            raise ValueError(str(exc)) from None


class RepositoryFileInfo(BaseModel):
    path: str
    language: str
    size_bytes: int = Field(ge=0)
    line_count: int = Field(ge=0)


class SkippedPathInfo(BaseModel):
    path: str
    reason: SkipReason


class RepositoryScanResponse(BaseModel):
    repository: str
    files_scanned: int = Field(ge=0)
    supported_files: int = Field(ge=0)
    skipped_files: int = Field(ge=0)
    total_bytes: int = Field(ge=0)
    files: list[RepositoryFileInfo]
    skipped: list[SkippedPathInfo]
    pruned_directories: list[SkippedPathInfo]

    @classmethod
    def from_result(cls, result: RepositoryScanResult) -> "RepositoryScanResponse":
        # Explicit projection keeps file contents and absolute server paths internal.
        return cls(
            repository=result.repository, files_scanned=result.files_scanned,
            supported_files=len(result.files), skipped_files=len(result.skipped),
            total_bytes=result.total_bytes,
            files=[RepositoryFileInfo(
                path=file.path, language=file.language, size_bytes=file.size_bytes,
                line_count=file.line_count,
            ) for file in result.files],
            skipped=[SkippedPathInfo(path=item.path, reason=item.reason) for item in result.skipped],
            pruned_directories=[SkippedPathInfo(path=item.path, reason=item.reason) for item in result.pruned_directories],
        )
