"""One offline inference subprocess; bounded admission and kill-on-timeout cleanup."""
import asyncio
from contextlib import suppress
import json
import os
from pathlib import Path
from uuid import uuid4

from app.retrieval.rerank import MODEL_ID, MODEL_REVISION, RerankFailure, validate_reply


class ProcessReranker:
    def __init__(self, python, model_directory, *, device="cuda", startup_timeout=90., worker_script=None):
        if device not in {"cuda", "cpu"} or type(startup_timeout) not in (int, float) or not 0.01 <= startup_timeout <= 120:
            raise ValueError("Invalid reranker runtime configuration.")
        self.python = Path(python).resolve()
        self.model_directory = Path(model_directory).resolve()
        self.worker_script = Path(worker_script or Path(__file__).with_name("reranker_worker.py")).resolve()
        self.device, self.startup_timeout = device, startup_timeout
        self.process = None
        self.ready = None
        self.busy = False
        self.closed = False
        self.active_task = None

    async def _stop(self):
        process, self.process = self.process, None
        self.ready = None
        if process is not None:
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            # Process owns no children; waiting confirms lock and GPU release.
            await asyncio.wait_for(process.wait(), 5.)

    async def _read(self):
        line = await self.process.stdout.readline()
        if not line or len(line) > 2*1024*1024:
            raise RerankFailure("worker_failed")
        try:
            value = json.loads(line)
        except (ValueError, UnicodeError) as exc:
            raise RerankFailure("invalid_response") from exc
        if not isinstance(value, dict):
            raise RerankFailure("invalid_response")
        if value.get("error"):
            reason = value["error"]
            raise RerankFailure(reason if reason in {"busy", "model_unavailable", "input_limit", "worker_failed", "out_of_memory"} else "worker_failed")
        return value

    async def _start(self):
        if self.process is not None and self.process.returncode is None:
            return self.ready
        await self._stop()
        env = dict(os.environ, HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1", PYTHONIOENCODING="utf-8", TOKENIZERS_PARALLELISM="false")
        try:
            self.process = await asyncio.create_subprocess_exec(str(self.python), "-u", str(self.worker_script),
                "--model-directory", str(self.model_directory), "--device", self.device,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                limit=2*1024*1024, env=env, **({"creationflags": 0x08000000} if os.name == "nt" else {}))
            ready = await self._read()
            if ready.get("event") != "ready" or ready.get("model") != MODEL_ID or ready.get("revision") != MODEL_REVISION:
                raise RerankFailure("invalid_response")
            self.ready = ready
            return ready
        except OSError as exc:
            raise RerankFailure("model_unavailable") from exc

    def _admit(self):
        if self.closed:
            raise RerankFailure("closed")
        if self.busy:
            raise RerankFailure("busy")
        self.busy = True
        self.active_task = asyncio.current_task()

    async def start(self):
        self._admit()
        try:
            async with asyncio.timeout(self.startup_timeout):
                return await self._start()
        except TimeoutError as exc:
            await self._stop()
            raise RerankFailure("timeout") from exc
        except BaseException:
            await self._stop()
            raise
        finally:
            self.busy = False
            self.active_task = None

    async def score(self, query, documents, policy):
        self._admit()
        try:
            async with asyncio.timeout(policy.timeout_seconds):
                await self._start()
                request_id = uuid4().hex
                body = {"id": request_id, "query": query, "documents": list(documents), "policy": policy.to_dict()}
                wire = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8") + b"\n"
                if len(wire) > 8*1024*1024:
                    raise RerankFailure("input_limit")
                self.process.stdin.write(wire)
                await self.process.stdin.drain()
                response = await self._read()
                if set(response) != {"id", "reply"} or response["id"] != request_id:
                    raise RerankFailure("invalid_response")
                reply = response["reply"]
                validate_reply(reply, query, documents, policy)
                if reply["manifest_sha256"] != self.ready.get("manifest_sha256"):
                    raise RerankFailure("invalid_response")
                return reply
        except asyncio.CancelledError:
            await self._stop()
            raise
        except TimeoutError as exc:
            await self._stop()
            raise RerankFailure("timeout") from exc
        except RerankFailure:
            await self._stop()
            raise
        except Exception as exc:
            await self._stop()
            raise RerankFailure("worker_failed") from exc
        finally:
            self.busy = False
            self.active_task = None

    async def aclose(self):
        self.closed = True
        active = self.active_task
        if active is not None and active is not asyncio.current_task() and not active.done():
            active.cancel()
            with suppress(asyncio.CancelledError):
                await active
        await self._stop()
