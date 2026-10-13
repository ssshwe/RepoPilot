import math
from collections.abc import Sequence

from app.embedding.validation import validate_vector


def cosine_similarity(vector_a: Sequence[float], vector_b: Sequence[float]) -> float:
    """Compare direction, returning [-1, 1]; zero vectors have no defined cosine."""
    a = validate_vector(vector_a)
    b = validate_vector(vector_b)
    if len(a) != len(b):
        raise ValueError("Cosine similarity requires equal vector dimensions.")

    # Scale before computing norms: finite inputs near 1e308/1e-308 must not
    # overflow or underflow merely because their magnitudes differ.
    scale_a = max(abs(value) for value in a)
    scale_b = max(abs(value) for value in b)
    a = [value / scale_a for value in a]
    b = [value / scale_b for value in b]
    norm_a = math.hypot(*a)
    norm_b = math.hypot(*b)
    score = math.fsum(
        (x / norm_a) * (y / norm_b) for x, y in zip(a, b, strict=True)
    )
    # Floating-point roundoff can move mathematically valid cosine beyond 1.
    return max(-1.0, min(1.0, score))
