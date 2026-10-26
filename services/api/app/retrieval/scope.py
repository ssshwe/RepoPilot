"""Versioned internal filtering contract; public API schemas stay unchanged."""
from dataclasses import dataclass, field
from pathlib import PurePosixPath
import re

from app.retrieval.candidates import CandidateBatch, CandidateSearchRequest
from app.retrieval.snapshot_models import digest

SCOPE_VERSION = "retrieval-scope-v1"


def relative_path(value, *, trailing_slash=False):
    if (not isinstance(value, str) or not 1 <= len(value) <= 4096 or value.strip() != value
            or PurePosixPath(value).is_absolute() or "\\" in value or ":" in value
            or any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in value)
            or any(c in value for c in "*?[]")
            or any(part in {"", ".", ".."} for part in (value[:-1] if trailing_slash and value.endswith("/") else value).split("/"))):
        raise ValueError("Path must be a portable relative literal path without traversal or glob syntax.")
    return value


@dataclass(frozen=True)
class SearchFilters:
    language: str | None = None
    path_prefix: str | None = None

    def __post_init__(self):
        if self.language is not None and (not isinstance(self.language, str) or re.fullmatch(r"[a-z][a-z0-9_+#-]{0,31}", self.language) is None):
            raise ValueError("Language must be a lowercase indexed language name.")
        if self.path_prefix is not None:
            relative_path(self.path_prefix, trailing_slash=True)

    def matches(self, source):
        return ((self.language is None or source.language == self.language)
                and (self.path_prefix is None or source.path.startswith(self.path_prefix)))

    def to_dict(self):
        return {"language": self.language, "path_prefix": self.path_prefix}

    def fingerprint(self, repository):
        return digest({"version": SCOPE_VERSION, "repository": repository, "filters": self.to_dict()})


@dataclass(frozen=True)
class ScopedSearchRequest(CandidateSearchRequest):
    filters: SearchFilters = field(default_factory=SearchFilters, kw_only=True)

    def __post_init__(self):
        super().__post_init__()
        if not isinstance(self.filters, SearchFilters):
            raise TypeError("Search filters must be SearchFilters.")


def scoped_request(request):
    if isinstance(request, ScopedSearchRequest):
        return request
    if isinstance(request, CandidateSearchRequest):
        return ScopedSearchRequest(request.repository, request.query, request.candidate_limit)
    raise TypeError("Scoped channels require a candidate search request.")


@dataclass(frozen=True)
class ScopedCandidateBatch(CandidateBatch):
    filters: SearchFilters = field(default_factory=SearchFilters, kw_only=True)

    def __post_init__(self):
        super().__post_init__()
        if not isinstance(self.filters, SearchFilters) or any(not self.filters.matches(c.source_metadata) for c in self.candidates):
            raise ValueError("Candidate batch contains code outside its declared filters.")

    def to_dict(self):
        return super().to_dict() | {"filters": self.filters.to_dict(), "scope_fingerprint": self.filters.fingerprint(self.repository)}

    @classmethod
    def from_dict(cls, value):
        fields = dict(value)
        filters = SearchFilters(**fields.pop("filters"))
        fingerprint = fields.pop("scope_fingerprint")
        base = CandidateBatch.from_dict(fields)
        if fingerprint != filters.fingerprint(base.repository):
            raise ValueError("Candidate scope fingerprint mismatch.")
        return cls(base.repository, base.query, base.channel, base.index_revision, base.candidate_limit,
                   base.candidates, base.retrieval_latency_ms, filters=filters)
