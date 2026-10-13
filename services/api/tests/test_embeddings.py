import asyncio
import logging
import math
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient
from openai import APIConnectionError, APIStatusError, APITimeoutError
from pydantic import ValidationError

from app.core.config import Settings
from app.core.exceptions import EmbeddingConfigurationError, EmbeddingProviderError
from app.embedding.base import EmbeddingProvider
from app.embedding.factory import EmbeddingProviderFactory, get_embedding_provider
from app.embedding.provider import OpenAICompatibleEmbeddingProvider
from app.main import app
from app.retrieval.similarity import cosine_similarity


def configured_settings(**overrides) -> Settings:
    values = dict(
        embedding_provider="openai_compatible", embedding_api_key="embedding-test-key",
        embedding_base_url="http://127.0.0.1:11434/v1", embedding_model="embedding-test",
        embedding_timeout_seconds=17, embedding_batch_size=2,
        llm_api_key="different-llm-key", llm_model="different-chat-model",
        llm_base_url="https://chat.example.test/v1",
    )
    return Settings(_env_file=None, **(values | overrides))


def sdk_response(*vectors: list[float]) -> SimpleNamespace:
    return SimpleNamespace(data=[
        SimpleNamespace(index=index, embedding=vector)
        for index, vector in enumerate(vectors)
    ])


def provider_with_client(*responses) -> tuple[OpenAICompatibleEmbeddingProvider, SimpleNamespace]:
    client = SimpleNamespace(
        embeddings=SimpleNamespace(create=AsyncMock(side_effect=list(responses))),
        close=AsyncMock(),
    )
    provider = OpenAICompatibleEmbeddingProvider(
        api_key="fake-key", base_url="http://127.0.0.1:11434/v1", model="embedding-test",
        timeout_seconds=17, batch_size=2, client=client,
    )
    return provider, client


@pytest.mark.parametrize("a,b,expected", [
    ([1, 2, 3], [1, 2, 3], 1),
    ([1, 0], [0, 1], 0),
    ([1, 2], [-1, -2], -1),
    ([1, 2], [10, 20], 1),
    ([1, 0], [1, 1], 1 / math.sqrt(2)),
    ([1e308, 1e308], [1e-308, 1e-308], 1),
    ([1e-308, 0], [0, 1e-308], 0),
])
def test_cosine_direction_and_numerical_stability(a, b, expected) -> None:
    score = cosine_similarity(a, b)
    assert math.isfinite(score) and -1 <= score <= 1
    assert score == pytest.approx(expected)
    assert cosine_similarity(b, a) == pytest.approx(score)


@pytest.mark.parametrize("a,b", [
    ([], []), ([0, 0], [1, 1]), ([1], [1, 2]),
    ([float("nan")], [1]), ([float("inf")], [1]),
    ([True], [1]), (["1"], [1]), ("12", [1, 2]), (None, [1]),
])
def test_cosine_rejects_undefined_or_malformed_vectors(a, b) -> None:
    with pytest.raises(ValueError):
        cosine_similarity(a, b)


def test_factory_passes_only_embedding_settings_and_redacts_key() -> None:
    settings = configured_settings()
    assert "embedding-test-key" not in repr(settings)
    with patch("app.embedding.factory.OpenAICompatibleEmbeddingProvider") as factory:
        EmbeddingProviderFactory.create(settings)
    assert factory.call_args.kwargs == dict(
        api_key="embedding-test-key", base_url="http://127.0.0.1:11434/v1",
        model="embedding-test", timeout_seconds=17, batch_size=2,
    )


@pytest.mark.parametrize("overrides,field", [
    ({"embedding_provider": "unknown"}, "EMBEDDING_PROVIDER"),
    ({"embedding_api_key": ""}, "EMBEDDING_API_KEY"),
    ({"embedding_model": " "}, "EMBEDDING_MODEL"),
    ({"embedding_base_url": ""}, "EMBEDDING_BASE_URL"),
])
def test_missing_embedding_config_never_falls_back_to_llm(overrides, field) -> None:
    with pytest.raises(EmbeddingConfigurationError, match=field):
        EmbeddingProviderFactory.create(configured_settings(**overrides))


@pytest.mark.parametrize("url", [
    "ftp://example.test", "file:///tmp/vector", "http://localhost:99999",
    "http://user:private-secret@localhost/v1", "http://localhost/v1?key=private-secret",
    "http://localhost/v1#private-secret", "http://[invalid",
])
def test_factory_rejects_bad_urls_without_echoing_them(url) -> None:
    with pytest.raises(EmbeddingConfigurationError) as error:
        EmbeddingProviderFactory.create(configured_settings(embedding_base_url=url))
    assert "private-secret" not in str(error.value)


@pytest.mark.parametrize("field,value", [
    ("embedding_batch_size", 0), ("embedding_batch_size", 129),
    ("embedding_timeout_seconds", 0), ("embedding_timeout_seconds", 301),
])
def test_embedding_config_limits(field, value) -> None:
    with pytest.raises(ValidationError):
        configured_settings(**{field: value})


@pytest.mark.parametrize("url,loopback", [
    ("http://127.0.0.1:11434/v1", True), ("http://localhost:11434/v1", True),
    ("http://[::1]:11434/v1", True), ("https://embedding.example.test/v1", False),
])
def test_sdk_uses_explicit_float_encoding_timeout_and_loopback_proxy_policy(url, loopback) -> None:
    with patch("app.embedding.provider.AsyncOpenAI") as sdk, patch(
        "app.embedding.provider.DefaultAsyncHttpxClient"
    ) as http_client:
        OpenAICompatibleEmbeddingProvider(
            api_key="fake-key", base_url=url, model="embedding-test",
            timeout_seconds=17, batch_size=2,
        )
    options = sdk.call_args.kwargs
    assert options["api_key"] == "fake-key" and options["base_url"] == url
    assert options["timeout"] == 17 and options["max_retries"] == 0
    if loopback:
        http_client.assert_called_once_with(trust_env=False)
        assert options["http_client"] is http_client.return_value
    else:
        http_client.assert_not_called()


def test_single_text_and_batch_preserve_order_whitespace_and_limit_requests() -> None:
    first = sdk_response([1, 0], [0, 1])
    first.data.reverse()
    provider, client = provider_with_client(first, sdk_response([1, 1]), sdk_response([3, 4]))

    async def exercise() -> None:
        assert await provider.embed_texts([" a ", "b", "c"]) == [[1, 0], [0, 1], [1, 1]]
        assert await provider.embed_text("single") == [3, 4]
        await provider.aclose()

    asyncio.run(exercise())
    assert [call.kwargs for call in client.embeddings.create.call_args_list] == [
        dict(model="embedding-test", input=[" a ", "b"], encoding_format="float"),
        dict(model="embedding-test", input=["c"], encoding_format="float"),
        dict(model="embedding-test", input=["single"], encoding_format="float"),
    ]
    client.close.assert_awaited_once()


def test_empty_input_makes_no_request() -> None:
    provider, client = provider_with_client()
    assert asyncio.run(provider.embed_texts([])) == []
    client.embeddings.create.assert_not_called()


@pytest.mark.parametrize("inputs", ["text", [""], ["  "], [None], [12], ["x" * 8193], ["a", "b", " "]])
def test_invalid_input_is_checked_before_any_batch_request(inputs) -> None:
    provider, client = provider_with_client()
    with pytest.raises(ValueError):
        asyncio.run(provider.embed_texts(inputs))
    client.embeddings.create.assert_not_called()


@pytest.mark.parametrize("response", [
    sdk_response(), sdk_response([1, 2], [3, 4]),
    sdk_response([]), sdk_response([0, 0]), sdk_response([float("nan")]),
    sdk_response([float("inf")]), sdk_response([True]), sdk_response(["secret"]),
    sdk_response([1] * 65537), SimpleNamespace(data=None), SimpleNamespace(),
    SimpleNamespace(data=[SimpleNamespace(index=-1, embedding=[1])]),
    SimpleNamespace(data=[SimpleNamespace(index=True, embedding=[1])]),
    SimpleNamespace(data=[SimpleNamespace(index=1, embedding=[1])]),
    SimpleNamespace(data=[SimpleNamespace(index=0, embedding=None)]),
])
def test_provider_rejects_invalid_response_with_safe_error(response) -> None:
    provider, _ = provider_with_client(response)
    with pytest.raises(EmbeddingProviderError, match="invalid vectors") as error:
        asyncio.run(provider.embed_text("text"))
    assert "secret" not in str(error.value)


def test_provider_rejects_duplicate_indexes_and_dimension_changes() -> None:
    repeated = sdk_response([1, 2], [3, 4])
    repeated.data[1].index = 0
    cases = [
        (repeated, ["a", "b"]),
        (sdk_response([1], [1, 2]), ["a", "b"]),
    ]
    for response, inputs in cases:
        provider, _ = provider_with_client(response)
        with pytest.raises(EmbeddingProviderError):
            asyncio.run(provider.embed_texts(inputs))
    provider, _ = provider_with_client(sdk_response([1], [2]), sdk_response([1, 2]))
    with pytest.raises(EmbeddingProviderError):
        asyncio.run(provider.embed_texts(["a", "b", "c"]))


@pytest.mark.parametrize("kind", ["connection", "timeout", "status"])
def test_upstream_errors_do_not_expose_body_or_credentials(kind) -> None:
    request = httpx.Request("POST", "http://example.test/v1/embeddings")
    if kind == "status":
        error = APIStatusError(
            "private-secret", response=httpx.Response(401, request=request),
            body={"api_key": "private-secret"},
        )
    elif kind == "timeout":
        error = APITimeoutError(request=request)
    else:
        error = APIConnectionError(message="private-secret", request=request)
    provider, _ = provider_with_client(error)
    with pytest.raises(EmbeddingProviderError, match="request failed") as captured:
        asyncio.run(provider.embed_text("text"))
    assert "private-secret" not in str(captured.value)


def test_embedding_logs_metadata_without_input_text_or_key(caplog) -> None:
    provider, _ = provider_with_client(sdk_response([1, 2]))
    with caplog.at_level(logging.INFO, logger="app.embedding.provider"):
        asyncio.run(provider.embed_text("private-code-text"))
    assert "dimension=2" in caplog.text and "texts=1" in caplog.text
    assert "private-code-text" not in caplog.text and "fake-key" not in caplog.text


class StubEmbeddingProvider(EmbeddingProvider):
    def __init__(self, vectors):
        self.vectors = vectors
        self.calls = []
        self.closed = False

    @property
    def model_name(self) -> str:
        return "stub-embedding"

    async def embed_texts(self, texts):
        self.calls.append(list(texts))
        return self.vectors

    async def aclose(self):
        self.closed = True


def call_with_stub(path, body, vectors):
    stub = StubEmbeddingProvider(vectors)

    @asynccontextmanager
    async def stub_context():
        try:
            yield stub
        finally:
            await stub.aclose()

    with patch("app.api.embeddings.get_embedding_provider", stub_context), TestClient(app) as http:
        response = http.post(path, json=body)
    return response, stub


def test_debug_returns_actual_dimension_and_only_eight_coordinates() -> None:
    response, stub = call_with_stub("/api/embeddings/debug", {"text": "代码"}, [list(range(1, 13))])
    assert response.status_code == 200
    assert response.json() == dict(model="stub-embedding", dimension=12, preview=list(range(1, 9)))
    assert stub.calls == [["代码"]] and stub.closed


def test_similarity_embeds_both_texts_together_and_returns_cosine() -> None:
    response, stub = call_with_stub(
        "/api/embeddings/similarity", {"text_a": "订单退款", "text_b": "退还订单金额"},
        [[1, 0], [1, 1]],
    )
    assert response.status_code == 200
    assert response.json()["similarity"] == pytest.approx(1 / math.sqrt(2))
    assert response.json()["dimension"] == 2
    assert stub.calls == [["订单退款", "退还订单金额"]] and stub.closed


@pytest.mark.parametrize("path,body", [
    ("/api/embeddings/debug", {}), ("/api/embeddings/debug", {"text": " "}),
    ("/api/embeddings/debug", {"text": 12}),
    ("/api/embeddings/debug", {"text": "x" * 8193}),
    ("/api/embeddings/debug", {"text": "text", "preview_length": 1000}),
    ("/api/embeddings/similarity", {"text_a": "a"}),
    ("/api/embeddings/similarity", {"text_a": "a", "text_b": " "}),
    ("/api/embeddings/similarity", {"text_a": "a", "text_b": "b", "extra": 1}),
])
def test_invalid_http_body_does_not_construct_provider(path, body) -> None:
    with patch("app.embedding.factory.EmbeddingProviderFactory.create") as factory, TestClient(app) as http:
        assert http.post(path, json=body).status_code == 422
    factory.assert_not_called()


@pytest.mark.parametrize("path,body,vectors", [
    ("/api/embeddings/debug", {"text": "text"}, [[float("nan")]]),
    ("/api/embeddings/similarity", {"text_a": "a", "text_b": "b"}, [[1], [1, 2]]),
    ("/api/embeddings/similarity", {"text_a": "a", "text_b": "b"}, [[1]]),
])
def test_route_rejects_malformed_provider_vectors_and_closes(path, body, vectors) -> None:
    response, stub = call_with_stub(path, body, vectors)
    assert response.status_code == 502 and stub.closed


@pytest.mark.parametrize("failure,status", [
    (EmbeddingConfigurationError("EMBEDDING_MODEL is required."), 503),
    (EmbeddingProviderError("The configured embedding provider request failed."), 502),
])
def test_http_error_status_and_health_are_independent(failure, status) -> None:
    with patch("app.embedding.factory.EmbeddingProviderFactory.create", side_effect=failure), TestClient(app) as http:
        response = http.post("/api/embeddings/debug", json={"text": "text"})
        assert response.status_code == status and response.json() == {"detail": str(failure)}
        assert http.get("/health").status_code == 200


@pytest.mark.parametrize("raises", [False, True])
def test_request_scope_closes_provider_after_success_and_failure(raises) -> None:
    stub = StubEmbeddingProvider([[1]])

    async def exercise():
        async with get_embedding_provider() as provider:
            assert provider is stub
            if raises:
                raise EmbeddingProviderError("safe error")

    with patch("app.embedding.factory.EmbeddingProviderFactory.create", return_value=stub):
        if raises:
            with pytest.raises(EmbeddingProviderError):
                asyncio.run(exercise())
        else:
            asyncio.run(exercise())
    assert stub.closed
