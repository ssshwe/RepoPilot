"""Verified official CUDA wheel download using bounded ranged requests."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
from pathlib import Path
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
SIZE = 3461384651
SHA = "0ad925202387f4e7314302a1b4f8860fa824357f9b1466d7992bf276370ebcff"
URL = "https://download.pytorch.org/whl/cu128/torch-2.8.0%2Bcu128-cp312-cp312-win_amd64.whl"
DEST = ROOT/".tools/reranker-wheels"
DEST.mkdir(exist_ok=True)
STEP = 16*1024*1024


def part(index):
    start, end = index*STEP, min(SIZE, (index+1)*STEP)-1
    path = DEST/f"torch-{index:03d}.partial"
    if path.exists() and path.stat().st_size == end-start+1:
        return path
    for attempt in range(4):
        try:
            request = urllib.request.Request(URL+f"?part={index}", headers={"Range": f"bytes={start}-{end}"})
            with urllib.request.urlopen(request, timeout=60) as response:
                assert response.status == 206 and response.headers.get("Content-Range") == f"bytes {start}-{end}/{SIZE}"
                data = response.read()
            assert len(data) == end-start+1
            path.write_bytes(data)
            return path
        except Exception:
            if attempt == 3:
                raise
            time.sleep(1)


with ThreadPoolExecutor(max_workers=8) as pool:
    paths = list(pool.map(part, range((SIZE+STEP-1)//STEP)))
digest = hashlib.sha256()
target = DEST/"torch-2.8.0+cu128-cp312-cp312-win_amd64.whl"
with target.open("xb") as stream:
    for path in paths:
        block = path.read_bytes()
        digest.update(block)
        stream.write(block)
assert target.stat().st_size == SIZE and digest.hexdigest() == SHA
print(f"Verified official PyTorch CUDA wheel: {SIZE} bytes / {SHA}", flush=True)
