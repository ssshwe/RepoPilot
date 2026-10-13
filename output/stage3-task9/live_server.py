"""Record Task 9 real retrieval/inference; no mocked model or production index writes."""
import argparse
import asyncio
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys

import uvicorn

args = argparse.ArgumentParser()
args.add_argument("--run", choices=("isolated", "production", "final-isolated", "final-production"), required=True)
args.add_argument("--port", type=int, required=True)
args.add_argument("--collection")
options = args.parse_args()
ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent / options.run
OUT.mkdir(exist_ok=True)
sys.path.insert(0, str(ROOT / "services/api"))
if options.collection is not None:
    os.environ["QDRANT_COLLECTION"] = options.collection
# Deliberately fake runtime canary. Never load real environment values into messages.
os.environ["REPOPILOT_TASK9_PRIVATE_CANARY"] = "TASK9_FAKE_RUNTIME_PRIVATE_VALUE_20261007"

from app.api import code_chat
from app.citations import CitationValidator
from app.context import ContextBuilder
from app.llm.factory import get_llm_provider
from app.main import app

context_count = generation_count = citation_count = 0
http_calls = []
attached = set()
original_build = ContextBuilder.build
original_validate = CitationValidator.validate


def save(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def record_build(self, repository, results):
    global context_count
    value = original_build(self, repository, results)
    context_count += 1
    save(f"context-{context_count:03}.json", {"input": [r.model_dump() for r in results], "context": asdict(value)})
    return value


def record_validate(self, repository, source_ids, **kwargs):
    global citation_count
    value = original_validate(self, repository, source_ids, **kwargs)
    citation_count += 1
    save(f"citation-{citation_count:03}.json", {"repository": repository, "input_ids": list(source_ids),
                                             "context_characters": kwargs["context"].characters_used,
                                             "citations": [c.model_dump() for c in value]})
    return value


async def record_http(request):
    if request.url.path.endswith("/chat/completions"):
        body = json.loads(request.content)
        http_calls.append({"method": request.method, "host": request.url.host, "path": request.url.path,
                           "messages": body["messages"], "request_fields": sorted(body),
                           "tools_advertised": "tools" in body or "tool_choice" in body,
                           "schema_properties": sorted(body["response_format"]["json_schema"]["schema"]["properties"])})


async def record_reply(response):
    if response.request.url.path.endswith("/chat/completions"):
        await response.aread()
        body = response.json()
        choices = body.get("choices", [])
        # Only fixture/control model text; never record request/response headers.
        http_calls[-1]["provider_reply"] = {
            "http_status": response.status_code,
            "content": choices[0].get("message", {}).get("content") if choices else None,
            "finish_reason": choices[0].get("finish_reason") if choices else None,
        }


class RecordingProvider:
    def __init__(self, provider):
        self.provider = provider

    @property
    def model_name(self):
        return self.provider.model_name

    async def complete_structured(self, messages, response_model):
        global generation_count
        generation_count += 1
        number, before = generation_count, len(http_calls)
        record = {"kind": "real unchanged local Provider call, no mocked inference",
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
        provider._client._client.event_hooks.setdefault("request", []).append(record_http)
        provider._client._client.event_hooks.setdefault("response", []).append(record_reply)
        attached.add(id(provider))
    return RecordingProvider(provider)


async def main():
    ContextBuilder.build = record_build
    CitationValidator.validate = record_validate
    code_chat.get_llm_provider = record_resolver
    stop = OUT / "stop-server"
    assert not stop.exists(), "Use a fresh acceptance run."
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=options.port, log_level="warning"))
    task = asyncio.create_task(server.serve())
    save("server.json", {"pid": os.getpid(), "url": f"http://127.0.0.1:{options.port}",
                         "process_collection_override": options.collection, "local_acceptance_only": True})
    try:
        while not stop.exists() and not task.done():
            await asyncio.sleep(.5)
        server.should_exit = True
        await task
    finally:
        if get_llm_provider.cache_info().currsize:
            await get_llm_provider()._client.close()
        save("server-finished.json", {"contexts_built": context_count, "generation_calls": generation_count,
                                      "citation_validation_calls": citation_count,
                                      "actual_generation_http_requests": len(http_calls), "stopped": True})


if __name__ == "__main__":
    asyncio.run(main())
