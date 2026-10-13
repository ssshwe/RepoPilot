"""Actual HTTP requests to the instrumented, unchanged production API pipeline."""
import json
from pathlib import Path

import httpx

OUT = Path(__file__).resolve().parent / "final"
CASES = [
    ("refund", {"repository": "demo-repository", "question": "退款服务在哪里校验订单编号，然后把退款金额转回钱包？", "top_k": 5}),
    ("no-index", {"repository": "stage3-task7-not-indexed", "question": "退款逻辑在哪里？", "top_k": 5}),
    ("unsupported", {"repository": "demo-repository", "question": "当前仓库是否实现了 PostgreSQL 数据库主从切换？请指出实现位置。", "top_k": 5}),
]


def main():
    records = []
    with httpx.Client(base_url="http://127.0.0.1:8017", trust_env=False, timeout=180) as client:
        assert client.get("/health").status_code == 200
        for name, request in CASES:
            response = client.post("/api/code-chat", json=request)
            record = {"kind": "real HTTP; real configured Ollama embedding/generation and existing Qdrant index",
                      "request": request, "http_status": response.status_code, "response": response.json()}
            (OUT / f"response-{name}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            records.append({"case": name, "status": response.status_code,
                            "result_status": record["response"].get("status"),
                            "source_ids": record["response"].get("source_ids"),
                            "generation_called": record["response"].get("generation", {}).get("called")})
            print(json.dumps(records[-1]), flush=True)
    (OUT / "live-requests.json").write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
