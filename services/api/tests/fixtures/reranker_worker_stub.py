"""Deterministic protocol fault worker; never imported by production code."""
import argparse
import hashlib
import json
import sys
import time

parser = argparse.ArgumentParser()
parser.add_argument("--model-directory")
parser.add_argument("--device")
args = parser.parse_args()
mode = args.model_directory.split("\\")[-1].split("/")[-1]
def emit(value):
    print(json.dumps(value), flush=True)
if mode == "startup-hang":
    time.sleep(60)
if mode == "unavailable":
    emit({"error": "model_unavailable"})
    sys.exit()
emit({"event": "ready", "model": "BAAI/bge-reranker-v2-m3", "revision": "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e", "manifest_sha256": "a"*64})
for line in sys.stdin:
    request = json.loads(line)
    if mode == "hang":
        time.sleep(60)
    if mode == "exit":
        sys.exit(2)
    if mode == "malformed":
        print("not json", flush=True)
        continue
    if mode == "input":
        emit({"error": "input_limit"})
        continue
    if mode == "oom":
        emit({"error": "out_of_memory"})
        continue
    sha = lambda text: hashlib.sha256(text.encode()).hexdigest()
    documents, policy = request["documents"], request["policy"]
    inputs = [{"document_sha256": sha(d), "capped_sha256": sha(d[:policy["max_document_characters"]]),
               "original_characters": len(d), "capped_characters": len(d[:policy["max_document_characters"]]),
               "query_tokens": 1, "document_tokens": 2, "used_document_tokens": 2, "input_ids": [0, 5, 2, 2, 3, 4, 2]} for d in documents]
    reply = {"model": "BAAI/bge-reranker-v2-m3", "revision": "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e", "manifest_sha256": "a"*64,
             "query_sha256": sha(request["query"]), "policy": policy, "scores": list(range(len(documents))), "inputs": inputs,
             "performance": {"inference_ms": 1., "tokenization_ms": 1., "rss_bytes": 10, "cuda_peak_allocated_bytes": 0,
                 "cuda_peak_reserved_bytes": 0, "forward_calls": (len(documents)+policy["batch_size"]-1)//policy["batch_size"],
                 "pairs": len(documents), "device": "cpu", "dtype": "float32"}}
    emit({"id": "wrong" if mode == "wrong-id" else request["id"], "reply": reply})
