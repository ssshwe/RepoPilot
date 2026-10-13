"""Explicit source review for THIS saved run only. Never grade future inference with these decisions."""
import hashlib
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services/api"))
sys.path.insert(0, str(ROOT))
from evaluation.stage3.evaluate import fingerprint
from evaluation.stage4.state import read_json, write_new

RAW = ROOT / "evaluation/reports/stage3_grounding_20261013_task13_raw.json"
EXPECTED_REPORT_SHA256 = "e1f1010824beab6d5fd019d5c828f160dbcdcd122005d329dfb89e3d62f9b537"
assert hashlib.sha256(RAW.read_bytes()).hexdigest() == EXPECTED_REPORT_SHA256
report = read_json(RAW)
# Decisions recorded after reading the new responses, selected slices and source files.
decisions = [
    ("refund-service", "supported", True, ["S1"], None,
     "回答准确定位 RefundService.refund；S1 原始 12–18 行完整包含空订单编号检查和钱包调用，满足此位置问题。"),
    ("wallet-credit", "supported", True, ["S4"], None,
     "回答逐句给出实际 credit 方法的 userId、正金额检查及 balances.merge；S4 10–18 行支持完整答案。其余四个引用不是这些实现事实的必要证据。"),
    ("wallet-default-balance", "unsupported", False, [], None,
     "回答称未找到返回零逻辑，但所选 S1 20–22 行明确 getOrDefault(userId, BigDecimal.ZERO)，与证据矛盾。"),
    ("refund-controller", "unsupported", False, [], None,
     "回答把后端入口定位到 RefundService.java；实际入口是 OrderController.refundOrder 13–15 行。Top5 仅有 Controller imports/类字段，没有目标方法。"),
    ("refund-form", "nonresponsive", False, [], None,
     "原样复述问题，没有说明 reactive、字段或 emit；完整 RefundPanel.vue 已在所选 S1 1–22 行，引用有效不能使复述成为答案。"),
    ("refund-request-type", "supported", False, ["S1"], None,
     "orderId: string 由 S1 支持；同一 S1 也含 userId: string 和 amount: number，回答漏掉问题要求的另外两字段。"),
    ("orders-schema", "unsupported", False, [], None,
     "回答称订单 status/amount 在 RefundService.java 定义；所选 S1 schema.sql 1–11 行直接给出 orders DDL，所指 Java 文件 imports 不支持该位置。"),
    ("wallets-schema", "unsupported", False, [], None,
     "回答 balanceOf 方法而非 wallets 表 DEFAULT 0 定义；schema.sql 未进入 Top5，选中的 Java/JSON/README 不支持表默认值位置。"),
    ("fixture-documentation", "unsupported", False, [], None,
     "只列一个源文件且选中 repository.json S1，它不包含该文件路径；README 有完整清单和 notes.txt/package-lock.json 排除说明，但没有被引用。"),
    ("repository-metadata", "supported", True, ["S1"], None,
     "准确定位 config/repository.json，S1 1–5 行含 name/purpose/features；满足位置问题，其余引用并不支持这些具体元数据字段。"),
    ("refund-validation-files", "unsupported", False, [], None,
     "把校验指向 types.ts 和 Vue，实际后端订单编号检查是 RefundService.refund，钱包正金额检查是 WalletService.credit。所选 S4/S5 不能支持回答的后端范围。"),
    ("refund-call-chain", "supported", False, ["S4"], None,
     "RefundService.java 是真实链路的一个局部节点，所选 S4 的 refund 方法支持该事实；漏 Controller 与 Wallet 两文件，S2 imports 和 S3 类型定义不支持完整调用链。"),
    ("issue-refund", "unsupported", False, [], False,
     "声称代码被截断，但实际 Context 未截断且 S4 完整覆盖 refund 12–18 行；其余建议泛化，没有具体重复调用步骤/断言，未形成可验证分析或候选调用边。"),
    ("issue-direct", "unsupported", False, [], False,
     "选择 README 为候选调用节点，它不是可调用实现。复述待检查命题，没有证明重复执行；所谓测试建议是添加检查的代码修改，没有具体测试步骤，完整 refund 实现 S2 未选中。"),
    ("empty-repository", "abstention", True, [], None,
     "空检索返回固定证据不足，空 Sources/Citations 且零原生生成调用，符合负对照；无实质答案与 Citation 准确率可评。"),
    ("refund-budget700", "nonresponsive", False, [], None,
     "原样复述问题；真实 Context 698/700 字符仅输出 refund 12–15 行，17 行钱包调用不可见。Citation 正确缩短，但回答没有实质说明或明确证据不足。"),
]
assert [r["id"] for r in report["cases"]] == [d[0] for d in decisions]
reviews = []
for row, (identifier, outcome, complete, supporting, chain, reason) in zip(report["cases"], decisions, strict=True):
    selected = row["response"]["body"]["source_ids"]
    reviews.append({"id": identifier, "response_sha256": fingerprint(row["response"]), "outcome": outcome,
        "answer_supported": True if outcome == "supported" else None if outcome == "abstention" else False,
        "answer_complete": complete, "supporting_source_ids": supporting,
        "citation_batch_supports_answer": set(selected) == set(supporting) if selected else None,
        "candidate_chain_supported": chain, "reason": reason})
write_new(ROOT / "evaluation/reports/stage3_grounding_20261013_task13_review.json", {
    "schema_version": 1, "report_sha256": EXPECTED_REPORT_SHA256, "dataset_sha256": report["dataset_sha256"],
    "review_date": "2026-10-13", "reviewer": {"role": "development_assistant", "name": "Codex",
        "method": "New per-case review of actual replies, emitted Context, selected Sources and fixed source files; independent human review pending"},
    "support_definition": "Substantive query-relevant facts supported by selected slices; precise Symbol/path suffices for location questions. Complete additionally covers all requested fields/files/actions. Extra unrelated citations are judged separately.",
    "cases": reviews})
print("Saved explicit review bound to this new 16-case report")
