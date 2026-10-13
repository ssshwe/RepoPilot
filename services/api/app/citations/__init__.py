"""Request-scoped, pure Citation validation and metadata projection."""

from app.citations.models import Citation
from app.citations.validator import CitationValidationError, CitationValidator

__all__ = ["Citation", "CitationValidationError", "CitationValidator"]
