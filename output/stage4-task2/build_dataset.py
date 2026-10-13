"""Author deterministic learning fixtures and source-grounded labels BEFORE any retrieval run."""
import hashlib
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "services/api"))
from app.repository.scanner import RepositoryScanner
from evaluation.stage4.state import fingerprint, read_json, write_new

MAIN, SHADOW, EMPTY = "stage4-commerce", "stage4-shadow", "stage4-empty"
SPECS = [
    ("wallet", "WalletService", "credit", "钱包入账", "amount <= 0", "Amount must be positive", "balance += amount;", "钱包余额增加入账金额", "dev"),
    ("refund", "RefundService", "refund", "订单退款", "reference.isBlank()", "Refund order ID required", "refunded += amount;", "累计退款金额增加", "dev"),
    ("orders", "OrderService", "submit", "提交订单", "amount < 1", "Cart must contain an item", "state = \"PAID\";", "订单状态设为 PAID", "dev"),
    ("inventory", "InventoryService", "reserve", "库存预留", "amount > stock", "Insufficient stock", "stock -= amount;", "可用库存扣减预留数量", "dev"),
    ("shipping", "ShippingService", "dispatch", "发货", "reference.length() < 5", "Shipping postcode invalid", "state = \"TRACK-\" + reference;", "追踪号由 TRACK- 前缀和地址编号构成", "dev"),
    ("auth", "AuthService", "login", "登录", "reference.isBlank()", "Invalid credentials", "attempts += 1;", "成功登录计数增加", "dev"),
    ("ledger", "LedgerService", "transfer", "账本转账", "amount > balance", "Ledger balance insufficient", "balance -= amount;", "转账扣减账本可用余额", "dev"),
    ("invoice", "InvoiceService", "issue", "开具发票", "amount < 0", "Invoice amount cannot be negative", "total = amount + 20;", "发票总额包含固定税额 20", "holdout"),
    ("subscription", "SubscriptionService", "renew", "续订", "amount < 1", "Renewal months required", "months += amount;", "订阅期限按月份累加", "holdout"),
    ("fraud", "FraudService", "check", "风控审核", "amount > 80", "Fraud score exceeds threshold", "state = \"APPROVED\";", "风险分数超过 80 被拒绝，否则通过审核", "holdout"),
    ("notification", "NotificationService", "send", "发送通知", "!reference.contains(\"@\")", "Notification recipient invalid", "queued += 1;", "有效邮件收件人使待发送队列计数增加", "holdout"),
    ("coupon", "CouponService", "apply", "使用优惠券", "amount > 100", "Coupon discount exceeds limit", "discount = amount;", "优惠折扣不能超过 100", "holdout"),
]


def text(repository, path, content):
    destination = ROOT / "workspaces" / repository / path
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(content)


def span(repository, path, marker, method=False):
    lines = (ROOT / "workspaces" / repository / path).read_bytes().decode().splitlines(keepends=True)
    start = next(i for i, line in enumerate(lines) if marker in line)
    end = start
    if method:
        depth = 0
        for end in range(start, len(lines)):
            depth += lines[end].count("{") - lines[end].count("}")
            if depth == 0:
                break
    return {"path": path, "start_line": start + 1, "end_line": end + 1, "snippet": "".join(lines[start:end + 1]).strip()}


def label(identifier, query, category, group, split, evidence, symbols=(), facts=(), repository=MAIN, **extra):
    return {"id": identifier, "query": query, "repository": repository, "category": category,
            "group_id": group, "split": split, "source_revision": None,
            "expected_paths": list(dict.fromkeys(e["path"] for e in evidence)), "expected_symbols": list(symbols),
            "evidence": evidence, "required_facts": list(facts),
            "annotator": "development_assistant: explicit source inspection; deterministic learning fixture, independent human review pending", **extra}


def main():
    shared = "backend/shared/SharedRecorder.java"
    text(MAIN, shared, '''package learning.shared;
public final class SharedRecorder {
    private int events = 0;
    public void record(String event) {
        events += 1;
    }
    public int count() {
        return events;
    }
}
''')
    shared_span = span(MAIN, shared, "public void record", True)
    cases = []
    for index, (module, cls, action, chinese, condition, error, update, fact, split) in enumerate(SPECS):
        service = f"backend/{module}/{cls}.java"
        controller = f"backend/{module}/WorkflowController.java"
        fields = "    private int balance = 1000, stock = 100, refunded = 0, attempts = 0;\n    private int total = 0, months = 0, queued = 0, discount = 0;\n    private String state = \"NEW\";\n"
        text(MAIN, service, f'''package learning.{module};
import learning.shared.SharedRecorder;
public final class {cls} {{
{fields}    private final SharedRecorder recorder;
    public {cls}(SharedRecorder recorder) {{
        this.recorder = recorder;
    }}
    public void {action}(String reference, int amount) {{
        if ({condition}) {{
            throw new IllegalArgumentException("{error}");
        }}
        {update}
        recorder.record("{module}.{action}");
    }}
}}
''')
        text(MAIN, controller, f'''package learning.{module};
public final class WorkflowController {{
    private final {cls} service;
    public WorkflowController({cls} service) {{
        this.service = service;
    }}
    public void handle(String reference, int amount) {{
        service.{action}(reference, amount);
    }}
}}
''')
        method = span(MAIN, service, f"public void {action}", True)
        symbol = {"path": service, "symbol": f"{cls}.{action}"}
        evidence, symbols = [method], [symbol]
        query = f"backend/{module} 中的 {cls}.{action} 实现在哪里？"
        if module == "wallet":
            legacy = "backend/legacy/WalletService.java"
            text(MAIN, legacy, '''package learning.legacy;
public final class WalletService {
    private int balance = 0;
    public void credit(String reference, int amount) {
        balance += amount * 2;
    }
}
''')
            evidence = [method, span(MAIN, legacy, "public void credit", True)]
            symbols += [{"path": legacy, "symbol": "WalletService.credit"}]
            query = "当前仓库中 WalletService.credit 有哪些实现？请同时列出正式钱包和 legacy 钱包。"
        cases.append(label(f"{module}-symbol", query, "symbol", f"feature-{module}", split, evidence, symbols, [f"准确定位 {cls}.{action} 的作用域内定义"] ))
        cases.append(label(f"{module}-behavior", f"在 backend/{module} 的{chinese}流程中，{fact}的代码在哪里？", "natural_language", f"feature-{module}", split, [method], [symbol], [fact]))
        if index % 2:
            marker = f'recorder.record("{module}.{action}")'
            cases.append(label(f"{module}-literal", f'哪个{chinese}方法写入代码字面量 "{module}.{action}"？', "literal", f"feature-{module}", split,
                               [span(MAIN, service, marker)], [symbol], [f"方法记录事件 {module}.{action}"]))
        else:
            cases.append(label(f"{module}-error", f'哪段{chinese}代码抛出错误 "{error}"？', "error_text", f"feature-{module}", split,
                               [span(MAIN, service, f'throw new IllegalArgumentException("{error}")')], [symbol], [f"对应异常文本为 {error}"]))
        cases.append(label(f"{module}-chain", f"backend/{module} 的{chinese}请求如何从 WorkflowController.handle 到 {cls}.{action} 再记录审计事件？", "multi_file", f"feature-{module}", split,
                           [span(MAIN, controller, "public void handle", True), method, shared_span],
                           [{"path": controller, "symbol": "WorkflowController.handle"}, symbol, {"path": shared, "symbol": "SharedRecorder.record"}],
                           [f"Controller 调用 service.{action}", fact, "SharedRecorder.record 增加审计事件计数"]))
    text(MAIN, "config/application.json", '{\n  "maxRetries": 3,\n  "timeoutMs": 1000,\n  "currency": "CNY",\n  "auditEnabled": true\n}\n')
    text(MAIN, "database/schema.sql", 'CREATE TABLE ledger_entries (\n    id INTEGER PRIMARY KEY,\n    amount INTEGER NOT NULL,\n    status VARCHAR(20) DEFAULT \'PENDING\'\n);\nCREATE TABLE wallets (\n    user_id VARCHAR(64) PRIMARY KEY,\n    balance INTEGER NOT NULL DEFAULT 0\n);\n')
    text(MAIN, "README.md", '# Commerce retrieval learning fixture\n\nThis handwritten fixture is not a deployed payment application.\nOfficial wallet code lives in backend/wallet; backend/legacy doubles credits.\nWorkflowController.handle delegates to a feature service, then SharedRecorder records an event.\nThe database defaults wallet balance to zero and ledger status to PENDING.\nApplication settings use CNY, maxRetries=3, timeoutMs=1000.\n')
    cases += [
        label("config-retries", "配置中最大重试次数和超时时间分别是多少？", "config", "feature-configuration", "dev", [span(MAIN, "config/application.json", '"maxRetries"'), span(MAIN, "config/application.json", '"timeoutMs"')], facts=["maxRetries=3", "timeoutMs=1000"]),
        label("schema-wallet-default", "钱包表余额默认零和账本状态默认 PENDING 分别在哪些 SQL 行定义？", "sql", "feature-configuration", "dev", [span(MAIN, "database/schema.sql", "DEFAULT 0"), span(MAIN, "database/schema.sql", "DEFAULT 'PENDING'")], facts=["wallets.balance DEFAULT 0", "ledger_entries.status DEFAULT PENDING"]),
        label("fixture-wallet-documentation", "仓库文档如何区分正式钱包目录和翻倍入账的 legacy 钱包？", "documentation", "feature-configuration", "dev", [span(MAIN, "README.md", "Official wallet")], facts=["backend/wallet 是正式实现", "backend/legacy 的入账金额翻倍"]),
        label("config-explicit-path", "config/application.json 中配置的币种是什么？", "path", "feature-configuration", "dev", [span(MAIN, "config/application.json", '"currency"')], facts=["currency=CNY"]),
    ]
    text(SHADOW, "backend/wallet/WalletService.java", '''package shadow;
public final class WalletService {
    public void credit(String reference, int amount) {
        throw new IllegalArgumentException("Shadow wallet is disabled");
    }
}
''')
    text(SHADOW, "backend/ShadowGateway.java", '''package shadow;
public final class ShadowGateway {
    public void rotate() {
        System.out.println("SHADOW_ONLY_ROTATION");
    }
}
''')
    text(EMPTY, ".gitkeep", "")
    negatives = [
        ("missing-symbol-a", "定位 NeverExistsService.commit", "missing_symbol", "NeverExistsService.commit", MAIN),
        ("missing-symbol-b", "不存在的 PhantomWallet.reverse 在哪里？", "missing_symbol", "PhantomWallet.reverse", MAIN),
        ("missing-path", "backend/unknown/MissingController.java 在哪里？", "missing_path", "backend/unknown/MissingController.java", MAIN),
        ("missing-literal", '哪个文件包含错误 "THIS_ERROR_DOES_NOT_EXIST_2026"？', "missing_literal", "THIS_ERROR_DOES_NOT_EXIST_2026", MAIN),
        ("foreign-symbol", "只在当前 commerce 仓库中定位 ShadowGateway.rotate，不搜索 shadow 仓库。", "repository_scope_mismatch", "ShadowGateway.rotate", MAIN),
        ("scope-shadow-wallet", "只在 shadow 仓库里查找 SharedRecorder.record 的实现。", "repository_scope_mismatch", "SharedRecorder.record", SHADOW),
        ("empty-symbol", "空仓库中的 WalletService.credit 在哪里？", "empty_repository", None, EMPTY),
        ("empty-natural", "空仓库哪里检查退款金额？", "empty_repository", None, EMPTY),
    ]
    for identifier, query, kind, target, repository in negatives:
        cases.append(label(identifier, query, "negative", "negative-controls", "dev", [], facts=["当前仓库没有问题所需的实现证据"], repository=repository, negative_kind=kind, absent_target=target))
    historical = read_json(ROOT / "evaluation/datasets/stage3_grounding.json")
    regression = []
    for old in historical["cases"]:
        if old["group"] == "canonical":
            regression.append(label(old["id"], old["query"], "multi_file" if len(old["expected_paths"]) > 1 else "natural_language", "historical-12", "regression",
                                    old["evidence"], old["expected_symbols"], [old["expected_answer"]], repository=old["repository"]))
    scanner = RepositoryScanner(ROOT / "workspaces")
    repositories = {}
    for repository in ("demo-repository", MAIN, SHADOW, EMPTY):
        hashes = {f.path: hashlib.sha256((ROOT / "workspaces" / repository / f.path).read_bytes()).hexdigest() for f in scanner.scan(repository).files}
        repositories[repository] = {"source_revision": None, "source_sha256": hashes, "source_hash": fingerprint(hashes),
                                    "role": "historical" if repository == "demo-repository" else "empty_control" if repository == EMPTY else "distractor" if repository == SHADOW else "learning_fixture"}
    for case in regression + cases:
        case["source_hash"] = repositories[case["repository"]]["source_hash"]
    write_new(ROOT / "evaluation/datasets/stage4_retrieval.json", {"schema_version": 1, "name": "stage4-commerce-retrieval-v1", "repositories": repositories,
        "source_note": "Handwritten controlled learning fixtures; not production or a statistically blind benchmark. Labels authored before retrieval. Independent human review pending.",
        "split_policy": "Historical 12 separate; feature-domain families and all negative rewrites stay in one split; holdout not queried during Task 2",
        "cases": regression + cases})
    print("Authored 12 historical + 40 dev + 20 holdout cases before retrieval")


if __name__ == "__main__":
    main()
