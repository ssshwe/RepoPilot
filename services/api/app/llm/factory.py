from functools import lru_cache

from app.core.config import Settings, get_settings
from app.core.exceptions import LLMConfigurationError
from app.llm.base import LLMProvider
from app.llm.compatible_provider import OpenAICompatibleProvider
from app.llm.openai_provider import OpenAIProvider


class ProviderFactory:
    """Create the configured provider without exposing SDK clients to routes."""

    @staticmethod
    def create(settings: Settings) -> LLMProvider:
        provider_name = settings.llm_provider.strip().lower()
        api_key = settings.llm_api_key.get_secret_value().strip()
        model = settings.llm_model.strip()

        if not api_key or not model:
            raise LLMConfigurationError(
                "LLM_API_KEY and LLM_MODEL must be configured before using chat."
            )

        if provider_name == "openai":
            return OpenAIProvider(
                api_key=api_key,
                model=model,
                timeout_seconds=settings.llm_timeout_seconds,
            )

        if provider_name == "openai_compatible":
            base_url = settings.llm_base_url.strip()
            if not base_url:
                raise LLMConfigurationError(
                    "LLM_BASE_URL is required for the openai_compatible provider."
                )
            return OpenAICompatibleProvider(
                api_key=api_key,
                base_url=base_url,
                model=model,
                timeout_seconds=settings.llm_timeout_seconds,
            )

        raise LLMConfigurationError(
            f"Unsupported LLM_PROVIDER: {provider_name or '<empty>'}."
        )


@lru_cache
def get_llm_provider() -> LLMProvider:
    return ProviderFactory.create(get_settings())

