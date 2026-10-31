"""Resume the verified public model transfer; no inference or report retries."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
DEST = ROOT/".tools/reranker-model"
MODEL = "BAAI/bge-reranker-v2-m3"
REV = "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e"
NAMES = ("config.json", "model.safetensors", "sentencepiece.bpe.model", "special_tokens_map.json", "tokenizer.json", "tokenizer_config.json")
with urllib.request.urlopen(f"https://huggingface.co/api/models/{MODEL}/tree/{REV}", timeout=30) as response:
    inventory = {f["path"]: f for f in json.load(response) if f["type"] == "file"}
temporary = DEST/"model.safetensors.partial"
size = inventory["model.safetensors"]["size"]
prefix = temporary.stat().st_size
STEP = 16*1024*1024


def part(start):
    end = min(size, start+STEP)-1
    target = DEST/f"model-tail-{start}.partial"
    if target.exists() and target.stat().st_size == end-start+1:
        return target
    for attempt in range(4):
        try:
            url = f"https://huggingface.co/{MODEL}/resolve/{REV}/model.safetensors?part={start}&r={time.time_ns()}"
            with urllib.request.urlopen(urllib.request.Request(url, headers={"Range": f"bytes={start}-{end}"}), timeout=60) as response:
                assert response.status == 206 and response.headers.get("Content-Range") == f"bytes {start}-{end}/{size}"
                data = response.read()
            assert len(data) == end-start+1
            target.write_bytes(data)
            return target
        except Exception:
            if attempt == 3:
                raise


with ThreadPoolExecutor(max_workers=4) as pool:
    paths = list(pool.map(part, range(prefix, size, STEP)))
with temporary.open("ab") as stream:
    for path in paths:
        stream.write(path.read_bytes())
assert temporary.stat().st_size == size
target = DEST/"model.safetensors"
assert not target.exists()
digest = hashlib.file_digest(temporary.open("rb"), "sha256").hexdigest()
assert digest == inventory["model.safetensors"]["lfs"]["oid"]
temporary.rename(target)
manifest = {"model": MODEL, "revision": REV, "files": {}}
for name in NAMES:
    path, item = DEST/name, inventory[name]
    if not path.exists():
        with urllib.request.urlopen(f"https://huggingface.co/{MODEL}/resolve/{REV}/{name}?r={time.time_ns()}", timeout=60) as response:
            with path.open("xb") as stream:
                stream.write(response.read())
    digest = hashlib.file_digest(path.open("rb"), "sha256").hexdigest()
    assert path.stat().st_size == item["size"]
    expected = item.get("lfs", {}).get("oid")
    if expected:
        assert digest == expected
    else:
        raw = path.read_bytes()
        assert hashlib.sha1(f"blob {len(raw)}\0".encode()+raw).hexdigest() == item["oid"]
    manifest["files"][name] = {"bytes": item["size"], "sha256": digest, "upstream_lfs_sha256": expected}
    print(f"Verified {name}: {item['size']} bytes", flush=True)
with (DEST/"manifest.json").open("x", encoding="utf-8") as stream:
    json.dump(manifest, stream, ensure_ascii=False, indent=2)
print("All model files verified", flush=True)
