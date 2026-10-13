"""Save the development assistant's explicit case-by-case source review, not an automated judge."""
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from evaluation.stage3.evaluate import fingerprint

path = ROOT / "evaluation/reports/stage3_grounding_20261012_task12_raw.json"
report = json.loads(path.read_bytes())
# These verdicts were entered after inspecting actual replies and source slices.
# They are fixed to these response fingerprints and must not grade future runs.
decisions = [
    ("refund-service", "supported", True, ["S1"], None,
     "回答 RefundService.refund；S1 是真实 12–18 行，确实在该方法校验订单编号并调用 wallets.credit。作为位置问题，方法名加精确引用足以定位。"),
    ("wallet-credit", "supported", True, ["S4"], None,
     "返回的 credit 方法体与 S4 10–18 行一致，包含 userId、正金额检查和 merge 累加。其余 S1/S2/S3/S5 没有直接支持这些完整实现事实，属于多余引用。"),
    ("wallet-default-balance", "unsupported", False, [], None,
     "回答未找到用户如何返回零，但 S1 20–22 行明确是 getOrDefault(userId, BigDecimal.ZERO)。目标方法在 Rank 1，失败发生在生成。"),
    ("refund-controller", "unsupported", False, [], None,
     "回答 RefundService.java，实际入口是 OrderController.refundOrder 13–15 行；Top5 只召回 controller imports 与 class 字段，没有入口方法。引用真实但不能支持入口定位。"),
    ("refund-form", "nonresponsive", False, [], None,
     "回答逐字复制问题，没有说明 reactive、ID/金额检查或 emit。S1 已包含完整 Vue 表单，不能把复述算作受支持答案。"),
    ("refund-request-type", "supported", False, ["S1"], None,
     "orderId: string 与 S1 types.ts 1–5 行一致；遗漏 userId: string 与 amount: number，所以支持成立但问题未完整回答。"),
    ("orders-schema", "unsupported", False, [], None,
     "将 orders.status/amount 定义位置说成 RefundService.java，真实定义在 S1 schema.sql 1–6 行。正确 DDL 已召回，仍给出错误文件。"),
    ("wallets-schema", "nonresponsive", False, [], None,
     "只写 WalletService.balanceOf(String userId)，没有回答 wallets 表 DEFAULT 0 的定义。Top5 未召回 schema.sql；业务读取默认零不能替代 DDL 默认值证据。"),
    ("fixture-documentation", "unsupported", False, [], None,
     "只列 RefundService.java，既未覆盖源文件与忽略规则，唯一引用 S1 config/repository.json 也不含这个文件名；相关说明实际上在未引用的 S2 README。"),
    ("repository-metadata", "supported", True, ["S1"], None,
     "回答 config/repository.json；S1 明确包含 name/purpose/features，满足元数据位置问题。其他四条引用多余。"),
    ("refund-validation-files", "unsupported", False, [], None,
     "把订单校验和钱包入账校验分别指向 types.ts 与 RefundPanel.vue。类型定义不是校验实现；钱包 credit 方法没有进入 Context。S4 RefundService.refund 已有订单校验，却未被正确解释。"),
    ("refund-call-chain", "supported", False, ["S4"], None,
     "RefundService.java 是三个实现文件之一，S4 支持这一有限位置事实。回答没有三个文件或调用顺序；controller 方法和 wallet 方法都未召回，S2 imports/S3 types 引用不能证明链路。"),
    ("issue-refund", "unsupported", False, [], None,
     "analysis 说代码被截断，实际 Context budget_limited=false、全部 budget_truncated=false，S4 退款方法完整。建议泛化，没有对具体片段做代码分析；不能用截断解释缺失钱包方法的检索问题。"),
    ("issue-direct", "unsupported", False, [], False,
     "只引用 README S1，并把 README 当调用节点；analysis 复述 Issue，test_suggestions 实际是加检查的代码修改建议。退款方法在 S2 已可见却未引用，没有具体重复调用测试，候选链无法成立。"),
    ("empty-repository", "abstention", True, [], None,
     "零检索、零 Context，固定证据不足答案，model=null、generation.called=false、无实际生成 HTTP，引用数组为空；这是正确负对照，语义支持和 Citation Valid 为 N/A。"),
    ("refund-budget700", "nonresponsive", False, [], None,
     "实际只输出 S1 RefundService.refund 12–15 行、四行预览，未包含第17行 wallets.credit。模型复述问题，未解释证据限制；引用正确截断，但不能据此判定回答有用。"),
]
require_ids = [r["id"] for r in report["cases"]]
assert [d[0] for d in decisions] == require_ids
reviews = []
for row, (identifier, outcome, complete, support, chain, reason) in zip(report["cases"], decisions, strict=True):
    selected = row["response"]["body"].get("source_ids", [])
    reviews.append({"id": identifier, "response_sha256": fingerprint(row["response"]), "outcome": outcome,
                    "answer_supported": True if outcome == "supported" else None if outcome == "abstention" else False,
                    "answer_complete": complete, "supporting_source_ids": support,
                    "citation_batch_supports_answer": set(selected) == set(support) if selected else None,
                    "candidate_chain_supported": chain, "reason": reason})
result = {"schema_version": 1, "report_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
          "dataset_sha256": report["dataset_sha256"], "review_date": "2026-10-12",
          "reviewer": {"role": "development_assistant", "method": "case-by-case inspection of actual replies, cited Context and local files; no automated LLM judge",
                       "independent_human_review": "pending"},
          "support_definition": "回答需包含与 Query 相关的实质性内容，且这些内容均可由选中的引用片段支持；空洞复述、错误位置或错误范围不通过。支持成立与完整回答分开；一条正确局部事实可以 supported 但 incomplete。多余引用另记整批语义支持失败。",
          "cases": reviews}
target = ROOT / "evaluation/reports/stage3_grounding_20261012_task12_review.json"
with target.open("x", encoding="utf-8") as stream:
    stream.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
print(json.dumps({"reviewed_cases": len(reviews), "reviewer": "development_assistant", "independent_human_review": "pending"}))
