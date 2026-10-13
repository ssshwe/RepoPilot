import json
from pathlib import Path

import httpx

OUT = Path(__file__).resolve().parent
QUERY = "退款服务在哪里校验订单编号，然后把退款金额转回钱包？"


def main():
    records = []
    for run, port, requests in (
        ("full", 8018, [("refund", "demo-repository"), ("empty", "stage3-task8-not-indexed")]),
        ("budget", 8019, [("refund", "demo-repository")]),
    ):
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=180) as client:
            assert client.get("/health").status_code == 200
            for name, repository in requests:
                payload = {"repository": repository, "question": QUERY, "top_k": 5}
                response = client.post("/api/code-chat", json=payload)
                record = {"kind": "real HTTP; real configured Ollama and existing Qdrant; no mocked inference",
                          "request": payload, "http_status": response.status_code, "response": response.json()}
                (OUT / run / f"response-{name}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                item = {"run": run, "case": name, "http_status": response.status_code,
                        "result_status": record["response"].get("status"),
                        "source_ids": record["response"].get("source_ids"),
                        "citations": record["response"].get("citations")}
                records.append(item)
                print(json.dumps(item), flush=True)
    (OUT / "live-requests.json").write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
