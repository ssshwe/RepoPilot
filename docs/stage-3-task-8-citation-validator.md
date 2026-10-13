# Stage 3 / Task 8：Citation Validator

验收日期：2026-10-07，Asia/Shanghai。本次完成 Task 8，停在 Citation Validator；Task 9 尚未开始。

## 本次完成

建立独立、无 I/O 的 `CitationValidator`，把模型选择的 Source IDs 映射为来自本次后端检索与实际 Context 的 Citation。生成模型的 Schema 仍只有 `answer`、`source_ids`；模型不填写 Citation 的 Path、Symbol、Lines。API 在原有响应上增加必需的 `citations`，保留 `source_ids`。

每个 Citation 恰好包含六个字段：`repository`、`source_id`、`path`、`symbol`、`start_line`、`end_line`。Symbol 允许为 null，适用于 README、Fallback 或无命名节点的片段。记录采用严格类型、禁止额外字段、冻结属性，并复用现有 Repository、相对路径和 Symbol 校验。

校验包含 ID 存在性与唯一性、当前 Repository、本次原始检索 Chunk 身份、合法 Metadata、实际片段与原 Chunk 的逐行对应，以及序列化 Context 与 Source Map 的一致性。原始检索中存在、但未进入预算内 Context 的 Source 不能被引用。每次调用只使用显式传入的数据，没有跨请求的全局 Source Map。

采用**整批拒绝**策略：任意 ID 或 Metadata 不合法时，返回受控 502，不返回部分 Citation 或模型回答，不静默删除错误引用。空 Citation 是正常的证据不足结果；空 IDs 仍须通过 Metadata 校验。

RAG Service 在生成之前用空 IDs 校验所有来源，在生成之后用模型 IDs 再次校验并映射。前置校验避免把坏来源交给模型；后置校验还能检测等待生成期间被修改的原始检索对象。失败消息不包含原始代码、异常对象或模型回答；没有增加应用层修复循环或 Agent Loop。

## 修改文件

| 文件 | 状态与职责 |
| --- | --- |
| [citations/models.py](D:/aaaagent开发文件/RepoPilot/services/api/app/citations/models.py) | 新增：共享 SourceId 类型与六字段 Citation 契约 |
| [citations/validator.py](D:/aaaagent开发文件/RepoPilot/services/api/app/citations/validator.py) | 新增：整批来源校验、片段核对与 ID → Citation 映射 |
| [citations/__init__.py](D:/aaaagent开发文件/RepoPilot/services/api/app/citations/__init__.py) | 新增：模块导出 |
| [schemas/code_chat.py](D:/aaaagent开发文件/RepoPilot/services/api/app/schemas/code_chat.py) | 修改：响应增加 citations；验证 ID 顺序、Repository 与 Status 一致 |
| [rag/service.py](D:/aaaagent开发文件/RepoPilot/services/api/app/rag/service.py) | 修改：生成前后调用 Validator，空证据返回空 Citation |
| [test_citations.py](D:/aaaagent开发文件/RepoPilot/services/api/tests/test_citations.py) | 新增：103 个纯 Validator 与 Citation 契约用例 |
| [test_code_chat.py](D:/aaaagent开发文件/RepoPilot/services/api/tests/test_code_chat.py) | 修改：更新原 60 个用例，增加 8 个集成用例，共 68 个 |
| [README.md](D:/aaaagent开发文件/RepoPilot/README.md) | 修改：Task 8 用法、进度、预算截断引用与限制 |

另新增本验收文档、[机器验收记录](D:/aaaagent开发文件/RepoPilot/docs/stage-3-task-8-verification.json) 及 [独立复核脚本](D:/aaaagent开发文件/RepoPilot/output/stage3-task8/verify.py)、临时验收服务脚本和真实请求/Context/模型结果/测试 XML。

对 293 个原有文件作 SHA-256 保护核对，仅 README、RAG Service、Code Chat Schema、Code Chat 测试这四个原有文件发生预期修改。`.env`、锁文件、Context Builder、RAG Prompt、前端、演示源码、评测数据和 Task 1–7 证据保持原样；未安装依赖或切换模型。

## 调用链 / 数据流

```text
Repository + Question + TopK
→ 请求校验
→ 现有 Retrieval Service：Query Embedding + Repository Filter + Dense TopK
→ Context Builder：去重、预算、实际片段、Source IDs
→ CitationValidator.validate(repository, [], context, raw_results)
  → 错误：生成前受控 502
  → Context 为空：200 / insufficient_evidence / citations=[]，跳过生成
  → Context 非空：
    → 现有 RAG Prompt 与本次 Source ID 枚举
    → 一次 Provider.complete_structured
    → 严格校验 answer/source_ids
    → CitationValidator.validate(repository, model_ids, context, raw_results)
    → 原始 Metadata 的 Path/Symbol + 实际发送片段的 Lines
    → 非空 Citation：回答与 Citation；空 Citation：固定证据不足消息
→ CodeChatResponse 再校验 IDs、Citation、Repository、Status 的一致性
→ 关闭本次 Embedding / Qdrant Client
```

Validator 不调用 Provider、扫描器、文件系统或数据库。输入限制沿用 TopK 与 Context 上限：IDs 和原始检索最多 20 项，Sources 不超过配置上限；不接受无界生成器。它严格重验原始 Retrieval DTO，核对规范 UUID、排序、同 ID Metadata 一致性和同文件重叠行一致性，再验证每个实际 Source。

Path 与 Symbol 从严格校验的原始后端检索 Metadata 取出。Lines 从已核对为原 Chunk 精确子切片的 Context Source 取出，使用从 1 开始的闭区间。原始行范围只作内部来源校验，不能直接代替经过预算截断的实际引用范围。多个不连续片段各自保持自己的范围。

应用层仍是固定 RAG Pipeline。既有 SDK 传输重试配置未变；本次两个实际生成请求各只有一条生成 HTTP 记录。

## 我必须理解的知识

1. **Source ID 是本次请求中的引用键。** Context Builder 输入本次 TopK，输出 S1、S2 等 Sources；`citations/validator.py` 输入模型 IDs 与同一请求的 Context/原始结果，输出 Citation。它让模型选择证据，后端提供坐标。S1 不具有跨请求身份；Validator 通过原始 Chunk UUID 与 Metadata 核对当前来源，不能使用上一请求缓存的映射。

2. **原始 Chunk 范围与实际引用范围不同。** `ContextBuilder` 输入 12–18 行 Chunk，在小预算下只发送 12–15 行。Validator 输出 12–15 行 Citation，同时验证它是原内容的完整行子切片。否则会把模型没看到的第 17 行钱包调用也算作证据。Symbol 仍表示这个片段的原所属方法，不保证 Context 含完整方法。

3. **Repository 隔离与来源一致性需要一起验证。** `CitationValidator.validate` 输入当前 Repository、原始 Retrieval Results 和 BuiltContext，输出只属于当前仓库的 Citation。它检查全批次原始结果，包括预算外 Chunk，并核对来源身份、路径、Symbol、内容和行范围；跨仓库结果、错误 UUID 或被替换的 Context 应整批失败。仅检查 S1 的拼写不能完成这些约束。

4. **整批拒绝保持回答与引用的共同边界。** Validator 接收一个 ID 批次，输出完整 Citation 元组或固定错误。`rag/service.py` 将错误转成受控 502。如果保留回答并静默删除其中一个错误 ID，用户可能收到缺少依据的回答；本次策略在任何错误下都不返回部分回答。合法空 IDs 则对应证据不足，不是 502。

5. **Citation 合法与 Grounding 正确是两种检查。** `models.py`、`validator.py` 保证 Citation 的形状、来源和坐标；它们不判断自然语言是否被代码支持。输入真实模型结果后，即使返回合规 Citation，回答仍可能无用或错误。本次 700 字符实测只重复了问题；`status=answered` 目前表示有非空合规 Citation，不能读作语义验证通过。

## 真实数据示例

真实问题：**退款服务在哪里校验订单编号，然后把退款金额转回钱包？**

使用已有本地 Ollama 生成模型 `qwen2.5:0.5b`、Embedding 模型 `qwen3-embedding:0.6b`，以及生产 `repopilot_code`（1024 维、Cosine）。TopK=5；没有 Mock 推理或重建索引。

默认 16000 字符预算下，5 个结果进入 5 个 Source，Context 实际为 4110 字符。模型原始结果只有 `answer="backend/service/RefundService.java"` 与 `source_ids=["S1"]`。最终 HTTP 200 中的 Citation 是：

```json
{
  "repository": "demo-repository",
  "source_id": "S1",
  "path": "backend/service/RefundService.java",
  "symbol": "RefundService.refund",
  "start_line": 12,
  "end_line": 18
}
```

S1 来自原始 Chunk `66b8fac4-539e-5bd2-802f-2f88b7965a2a`，`node_type=method_declaration`、`parent_symbol=RefundService`、`chunking_strategy=ast`、`chunking_version=ast-v1`。实际片段含订单编号校验和第 17 行 `wallets.credit(userId, amount);`。Metadata 来自后端，不是从回答中的文件名提取。

记录：[真实 HTTP 响应](D:/aaaagent开发文件/RepoPilot/output/stage3-task8/full/response-refund.json)、[原始检索与实际 Context](D:/aaaagent开发文件/RepoPilot/output/stage3-task8/full/context-001.json)、[实际消息/Schema/生成结果](D:/aaaagent开发文件/RepoPilot/output/stage3-task8/full/generation-001.json)、[后置 Citation 校验](D:/aaaagent开发文件/RepoPilot/output/stage3-task8/full/citation-002.json)。

临时验收进程把预算设为 700 字符；仅该进程覆盖配置，未修改 `.env`。真实结果为 5 个检索 Chunk → 1 个 Source，Context **698 字符**。该 S1 原范围 12–18，实际发送并引用 **12–15**：

```java
    public void refund(String orderId, String userId, BigDecimal amount) {
        if (orderId == null || orderId.isBlank()) {
            throw new IllegalArgumentException("Order ID is required");
        }
```

此时 Citation 的 Path/Symbol 与上例相同，`end_line` 为 15，`budget_limited=true`。钱包调用没有进入 Context。模型实际 `answer` 为 **“退款服务在哪里校验订单编号，然后把退款金额转回钱包？”**，完全重复了问题，`source_ids=["S1"]`；保留原始结果，没有手写替换答案。

记录：[小预算真实响应](D:/aaaagent开发文件/RepoPilot/output/stage3-task8/budget/response-refund.json)、[小预算实际 Context](D:/aaaagent开发文件/RepoPilot/output/stage3-task8/budget/context-001.json)、[小预算原始模型结果](D:/aaaagent开发文件/RepoPilot/output/stage3-task8/budget/generation-001.json)。

未索引仓库 `stage3-task8-not-indexed` 的真实响应为 200，`source_ids=[]`、`citations=[]`、`status=insufficient_evidence`、`model=null`、`generation.called=false`；固定回答为“根据当前索引到的代码，我没有找到足够证据回答这个问题。”记录：[空检索响应](D:/aaaagent开发文件/RepoPilot/output/stage3-task8/full/response-empty.json)。没有创建该仓库或写入索引。

共执行 **3 次真实 API 请求、2 次真实生成 HTTP 请求**。独立脚本核对每个检索 Chunk、Context Source 的内容与当前本地源码完全一致，再重放 Validator 对照最终 Citation。另基于保存的实测输入作 **8 个受控纯函数变异回放**：未知 ID、重复 ID、外仓库、非法行范围、未进入预算的 Source、截断后误用原末行、错误内容切片、Context/Map 不一致，均整批拒绝。这些回放是人为构造的错误输入测试，不冒充实际模型输出。记录：[controlled-replays.json](D:/aaaagent开发文件/RepoPilot/output/stage3-task8/controlled-replays.json)。

## 验收方法

| 检查 | 实际结果 |
| --- | --- |
| 后端完整回归 | 901 项收集；895 passed、6 skipped、2 个原有 warning |
| Citation 与 Code Chat 测试 | 103 + 68 = 171 项通过；本次新增 103 + 8 = 111 项 |
| 评测测试 | 42 passed |
| 前端测试 | 9 passed |
| 前端类型检查与生产构建 | 通过，1615 个模块 |
| 真实 HTTP | 默认预算、700 字符预算、空检索共 3 次；均 200 |
| 原始检索/Context/Citation 对照 | 精确核对来源、Metadata、源码切片及实际引用行范围 |
| 受控错误回放 | 8 项均拒绝，无部分 Citation |
| 生产 Qdrant | 17 个点；Vector、Payload、Collection Metadata 与所有其他集合完全一致 |

6 个跳过沿用原有外部集成 opt-in 与 Windows 文件符号链接条件；两个 warning 为原有框架弃用提示。没有更改跳过条件或降低检查。[后端 JUnit](D:/aaaagent开发文件/RepoPilot/output/stage3-task8/backend-tests.xml)、[评测 JUnit](D:/aaaagent开发文件/RepoPilot/output/stage3-task8/evaluation-tests.xml) 保存实际运行结果。

从工作区根目录启动现有 Qdrant 和应用，沿用已安装依赖、已下载模型和现有索引：

```powershell
Set-Location 'D:\aaaagent开发文件\RepoPilot'
.\infra\Qdrant.ps1 up
.\Start-RepoPilot.ps1 -NoBrowser
Invoke-RestMethod http://localhost:8000/health/qdrant
```

调用 `POST /api/code-chat`，显式 UTF-8 编码中文请求，并展开响应中的 Citation：

```powershell
$body = @'
{"repository":"demo-repository","question":"退款服务在哪里校验订单编号，然后把退款金额转回钱包？","top_k":5}
'@
Invoke-RestMethod -Method Post -Uri http://localhost:8000/api/code-chat `
  -ContentType 'application/json; charset=utf-8' `
  -Body ([Text.Encoding]::UTF8.GetBytes($body)) | ConvertTo-Json -Depth 8
```

应检查 `source_ids` 与 `citations[].source_id` 同序、Citation 属于当前 Repository、坐标来自后端 Sources。模型文本可能变化，不能把固定措辞当作校验成功条件。700 字符只属于保存的小预算验收进程，不是现在的默认配置。

运行相关测试：

```powershell
Push-Location 'D:\aaaagent开发文件\RepoPilot\services\api'
.\.venv\Scripts\python.exe -m pytest tests/test_citations.py tests/test_code_chat.py -q
Pop-Location
```

独立复核保存的实测、源码切片、错误回放、OpenAPI、测试 XML 与保护快照；读取当前 Qdrant，不写索引，也不重新生成回答：

```powershell
Set-Location 'D:\aaaagent开发文件\RepoPilot'
.\services\api\.venv\Scripts\python.exe output/stage3-task8/verify.py
```

保护证据：[preservation.json](D:/aaaagent开发文件/RepoPilot/output/stage3-task8/preservation.json)。生产点完整指纹保持：

```text
af643856c5172b9ff6225e64d156c4a646e936adbed81fcf80cfb00d11f43e98
```

验收 API 曾使用 `127.0.0.1:8018` 与 `:8019`，结束后均正常停止，并关闭验收 LLM SDK Client。Ollama/Qdrant 保持运行；正常应用启动方式未改变。

## 当前未实现

Task 9 的专门 Prompt Injection 审查与恶意仓库输入验证尚未完成。已有 Task 7 的 RAG Prompt 和 Untrusted Context 包装仍保留，本次没有修改它们。

尚无 Code Chat UI、Issue Analyzer、RAG Streaming、回答语义验证或大规模 Answer Evaluation。仍是 Dense Retrieval 与固定 RAG Pipeline，没有 BM25、Hybrid、Rerank、Query Expansion、自动重新索引、Tool/Agent Loop、多 Agent 或自动修改仓库代码。

## Bad Case / 风险

**真实 Bad Case：** 700 字符预算下，模型原样重复问题，仍选 S1；服务器返回合法 12–15 行 Citation。引用坐标是对的，回答没有解释校验，也没有回答钱包入账。默认预算下模型也仅返回文件名。记录：[bad-case.json](D:/aaaagent开发文件/RepoPilot/output/stage3-task8/bad-case.json)。这些是实际回答质量限制，没有被修饰成成功的完整问答。

**Metadata 校验不证明答案语义。** Validator 不分析回答里自由文本的文件名或调用链，不判断引用支持每句话。测试特意让模型在回答中编造路径/方法/行号，验证服务器 Citation 仍从后端取值；那是构造的单元输入，不是本次真实模型结果。错误自然语言仍可能留在回答中。

**索引新鲜度与来源可信度有边界。** Validator 验证本次后端检索 Metadata、Context 和 Citation 的内部对应，不实时读文件、不重新解析 AST，也不证明向量库本身从未被篡改。索引过期时，Citation 可能对应旧版本。验收脚本独立逐行对照了当前演示源码，这不代表在线接口会在每个请求里做磁盘复核。

**字符预算可能留下不完整方法。** 实际完整行截断正确限制了引用范围，但不能保证剩余片段足以回答问题，也不是精确 Token 预算。基础 Prompt 边界不能完全解决 Prompt Injection，后续需要专门审查与测试。

## 下一任务

Task 9：Prompt Injection 基础防护。审查 System Prompt、Context Format 与 Repository Content Boundary，明确仓库内容不可信、不得覆盖系统规则、不得输出 Secret、不得把 README/Comment 当作工具指令，并加入恶意仓库文本测试。只做基础防护，不宣称完全解决 Prompt Injection。本次停在 Task 8。
