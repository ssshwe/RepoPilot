import math
import os

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.embedding.provider import OpenAICompatibleEmbeddingProvider
from app.main import app


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("REPOPILOT_EMBEDDING_INTEGRATION") != "1",
    reason="Opt in with REPOPILOT_EMBEDDING_INTEGRATION=1 and a configured embedding service.",
)
def test_real_embedding_debug_and_similarity(monkeypatch) -> None:
    """Explicit opt-in sends only these fixed texts to the configured provider."""
    captured_batches = []
    original_embed = OpenAICompatibleEmbeddingProvider.embed_texts

    async def capture_actual_vectors(provider, texts):
        vectors = await original_embed(provider, texts)
        captured_batches.append(vectors)
        return vectors

    # Observe real model responses; no vectors or network calls are stubbed.
    monkeypatch.setattr(OpenAICompatibleEmbeddingProvider, "embed_texts", capture_actual_vectors)
    with TestClient(app) as http:
        debug = http.post("/api/embeddings/debug", json={"text": "订单退款"})
        assert debug.status_code == 200, debug.text
        result = debug.json()
        assert result["model"] == get_settings().embedding_model
        assert result["dimension"] >= len(result["preview"]) > 0
        assert len(result["preview"]) <= 8
        assert all(math.isfinite(value) for value in result["preview"])
        for text in ["退还订单金额", "今天天气不错", "订单退款"]:
            captured_batches.clear()
            response = http.post("/api/embeddings/similarity", json={
                "text_a": "订单退款", "text_b": text,
            })
            assert response.status_code == 200, response.text
            pair = response.json()
            assert pair["model"] == result["model"]
            assert pair["dimension"] == result["dimension"]
            assert math.isfinite(pair["similarity"]) and -1 <= pair["similarity"] <= 1
            assert len(captured_batches) == 1 and len(captured_batches[0]) == 2
            first, second = captured_batches[0]
            # Same text can produce slightly different model outputs. Check the
            # API's arithmetic against these exact outputs, not an assumed 1.0.
            expected = math.fsum(a * b for a, b in zip(first, second, strict=True)) / (
                math.sqrt(math.fsum(a * a for a in first))
                * math.sqrt(math.fsum(b * b for b in second))
            )
            assert pair["similarity"] == pytest.approx(expected, rel=1e-12, abs=1e-12)
