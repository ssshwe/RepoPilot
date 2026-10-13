from openai import AsyncOpenAI

from app.llm.compatible_provider import OpenAICompatibleProvider


class OpenAIProvider(OpenAICompatibleProvider):
    """OpenAI-hosted Chat Completions provider using the SDK default base URL."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_seconds: float,
        client: AsyncOpenAI | None = None,
    ) -> None:
        if client is None:
            client = AsyncOpenAI(api_key=api_key, timeout=timeout_seconds)

        super().__init__(
            api_key=api_key,
            base_url="https://api.openai.com/v1",
            model=model,
            timeout_seconds=timeout_seconds,
            client=client,
        )

