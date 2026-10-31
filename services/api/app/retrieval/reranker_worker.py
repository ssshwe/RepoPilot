"""Offline Transformers BGE worker. No application imports or provider credentials."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import sys
from time import perf_counter

MODEL = "BAAI/bge-reranker-v2-m3"
REVISION = "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e"
WEIGHTS_SHA = "d9e3e081faff1eefb84019509b2f5558fd74c1a05a2c7db22f74174fcedb5286"
PINNED_GIT_BLOBS = {"config.json": "9f62673cb00ec41dcec8947b9ed16f6f2eb23ba2",
    "special_tokens_map.json": "b1879d702821e753ffe4245048eee415d54a9385",
    "tokenizer_config.json": "328a00a9a560aadcf2a3064f917517359eb3cc26"}
PINNED_LFS = {"model.safetensors": WEIGHTS_SHA,
    "sentencepiece.bpe.model": "cfc8146abe2a0488e9e2a0c56de7952f7c11ab059eca145a0a727afce0db2865",
    "tokenizer.json": "69564b696052886ed0ac63fa393e928384e0f8caada38c1f4864a9bfbf379c15"}


def sha(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def emit(value):
    print(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")), flush=True)


def file_sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(4*1024*1024):
            digest.update(block)
    return digest.hexdigest()


def acquire(directory):
    handle = (directory/".runtime.lock").open("a+b")
    handle.seek(0, os.SEEK_END)
    if not handle.tell():
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-directory", type=Path, required=True)
    parser.add_argument("--device", choices=("cpu", "cuda"), required=True)
    args = parser.parse_args()
    started = perf_counter()
    try:
        directory = args.model_directory.resolve(strict=True)
        lock = acquire(directory)
        if lock is None:
            emit({"error": "busy"})
            return
        manifest_path = directory/"manifest.json"
        manifest = json.loads(manifest_path.read_bytes())
        if manifest["model"] != MODEL or manifest["revision"] != REVISION or manifest["files"]["model.safetensors"]["sha256"] != WEIGHTS_SHA:
            raise ValueError("Pinned model mismatch")
        expected = {"config.json", "model.safetensors", "sentencepiece.bpe.model", "special_tokens_map.json", "tokenizer.json", "tokenizer_config.json"}
        if set(manifest["files"]) != expected:
            raise ValueError("Incomplete model manifest")
        for name, item in manifest["files"].items():
            path = directory/name
            if path.stat().st_size != item["bytes"] or file_sha(path) != item["sha256"]:
                raise ValueError("Local model drift")
            if name in PINNED_LFS and item["sha256"] != PINNED_LFS[name]:
                raise ValueError("Pinned tokenizer/weight identity differs")
            if name in PINNED_GIT_BLOBS:
                raw = path.read_bytes()
                if hashlib.sha1(f"blob {len(raw)}\0".encode()+raw).hexdigest() != PINNED_GIT_BLOBS[name]:
                    raise ValueError("Pinned configuration identity differs")
        import psutil
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
        torch.set_num_threads(2)
        if args.device == "cuda" and not torch.cuda.is_available():
            raise ValueError("CUDA unavailable")
        dtype = torch.float16 if args.device == "cuda" else torch.float32
        tokenizer = AutoTokenizer.from_pretrained(directory, local_files_only=True, trust_remote_code=False)
        model = AutoModelForSequenceClassification.from_pretrained(directory, local_files_only=True, trust_remote_code=False,
            use_safetensors=True, torch_dtype=dtype, attn_implementation="sdpa").to(args.device).eval()
        manifest_sha = file_sha(manifest_path)
        emit({"event": "ready", "model": MODEL, "revision": REVISION, "manifest_sha256": manifest_sha,
              "load_ms": round((perf_counter()-started)*1000, 3), "rss_bytes": psutil.Process().memory_info().rss,
              "device": args.device, "dtype": str(dtype).removeprefix("torch."), "pid": os.getpid(),
              "torch": torch.__version__, "transformers": __import__("transformers").__version__,
              "gpu": torch.cuda.get_device_name() if args.device == "cuda" else None,
              "cuda_runtime": torch.version.cuda, "system_memory_bytes": psutil.virtual_memory().total,
              "gpu_total_memory_bytes": torch.cuda.get_device_properties(0).total_memory if args.device == "cuda" else 0,
              "platform": platform.platform(), "python": sys.version})
    except Exception:
        emit({"error": "model_unavailable"})
        return
    while line := sys.stdin.buffer.readline(8*1024*1024+1):
        try:
            if len(line) > 8*1024*1024 or not line.endswith(b"\n"):
                emit({"error": "input_limit"})
                return
            request = json.loads(line)
            policy = request["policy"]
            query, documents = request["query"], request["documents"]
            # Repeat resource bounds inside the worker, independent of the API.
            if (not isinstance(query, str) or not 1 <= len(query) <= 4000 or not isinstance(documents, list)
                    or not 1 <= len(documents) <= policy["candidate_limit"] <= 60
                    or not 1 <= policy["batch_size"] <= 8 or not 64 <= policy["max_length"] <= 1024
                    or not 1 <= policy["max_query_tokens"] <= policy["max_length"]-4
                    or not 128 <= policy["max_document_characters"] <= 20000
                    or any(not isinstance(d, str) or len(d) > 100000 for d in documents)):
                emit({"error": "input_limit"})
                continue
            tokenize_started = perf_counter()
            query_ids = tokenizer.encode(query, add_special_tokens=False)
            if not 1 <= len(query_ids) <= policy["max_query_tokens"]:
                emit({"error": "input_limit"})
                continue
            encoded, traces = [], []
            for document in documents:
                capped = document[:policy["max_document_characters"]]
                doc_ids = tokenizer.encode(capped, add_special_tokens=False)
                # Preserve every original query token. Truncate only the passage.
                used = doc_ids[:policy["max_length"]-len(query_ids)-4]
                ids = tokenizer.build_inputs_with_special_tokens(query_ids, used)
                encoded.append({"input_ids": ids, "attention_mask": [1]*len(ids)})
                traces.append({"document_sha256": sha(document), "capped_sha256": sha(capped),
                    "original_characters": len(document), "capped_characters": len(capped), "query_tokens": len(query_ids),
                    "document_tokens": len(doc_ids), "used_document_tokens": len(used), "input_ids": ids})
            tokenization_ms = round((perf_counter()-tokenize_started)*1000, 3)
            if args.device == "cuda":
                torch.cuda.synchronize()
                torch.cuda.reset_peak_memory_stats()
            infer_started = perf_counter()
            scores, calls = [], 0
            with torch.inference_mode():
                for start in range(0, len(encoded), policy["batch_size"]):
                    inputs = tokenizer.pad(encoded[start:start+policy["batch_size"]], padding=True, return_tensors="pt").to(args.device)
                    output = model(**inputs, return_dict=True).logits.view(-1).float().cpu().tolist()
                    scores.extend(output)
                    calls += 1
            if args.device == "cuda":
                torch.cuda.synchronize()
            performance = {"inference_ms": round((perf_counter()-infer_started)*1000, 3), "tokenization_ms": tokenization_ms,
                "rss_bytes": psutil.Process().memory_info().rss, "cuda_peak_allocated_bytes": torch.cuda.max_memory_allocated() if args.device == "cuda" else 0,
                "cuda_peak_reserved_bytes": torch.cuda.max_memory_reserved() if args.device == "cuda" else 0,
                "forward_calls": calls, "pairs": len(documents), "device": args.device, "dtype": str(dtype).removeprefix("torch.")}
            emit({"id": request["id"], "reply": {"model": MODEL, "revision": REVISION, "manifest_sha256": manifest_sha,
                "query_sha256": sha(query), "policy": policy, "scores": scores, "inputs": traces, "performance": performance}})
        except (torch.cuda.OutOfMemoryError, MemoryError):
            emit({"error": "out_of_memory"})
            return
        except Exception:
            # No arbitrary exception strings, filesystem paths or code in errors.
            emit({"error": "worker_failed"})
            return
    lock.close()


if __name__ == "__main__":
    main()
