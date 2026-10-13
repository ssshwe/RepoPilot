import math
from collections.abc import Sequence
from numbers import Real


MAX_EMBEDDING_TEXT_LENGTH = 8_192
MAX_EMBEDDING_DIMENSION = 65_536


def validate_vector(vector: Sequence[float]) -> list[float]:
    """Reject malformed data before numerical calculation or later DB writes."""
    if isinstance(vector, (str, bytes)) or not isinstance(vector, Sequence):
        raise ValueError("An embedding vector must be a numeric sequence.")
    if not 1 <= len(vector) <= MAX_EMBEDDING_DIMENSION:
        raise ValueError("Embedding vector dimension is outside the allowed range.")
    if any(
        isinstance(value, bool) or not isinstance(value, Real)
        or not math.isfinite(value)
        for value in vector
    ):
        raise ValueError("Embedding coordinates must be finite numbers.")
    result = [float(value) for value in vector]
    if not any(result):
        raise ValueError("An embedding vector must not be a zero vector.")
    return result


def validate_embedding_batch(
    vectors: Sequence[Sequence[float]], expected_count: int
) -> list[list[float]]:
    if len(vectors) != expected_count:
        raise ValueError("Embedding count does not match input count.")
    result = [validate_vector(vector) for vector in vectors]
    if result and any(len(vector) != len(result[0]) for vector in result):
        raise ValueError("Embedding dimensions must match within one batch.")
    return result
