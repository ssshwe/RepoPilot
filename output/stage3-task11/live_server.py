"""Observe unchanged local Provider calls. Hooks do not record headers or keys."""
import asyncio
import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys

import uvicorn

ROOT = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser()
parser.add_argument("--run", choices=("live", "final", "bounded"), default="live")
options = parser.parse_args()
OUT = Path(__file__).resolve().parent / options.run
OUT.mkdir(exist_ok=True)
sys.path.insert(0, str(ROOT / "services/api"))
from app.api import issues
from app.citations import CitationValidator
from app.context import ContextBuilder
from app.llm.factory import get_llm_provider
from app.main import app

contexts = generations = validations = 0
http_calls = []
attached = set()
real_build, real_validate = ContextBuilder.build, CitationValidator.validate


def save(name, data):
    (OUT / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build(self, repository, results):
    global contexts
    context = real_build(self, repository, results)
    contexts += 1
    save(f"context-{contexts:03}.json", {"input": [r.model_dump() for r in results], "context": asdict(context)})
    return context


def validate(self, repository, ids, **kwargs):
    global validations
    result = real_validate(self, repository, ids, **kwargs)
    validations += 1
    save(f"citation-{validations:03}.json", {"repository": repository, "source_ids": list(ids), "citations": [c.model_dump() for c in result]})
    return result


async def request_hook(request):
    if request.url.path.endswith("/chat/completions"):
        body = json.loads(request.content)
        http_calls.append({"host": request.url.host, "path": request.url.path, "messages": body["messages"],
            "request_fields": sorted(body), "tools_advertised": "tools" in body or "tool_choice" in body,
            "schema_properties": sorted(body["response_format"]["json_schema"]["schema"]["properties"])})


async def response_hook(response):
    if response.request.url.path.endswith("/chat/completions"):
        await response.aread()
        choices = response.json().get("choices", [])
        http_calls[-1]["reply"] = {"http_status": response.status_code,
            "content": choices[0].get("message", {}).get("content") if choices else None,
            "finish_reason": choices[0].get("finish_reason") if choices else None}


class RecordingProvider:
    def __init__(self, provider): self.provider = provider
    @property
    def model_name(self): return self.provider.model_name
    async def complete_structured(self, messages, model):
        global generations
        generations += 1
        number, before = generations, len(http_calls)
        record = {"kind": "real unchanged local Provider call, no mocked inference", "model": self.model_name,
                  "messages": [m.model_dump() for m in messages], "schema": model.model_json_schema()}
        try:
            result = await self.provider.complete_structured(messages, model)
            record["result"] = result.model_dump()
            return result
        except Exception as error:
            record["error_type"] = type(error).__name__
            raise
        finally:
            record["actual_generation_http_requests"] = http_calls[before:]
            save(f"generation-{number:03}.json", record)


def resolver():
    provider = get_llm_provider()
    if id(provider) not in attached:
        provider._client._client.event_hooks.setdefault("request", []).append(request_hook)
        provider._client._client.event_hooks.setdefault("response", []).append(response_hook)
        attached.add(id(provider))
    return RecordingProvider(provider)


async def main():
    ContextBuilder.build, CitationValidator.validate = build, validate
    issues.get_llm_provider = resolver
    stop = OUT / "stop-server"
    assert not stop.exists(), "Use a fresh acceptance run"
    save("server.json", {"pid": os.getpid(), "port": 8000, "acceptance_only": True})
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8000, log_level="warning"))
    task = asyncio.create_task(server.serve())
    try:
        while not stop.exists() and not task.done(): await asyncio.sleep(.5)
        server.should_exit = True
        await task
    finally:
        if get_llm_provider.cache_info().currsize: await get_llm_provider()._client.close()
        save("server-finished.json", {"contexts": contexts, "generations": generations,
            "validations": validations, "generation_http_requests": len(http_calls), "stopped": True})


if __name__ == "__main__": asyncio.run(main())
