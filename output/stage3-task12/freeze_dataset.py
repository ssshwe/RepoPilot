"""Create source-grounded Stage 3 labels before any new model evaluation."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
source = ROOT / "evaluation/datasets/stage2_retrieval.json"
base = json.loads(source.read_bytes())
claims = {
    "refund-service": "RefundService.refund 在 12–18 行检查 orderId 为空或空白，再调用 wallets.credit(userId, amount)。",
    "wallet-credit": "WalletService.credit 在 10–18 行检查 userId 和正金额，用 balances.merge 与 BigDecimal::add 累加余额。",
    "wallet-default-balance": "WalletService.balanceOf 在 20–22 行调用 getOrDefault(userId, BigDecimal.ZERO)。",
    "refund-controller": "OrderController.refundOrder 在 13–15 行调用 refunds.refund(orderId, userId, amount)。",
    "refund-form": "RefundPanel.vue 用 reactive 收集三字段；submitRefund 检查空白 ID 与正金额，再 emit refund 事件。",
    "refund-request-type": "types.ts 的 RefundRequest 定义 orderId、userId 为 string，amount 为 number。",
    "orders-schema": "schema.sql 的 orders 表 amount 是 DECIMAL(12, 2) NOT NULL，status 是 VARCHAR(20) NOT NULL。",
    "wallets-schema": "schema.sql 的 wallets.balance 是 DECIMAL(12, 2) NOT NULL DEFAULT 0。",
    "fixture-documentation": "README 列出主要源文件；notes.txt 扩展名不支持，package-lock.json 被固定忽略规则排除。",
    "repository-metadata": "config/repository.json 定义 name、purpose、features，包含 order-refund 与 wallet-balance。",
    "refund-validation-files": "orderId 校验在 RefundService.refund；钱包 userId 和金额校验在 WalletService.credit。",
    "refund-call-chain": "三处实现为 OrderController.refundOrder → RefundService.refund → WalletService.credit；不能用 README 代替实现证据。",
}
symbols = {
    "refund-service": [("backend/service/RefundService.java", "RefundService.refund")],
    "wallet-credit": [("backend/service/WalletService.java", "WalletService.credit")],
    "wallet-default-balance": [("backend/service/WalletService.java", "WalletService.balanceOf")],
    "refund-controller": [("backend/controller/OrderController.java", "OrderController.refundOrder")],
    "refund-request-type": [("frontend/src/types.ts", "RefundRequest")],
    "refund-validation-files": [("backend/service/RefundService.java", "RefundService.refund"), ("backend/service/WalletService.java", "WalletService.credit")],
    "refund-call-chain": [("backend/controller/OrderController.java", "OrderController.refundOrder"), ("backend/service/RefundService.java", "RefundService.refund"), ("backend/service/WalletService.java", "WalletService.credit")],
}
cases = []
for case in base["cases"]:
    cases.append(case | {"mode": "code_chat", "group": "canonical", "repository": base["repository"],
                         "expected_answer": claims[case["id"]], "expected_symbols": [dict(path=p, symbol=s) for p, s in symbols.get(case["id"], [])],
                         "context_budget_characters": None})
chain = next(c for c in base["cases"] if c["id"] == "refund-call-chain")
refund = base["cases"][0]
for identifier, query in [
    ("issue-refund", "商城退款接口可能存在重复退款问题，请分析应该重点检查哪些代码。"),
    ("issue-direct", "检查 RefundService.refund 是否会在重复订单调用时再次执行 wallets.credit，给出测试建议。"),
]:
    cases.append({"id": identifier, "mode": "issue_analysis", "group": "supplementary", "repository": base["repository"],
                  "query": query, "expected_paths": chain["expected_paths"], "evidence": chain["evidence"],
                  "expected_symbols": [dict(path=p, symbol=s) for p, s in symbols["refund-call-chain"]],
                  "expected_answer": "引用可见退款实现，区分待验证 Issue 与事实；给出具体重复调用测试及待核实幂等检查点，不断言整个仓库无幂等性。",
                  "context_budget_characters": None})
cases.append({"id": "empty-repository", "mode": "code_chat", "group": "supplementary", "repository": "stage3-task12-not-indexed",
              "query": refund["query"], "expected_paths": [], "evidence": [], "expected_symbols": [],
              "expected_answer": "空检索时固定说明当前证据不足，返回空引用，不调用生成模型。", "context_budget_characters": None})
cases.append(refund | {"id": "refund-budget700", "mode": "code_chat", "group": "supplementary", "repository": base["repository"],
                      "expected_symbols": [dict(path=p, symbol=s) for p, s in symbols["refund-service"]],
                      "expected_answer": "只依据实际输出的完整行；若未包含钱包调用行，不能断言调用内容或引用原 12–18 大范围。",
                      "context_budget_characters": 700})
dataset = {"schema_version": 1, "name": "stage3-demo-grounding-v1", "repository": base["repository"],
           "description": "16 fixed cases: unchanged 12 Stage 2 queries, two known Issue failures, empty repository, 700-character context. Learning fixture, not a held-out benchmark.",
           "labels_author": "development assistant source inspection; user human review pending",
           "source_dataset": "evaluation/datasets/stage2_retrieval.json", "source_dataset_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
           "source_sha256": base["source_sha256"], "cases": cases}
target = ROOT / "evaluation/datasets/stage3_grounding.json"
with target.open("x", encoding="utf-8") as stream:
    stream.write(json.dumps(dataset, ensure_ascii=False, indent=2) + "\n")
print(json.dumps({"frozen_cases": len(cases), "canonical": 12, "supplementary": 4, "model_calls_yet": 0}))
