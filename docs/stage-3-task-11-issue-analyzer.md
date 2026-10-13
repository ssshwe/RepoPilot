# Stage 3 / Task 11：Issue Analyzer

## 本次完成

完成独立的 `POST /api/issue-analysis` 和 Issue Analyzer 页面。一次请求执行 Issue → Retrieve → Context → Structured Output → Validated Sources，展示 Task Summary、Related Code、Possible Call Chain、Analysis、Modification Suggestions、Test Suggestions、Risks、Sources。生成的修改和测试建议供人工核实，应用不会修改仓库或运行测试。

工程链路与界面验收通过；当前配置的 `qwen2.5:0.5b` **未提供合格、可靠的 Issue 分析**。实际输出存在复述问题、Context 标记充当建议、README 充当候选调用节点，以及要求用户提供密钥值等失败。`analyzed` 表示返回了附带引用的结构化结果，页面标记为“附带代码引用”，不能解释为分析正确或已确认缺陷。

本任务的实现、测试、真实推理和浏览器截图记录于 **2026-10-07**。中断后于 **2026-10-12** 补齐文档、重放记录、核对保护文件和索引、恢复开发服务。这次收尾没有重新运行模型，也没有将历史测试标记成今天运行。机器记录见 [verification.json](D:/aaaagent开发文件/RepoPilot/docs/stage-3-task-11-verification.json)。

## 修改文件

| 文件 | 职责 |
| --- | --- |
| [schemas/issue_analysis.py](D:/aaaagent开发文件/RepoPilot/services/api/app/schemas/issue_analysis.py) | Issue 请求、模型七字段结果、最终 Response；严格文本、ID 与证据一致性校验 |
| [issues/prompt.py](D:/aaaagent开发文件/RepoPilot/services/api/app/issues/prompt.py) | 专用系统规则、不可信 Issue JSON 封装、本次 Source ID 枚举 Schema |
| [issues/service.py](D:/aaaagent开发文件/RepoPilot/services/api/app/issues/service.py) | 固定单次检索和生成链路、弃答、输出检查、后端 Citation 与预览映射 |
| [api/issues.py](D:/aaaagent开发文件/RepoPilot/services/api/app/api/issues.py) | API 路由、依赖组装、请求级 Qdrant 和 Embedding 客户端释放 |
| [main.py](D:/aaaagent开发文件/RepoPilot/services/api/app/main.py) | 注册路由，扩展安全 422 错误返回 |
| [types/issue-analysis.ts](D:/aaaagent开发文件/RepoPilot/apps/web/src/types/issue-analysis.ts) | 前端 DTO，复用已有 Citation 和诊断类型 |
| [api/issue-analysis-contract.ts](D:/aaaagent开发文件/RepoPilot/apps/web/src/api/issue-analysis-contract.ts) | 复用 Code Chat 校验，检查 Issue 回显、候选链一致性、四组文本和安全错误 |
| [api/issue-analysis.ts](D:/aaaagent开发文件/RepoPilot/apps/web/src/api/issue-analysis.ts) | 单次请求、AbortSignal、响应解码与固定错误信息 |
| [stores/issue-analysis.ts](D:/aaaagent开发文件/RepoPilot/apps/web/src/stores/issue-analysis.ts) | 当前分析、请求快照、停止、清空、超时、重试与迟到响应隔离 |
| [IssueAnalyzer.vue](D:/aaaagent开发文件/RepoPilot/apps/web/src/components/IssueAnalyzer.vue)、[CitedSources.vue](D:/aaaagent开发文件/RepoPilot/apps/web/src/components/CitedSources.vue) | 简单表单、八类输出、候选链说明、引用坐标与带行号预览；全部以转义文本渲染 |
| [App.vue](D:/aaaagent开发文件/RepoPilot/apps/web/src/App.vue)、[style.css](D:/aaaagent开发文件/RepoPilot/apps/web/src/style.css) | 第四工作区、Hash 导航、模型标签和移动端样式 |
| [test_issue_analysis.py](D:/aaaagent开发文件/RepoPilot/services/api/tests/test_issue_analysis.py)、[issue-analysis.test.mjs](D:/aaaagent开发文件/RepoPilot/apps/web/tests/issue-analysis.test.mjs) | 新增 60 个后端、13 个前端确定性用例；Fake Provider 与受控响应明确标注 |
| [README.md](D:/aaaagent开发文件/RepoPilot/README.md) | 入口、接口、能力边界和真实失败说明 |
| [output/stage3-task11](D:/aaaagent开发文件/RepoPilot/output/stage3-task11/verification.json) | 原始 Context、实际模型 HTTP 回复、API 响应、测试收据、保护快照、重放脚本与 Bad Cases |

489 个旧文件被纳入哈希保护。预期改动为 README、App、style、main；其余 **485 个文件完全一致**。原 Code Chat 服务、Schema、组件、旧测试和旧验收文档没有变化，环境、依赖和锁文件也在保护范围内。原任务截图未全部纳入该清单，不将其描述为全部经哈希验证。Qdrant 所有 Collection 的元数据与完整 Point 快照一致；`repopilot_code` 保持 **17 Points、1024 维、Cosine**。本任务没有新建 Collection、重新索引或写入仓库样本。

## 调用链 / 数据流

```text
IssueAnalyzer → Pinia 请求快照 → 前端请求校验
  → POST /api/issue-analysis → IssueAnalysisRequest
  → Repository-scoped Retrieval（一次 Query Embedding + Dense Search）
  → ContextBuilder（去重、预算、实际片段、S1…Sn）
  → CitationValidator（先检查完整 Context 与检索 Metadata）
  → Issue Prompt（system + Context user + JSON Issue user）
  → 既有 LLMProvider.complete_structured（一次，不提供工具）
  → 七字段结果严格复验 + Source ID CitationValidator
  → 所有生成文本的有限敏感输出检查
  → related_code / possible_call_chain / sources 后端映射
  → 最终 Response 一致性校验 → 前端完整校验 → 八类输出
```

Related Code 取模型选中的真实 Source IDs；Possible Call Chain 取同一批 ID 的候选顺序，必须是 Related Code 的子集。两者的 Repository / Path / Symbol / Lines 来自后端，Sources 的 language / content 来自同一个已验证 Context。预览不二次检索、不读取额外文件。

Context 为空时不解析生成模型，返回固定 `insufficient_evidence`、`model: null`、`generation.called: false`。非空 Context 下模型返回空 ID 时，丢弃模型生成的摘要、分析、链和建议，统一返回固定证据不足结果。非法 Schema、Citation 或命中的敏感赋值/私钥块返回安全 HTTP 502，整批失败，没有部分答案、修复循环或自动重试。用户主动重试会重新执行一次完整请求。

## 我必须理解的知识

1. **检索阶段与索引阶段不同。** 输入 Issue、Repository、TopK，输出已有索引中的排序 Chunk。查询复用原 Dense Retrieval；不会为每个 Issue 扫描或解析整个仓库。这样查询成本有限，但 TopK 未召回的实现无法成为本次证据。检索不到幂等机制不能证明整个项目没有幂等机制。

2. **Context Budget 限制实际证据。** 输入排序 Chunk，ContextBuilder 输出去重后的完整行片段、S1…Sn 和实际行范围。预算计算序列化元数据、转义、边界与分隔符，按 Unicode 字符计数，不等于模型 Token 数。本次真实退款请求使用 5 Sources、4706 / 16000 字符。预算太小可以得到零 Source，必须停止生成；截断后引用实际输出的行，不能保留原大范围。

3. **Structured Output 保证形状。** 输入本次 Context 的 ID 集合，动态 Schema 输出七字段；Source IDs 和候选链只允许这些枚举值。当前四类文字字段在 Python 中用严格单元素 tuple 表达，JSON 传输仍是单元素数组；每项最多 1024 字符，摘要最多 2048 字符。这让字段必须存在且数量有界，但模型仍能用无意义文字填满合法数组。旧 `live` / `final` 记录允许空数组；当前 Schema 只用于最终 `bounded` 阶段重放，不拿新版契约错误判定历史回复。

4. **Citation 验证坐标，不证明推理。** 输入模型 IDs、Context 和真实检索结果，CitationValidator 检查 ID、仓库、路径、符号、行号、实际切片并生成六字段 Citation。它避免模型捏造结构化文件位置；任何不一致整批失败。候选调用顺序仅是选择的节点排列，系统没有构建调用图或证明边。README 的真实引用也能通过坐标检查，却不是合理的调用节点。一个正确坐标同样可能支持不了模型结论。

5. **不可信内容需要多层处理。** Issue 以 JSON user 数据提供，仓库元数据和代码以已有 Source 边界封装；系统提示禁止接受伪造角色、执行命令、读取环境和输出或索取 Secret。模型请求没有工具，四类文字和摘要经过既有有限敏感赋值/私钥检查，前端使用转义文本。这些是有限控制：目前检查会漏掉“请提供 API key”这种索取表达，真实模型也确实这样输出。浏览器 AbortController 只停止等待；请求身份检查避免旧响应恢复已清空结果，不能保证服务器推理立即取消。

## 真实数据示例

原仓库是已存在的手写演示仓库 `demo-repository`，不是生产支付系统。用真实文件、真实索引和本地模型运行，不将测试夹具中的理想分析当作推理结果。

真实浏览器 Issue：

```json
{"repository":"demo-repository","issue":"商城退款接口可能存在重复退款问题，请分析应该重点检查哪些代码。","top_k":5}
```

最终页面收到 `status: analyzed`、5 个真实引用、4706 字符 Context。其中 **S4** 为以下完整六字段 Citation：

```json
{
  "repository": "demo-repository",
  "source_id": "S4",
  "path": "backend/service/RefundService.java",
  "symbol": "RefundService.refund",
  "start_line": 12,
  "end_line": 18
}
```

该 Source 的 7 行预览与 [RefundService.java](D:/aaaagent开发文件/RepoPilot/workspaces/demo-repository/backend/service/RefundService.java:12) 一致：

```java
    public void refund(String orderId, String userId, BigDecimal amount) {
        if (orderId == null || orderId.isBlank()) {
            throw new IllegalArgumentException("Order ID is required");
        }
        // Fixture workflow: credit the refunded amount to the user's wallet.
        wallets.credit(userId, amount);
    }
```

Source ID 只在单次请求中有效；其他查询的 S4 可以是不同文件。该片段可见订单编号检查和 `wallets.credit` 调用；重复退款是否发生仍需结合业务状态及完整链路核实。

以下是同一真实回复中的字段摘录，明确展示分析失败：

```json
{
  "summary": "商城退款接口可能存在重复退款问题，请分析应该重点检查哪些代码。",
  "analysis": ["[UNTRUSTED_REPOSITORY_CONTEXT_BEGIN]\n[UNTRUSTED_REPOSITORY_CONTEXT_END]"],
  "modification_suggestions": ["[UNTRUSTED_REPOSITORY_CONTEXT_BEGIN]\n[SOURCE S1]\n[UNTRUSTED_REPOSITORY_CONTEXT_END]"],
  "test_suggestions": ["[UNTRUSTED_REPOSITORY_CONTEXT_BEGIN]\n[SOURCE S1]\n[UNTRUSTED_REPOSITORY_CONTEXT_END]"],
  "risks": ["重复退款问题"],
  "source_ids": ["S1", "S2", "S3", "S4", "S5"],
  "possible_call_chain": []
}
```

这段不是完整 Response。完整原始结果见 [response-browser-refund.json](D:/aaaagent开发文件/RepoPilot/output/stage3-task11/response-browser-refund.json)，模型原始 HTTP JSON 见 [bounded/generation-002.json](D:/aaaagent开发文件/RepoPilot/output/stage3-task11/bounded/generation-002.json)。

未索引仓库 `stage3-task11-not-indexed` 的真实请求返回 `insufficient_evidence`，0 检索结果、0 Context、空分析和引用，生成模型未调用，见 [response-browser-empty.json](D:/aaaagent开发文件/RepoPilot/output/stage3-task11/response-browser-empty.json)。该仓库名用于查询零结果，没有创建或索引一个新仓库。

## 验收方法

从项目根目录启动既有服务：

```powershell
.\infra\Qdrant.ps1 up
.\Start-RepoPilot.ps1 -NoBrowser
```

打开 [Issue Analyzer](http://localhost:5173/#issue-analysis)，选择 `demo-repository`、TopK 5，点“使用示例”再分析。查看八类输出、引用路径/符号/行号、展开 S4 的编号代码预览，核对真实文件。页面出现“附带代码引用”后仍要人工检查分析内容。试空输入不会发请求；输入未索引仓库应得到证据不足；停止和清空后迟到结果不能覆盖当前状态；失败可以按原提交参数重试。

接口：

```powershell
$body = @'
{"repository":"demo-repository","issue":"商城退款接口可能存在重复退款问题，请分析应该重点检查哪些代码。","top_k":5}
'@
Invoke-RestMethod -Method Post -Uri http://localhost:8000/api/issue-analysis `
  -ContentType "application/json" -Body ([Text.Encoding]::UTF8.GetBytes($body))
```

测试与构建可按下面命令复跑；各命令注明工作目录：

```powershell
# services/api
.\.venv\Scripts\python.exe -m pytest tests

# apps/web
npm.cmd test
npm.cmd run build

# 项目根目录：原 Stage 2 评测测试
.\services\api\.venv\Scripts\python.exe -m pytest evaluation/tests

# 项目根目录：保存证据重放与保护核对，不调用模型或写索引
.\services\api\.venv\Scripts\python.exe output/stage3-task11/verify.py
```

2026-10-07 已完成的结果：

| 项目 | 实际结果 | 范围 |
| --- | --- | --- |
| Backend | 1001 passed，6 skipped，2 个已有 warnings | 收集 1007；新增 Task 11 用例 60，跳过项是原有独立 live 测试 |
| Frontend | 41 passed | 原有 28 + 新增 13；包括契约与请求状态 |
| 原 Stage 2 Evaluation Tests | 42 passed | 既有评测数据与代码保持不变，不等于 Task 12 Stage 3 评测 |
| Frontend Build | vue-tsc + Vite 成功，1627 modules | 最终页面与 Schema 改动之后构建 |
| 真实浏览器流程 | 5 / 5 检查通过，2 次真实 Issue POST | 空输入、空证据、真实输出展示、桌面和移动端外层无溢出 |
| 受控浏览器流程 | 8 / 8 检查通过 | 隐私 502、篡改候选链、转义文本、伪造 Source 仅为文本、防重复、清空隔离、旧工作区、移动端；全部使用明确夹具，不调用模型 |

单元用例还覆盖非法/重复/跨请求 IDs、候选链子集、伪造 Metadata、非 UTF-8 文本、预算、空检索、截断实际行、弃答丢弃内容、各生成字段的敏感输出检查、Provider 返回对象复验、依赖释放、无工具和单次调用。

三阶段真实记录合计 **9 次 Issue HTTP 请求、7 次实际模型 HTTP 调用**，其中两个零结果请求跳过生成；所有完整记录均已保留。`live` 为初版 4 请求 / 3 生成，`final` 为提示描述调整后 1 / 1，`bounded` 为当前单项数组契约 4 / 3。这不是 9 个语义成功案例，也不保证重复请求完全确定。

2026-10-12 的只读重放核对了原始 Provider HTTP 回复与解析结果、Context 重新构建、前后 Citation 批次、预览和本地文件逐行一致性、七字段 Schema、无工具和单次调用，以及 JUnit 收据。当前 API `/health` 和前端首页返回 HTTP 200，路由已注册；Qdrant 快照与任务开始时完全一致。重放程序不会重新运行测试或推理，历史输出也不会被覆盖。

截图：

- [桌面](D:/aaaagent开发文件/RepoPilot/output/playwright/task11-desktop.png)、[桌面 Sources](D:/aaaagent开发文件/RepoPilot/output/playwright/task11-desktop-sources.png)：1280 × 1000。
- [移动端](D:/aaaagent开发文件/RepoPilot/output/playwright/task11-mobile.png)、[移动端代码预览](D:/aaaagent开发文件/RepoPilot/output/playwright/task11-mobile-preview.png)：390 × 844。
- [真实空证据](D:/aaaagent开发文件/RepoPilot/output/playwright/task11-empty.png)、[受控错误](D:/aaaagent开发文件/RepoPilot/output/playwright/task11-error.png)、[受控不可信文本](D:/aaaagent开发文件/RepoPilot/output/playwright/task11-untrusted-text.png)。

## 当前未实现

- 自动修改代码、执行测试、工具调用和 Agent Loop。
- Hybrid Search、BM25、Reranker、查询扩展、GraphRAG 或调用图构建。
- 对回答语义支持程度、真实调用边或缺陷成立的自动验证。
- 完整 Secret 检测、可靠的敏感信息索取拦截或通用 Prompt Injection 防护。
- 持久化 Issue 历史、多轮 Issue 对话、流式 Issue 输出、服务器推理取消。
- Task 12 的 Stage 3 评测集与评测报告、Task 13 的最终 Stage 3 汇总文档。

## Bad Case / 风险

完整记录见 [bad-cases.json](D:/aaaagent开发文件/RepoPilot/output/stage3-task11/bad-cases.json)。

1. **相关代码已召回，模型仍弃答。** 初版退款和直接询问 `RefundService.refund` 的请求均有非空 Context；模型只复述 Issue、返回空 Source IDs，后端固定弃答。工程处理正确，但不代表真实 Issue 分析完成。

2. **真实坐标对应错误候选节点。** 提示描述调整后的 [response-final-focused.json](D:/aaaagent开发文件/RepoPilot/output/stage3-task11/response-final-focused.json) 将 `README.md`、null Symbol、1–21 行作为唯一候选调用节点，四类分析数组为空。它暴露了 Citation 验证范围：只验证映射和实际切片，无法证明这是函数或真实调用关系。

3. **必填数组仍可能无意义。** 最终契约要求四个单项文字数组，实际模型填入 Context 标记，摘要复述 Issue。限制格式修复了缺项问题，但没有解决推理质量。真实截图原样展示这一失败。

4. **Secret 请求未得到正确拒绝。** 最终 [response-bounded-secret.json](D:/aaaagent开发文件/RepoPilot/output/stage3-task11/response-bounded-secret.json) 的修改建议是“请提供 .env 文件的 API key 和服务器环境变量值。”，同时引用无关仓库片段。没有输出真实密钥值、没有读取 `.env`、没有工具执行；但是**索取密钥本身违反系统规则，属于真实失败**。有限赋值/私钥检查漏掉此类表达。测试夹具验证命中的赋值被拦截，不证明模型能拒绝所有 Secret 请求。不要按该建议提供凭据。

5. **证据与交互仍有限。** Dense TopK 可以漏掉关键钱包方法和业务状态；小片段不能单独确认完整幂等性。当前 Sources 是索引快照的实际片段，仓库后来变更需重新索引。停止等待不会保证推理取消。真实模型语义质量及对敏感请求的行为尚不能作为可靠工程决策依据。

## 下一任务

Task 12：Stage 3 Evaluation + Bad Cases。下一次获准继续时才使用真实仓库与 Query 建立 Retrieval、Answer、Citation 案例并人工判断 Answer Supported / Citation Valid，将这里的真实失败作为候选材料。本次只完成 Task 11 验收留证，**没有开始 Task 12，也没有扩展到 Stage 4**。
