"""Local HTTP acceptance server; record actual inputs/outputs without replacing inference."""
import asyncio
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys

import uvicorn

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent / "final"
OUT.mkdir(exist_ok=True)
sys.path.insert(0, str(ROOT / "services/api"))

from app.api import code_chat
from app.context import ContextBuilder
from app.llm.factory import get_llm_provider
from app.main import app

context_count = generation_count = 0
http_calls = []
attached = set()
original_build = ContextBuilder.build


def save(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def record_build(self, repository, results):
    global context_count
    value = original_build(self, repository, results)
    context_count += 1
    save(f"context-{context_count:03}.json", {"input": [r.model_dump() for r in results], "context": asdict(value)})
    return value


async def record_http_request(request):
    # Do not record headers, authorization, keys or upstream response bodies.
    if request.url.path.endswith("/chat/completions"):
        http_calls.append({"method": request.method, "host": request.url.host, "path": request.url.path})


class RecordingProvider:
    def __init__(self, provider):
        self.provider = provider

    @property
    def model_name(self):
        return self.provider.model_name

    async def complete_structured(self, messages, response_model):
        global generation_count
        generation_count += 1
        number = generation_count
        before = len(http_calls)
        record = {"kind": "real local Provider, unchanged input/schema/output; no mocked inference",
                  "model": self.model_name, "messages": [m.model_dump() for m in messages],
                  "schema": response_model.model_json_schema()}
        try:
            result = await self.provider.complete_structured(messages, response_model)
            record["result"] = result.model_dump()
            return result
        except Exception as error:
            record["error_type"] = type(error).__name__
            raise
        finally:
            record["actual_generation_http_requests"] = http_calls[before:]
            save(f"generation-{number:03}.json", record)


def record_resolver():
    provider = get_llm_provider()
    if id(provider) not in attached:
        provider._client._client.event_hooks.setdefault("request", []).append(record_http_request)
        attached.add(id(provider))
    return RecordingProvider(provider)


async def main():
    ContextBuilder.build = record_build
    code_chat.get_llm_provider = record_resolver
    stop = OUT / "stop-server"
    assert not stop.exists(), "Use a fresh acceptance run; stop marker already exists."
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8017, log_level="warning"))
    task = asyncio.create_task(server.serve())
    save("server.json", {"pid": os.getpid(), "url": "http://127.0.0.1:8017", "local_acceptance_only": True})
    try:
        while not stop.exists() and not task.done():
            await asyncio.sleep(.5)
        server.should_exit = True
        await task
    finally:
        if get_llm_provider.cache_info().currsize:
            await get_llm_provider()._client.close()
        save("server-finished.json", {"contexts_built": context_count, "generation_calls": generation_count,
                                      "actual_generation_http_requests": len(http_calls), "stopped": True})


if __name__ == "__main__":
    asyncio.run(main())
