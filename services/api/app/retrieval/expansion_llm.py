"""Optional one-attempt Structured Output generator using existing LLM settings."""
import asyncio
import json
from time import perf_counter

import httpx
from openai import AsyncOpenAI

from app.llm.compatible_provider import _provider_json_schema
from app.retrieval.expansion import ExpansionSuggestions


class StructuredQueryGenerator:
    def __init__(self, settings, *, transport=None):
        self.settings, self.transport = settings, transport
        self.last_receipt = None

    async def generate(self, messages, policy):
        settings = self.settings
        if settings.llm_provider not in {"openai", "openai_compatible"}:
            raise ValueError("Unsupported expansion provider.")
        model = settings.llm_model.strip()
        key = settings.llm_api_key.get_secret_value().strip()
        if not model or not key:
            raise ValueError("Expansion model is not configured.")
        started = perf_counter()
        receipt = {"model": model, "request": {"messages": messages, "max_tokens": 512, "temperature": 0,
            "schema": ExpansionSuggestions.model_json_schema()}, "http_attempts": 0, "status": "pending",
            "raw_content": None, "finish_reason": None, "usage": None, "latency_ms": 0.}
        self.last_receipt = receipt
        async def request_hook(request):
            receipt["http_attempts"] += 1
        http = httpx.AsyncClient(trust_env=False, transport=self.transport, event_hooks={"request": [request_hook]})
        client = AsyncOpenAI(api_key=key, base_url="https://api.openai.com/v1" if settings.llm_provider == "openai" else settings.llm_base_url.rstrip("/"),
            max_retries=0, timeout=policy.llm_timeout_seconds, http_client=http)
        try:
            reply = await client.chat.completions.create(model=model, messages=messages,
                response_format={"type": "json_schema", "json_schema": {"name": "ExpansionSuggestions", "strict": True,
                    "schema": _provider_json_schema(ExpansionSuggestions.model_json_schema())}},
                max_tokens=512, temperature=0, stream=False)
            choice = reply.choices[0] if reply.choices else None
            raw = choice.message.content if choice else None
            # Retain malformed JSON for diagnosis, with an independent response
            # character limit. Never retain headers, credentials or error text.
            receipt["raw_content"] = raw[:16000] if isinstance(raw, str) else None
            receipt["finish_reason"] = choice.finish_reason if choice else None
            receipt["usage"] = {name: getattr(reply.usage, name, None) for name in ("prompt_tokens", "completion_tokens", "total_tokens")} if reply.usage else None
            receipt["status"] = "completed" if isinstance(raw, str) and len(raw) <= 16000 and receipt["finish_reason"] == "stop" else "invalid_output"
        except asyncio.CancelledError:
            receipt["status"] = "cancelled"
            raise
        except Exception:
            receipt["status"] = "provider_failed"
        finally:
            await client.close()
            receipt["latency_ms"] = round((perf_counter()-started)*1000, 3)
        return json.loads(json.dumps(receipt, ensure_ascii=False, allow_nan=False))
