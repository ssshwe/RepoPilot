"""Download a pinned public BGE snapshot; verify LFS SHA-256, never load remote code."""
import argparse
import hashlib
import json
from pathlib import Path
import time
import urllib.request

MODEL = "BAAI/bge-reranker-v2-m3"
REVISION = "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e"
FILES = ("config.json", "model.safetensors", "sentencepiece.bpe.model", "special_tokens_map.json", "tokenizer.json", "tokenizer_config.json")


def download(destination):
    destination.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(f"https://huggingface.co/api/models/{MODEL}/tree/{REVISION}", timeout=60) as response:
        inventory = {f["path"]: f for f in json.load(response) if f["type"] == "file"}
    manifest = {"model": MODEL, "revision": REVISION, "files": {}}
    for name in FILES:
        item, target = inventory[name], destination/name
        if target.exists():
            raise FileExistsError(f"Refusing to replace existing model file: {name}")
        temporary = destination/(name+".partial")
        if temporary.exists():
            raise FileExistsError(f"Inspect incomplete download before retrying: {temporary}")
        digest = hashlib.sha256()
        # A unique request avoids stale signed CDN redirects from intermediary caches.
        url = f"https://huggingface.co/{MODEL}/resolve/{REVISION}/{name}?download=true&request={time.time_ns()}"
        started = time.perf_counter()
        with urllib.request.urlopen(url, timeout=90) as response, temporary.open("xb") as stream:
            while block := response.read(4*1024*1024):
                stream.write(block)
                digest.update(block)
        if temporary.stat().st_size != item["size"]:
            raise ValueError(f"Download length differs: {name}")
        expected = item.get("lfs", {}).get("oid")
        if expected and digest.hexdigest() != expected:
            raise ValueError(f"Download SHA-256 differs: {name}")
        if not expected:
            raw = temporary.read_bytes()
            if hashlib.sha1(f"blob {len(raw)}\0".encode()+raw).hexdigest() != item["oid"]:
                raise ValueError(f"Download Git blob identity differs: {name}")
        temporary.rename(target)
        manifest["files"][name] = {"bytes": item["size"], "sha256": digest.hexdigest(), "upstream_lfs_sha256": expected}
        print(f"{name}: {item['size']} bytes, {time.perf_counter()-started:.1f}s", flush=True)
    with (destination/"manifest.json").open("x", encoding="utf-8") as stream:
        json.dump(manifest, stream, ensure_ascii=False, indent=2)
    print("Pinned model download verified", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    download(parser.parse_args().destination.resolve())
