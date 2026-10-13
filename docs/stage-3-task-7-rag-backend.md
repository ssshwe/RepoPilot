# Stage 3 / Task 7：RAG Backend

验收日期：2026-10-07，Asia/Shanghai。仅完成 Task 7；Task 8 尚未开始。

## 本次完成

新增真正的 `POST /api/code-chat`，接通现有 Retrieval Service → Task 6 Context Builder → 独立 RAG Prompt → 现有 LLM Provider → Structured Result。检索与生成都使用已有本地 Ollama 配置，分别为 `qwen3-embedding:0.6b` 和 `qwen2.5:0.5b`，没有安装依赖、切换模型或修改凭据。

输入只接受 `repository`、`question`、`top_k`。Repository 使用现有可移植名称校验；问题为有效 UTF-8、非空字符串，最多 8192 字符；TopK 为严格整数 1–20，默认 5。校验失败发生在 Provider/Client 创建前，返回不回显原问题的 422，包括包含非法 Unicode 的请求。

模型的输出 Schema 只有 `answer` 与 `source_ids`。Source ID 的枚举由本次 Context 动态生成，禁止模型输出 Citation 的路径、Symbol、行号等额外字段。结果再次严格校验，拒绝不存在、重复或格式错误的 ID，以及绕过 Pydantic 构造的错误对象。错误输出返回受控 502，没有应用层重试、修复循环或 Agent Loop。

Context 为空时直接返回证据不足，**不解析或调用 LLM Provider**；模型返回空 `source_ids` 时，也用固定证据不足消息替换模型的未引用文本。API 附带 Repository、Status、模型、检索和生成计量。

当前 `status=answered` 只表示模型给出了非空的合规 Source IDs，**不代表已经证明回答语义正确**。正式 Citation 对象和独立 Citation Validator 留到 Task 8。

## 修改文件

| 文件 | 职责 |
| --- | --- |
| [schemas/code_chat.py](../services/api/app/schemas/code_chat.py) | 请求、模型输出及 API 响应契约，严格类型和 Source ID 基础约束 |
| [rag/prompt.py](../services/api/app/rag/prompt.py) | RAG System Prompt、Untrusted Context 包装、本次 Source ID 枚举、消息空间预留 |
| [rag/service.py](../services/api/app/rag/service.py) | 固定检索/构建/生成流程，空证据回退、输出二次校验和安全错误 |
| [rag/__init__.py](../services/api/app/rag/__init__.py) | RAG 模块入口 |
| [api/code_chat.py](../services/api/app/api/code_chat.py) | 薄 Router、客户端生命周期，调用 Service |
| [core/exceptions.py](../services/api/app/core/exceptions.py) | `RAGResultError` |
| [main.py](../services/api/app/main.py) | 注册新 Router、RAG 502、新接口安全 422；其他接口沿用原校验响应 |
| [test_code_chat.py](../services/api/tests/test_code_chat.py) | 60 个新用例，含真实 Provider 协议适配器与内存客户端桩测试 |
| [README.md](../README.md) | 新 API 用法、实际阶段进度和限制 |

新增本验收文档、[verification.json](stage-3-task-7-verification.json) 和 `output/stage3-task7` 的真实 HTTP 记录、测试 XML、独立复核脚本。保护核对了 250 个原有文件，只有 README、`main.py`、`exceptions.py` 发生预期修改。原 `.env`、依赖锁文件、Context Builder、前端、演示源码、评测数据及 Task 1–6 证据均保持。

## 调用链 / 数据流

```text
POST /api/code-chat：Repository + Question + TopK
→ FastAPI / CodeChatRequest 严格校验
→ 现有 Embedding Provider + Qdrant Client
→ RetrievalService.search（Question 作为 Query）
→ Collection 模型身份核对 / Query Embedding / Repository Filter / Dense TopK
→ Context Builder：校验全批次 → 去重 → Budget → Source IDs
→ 空 Context？
  → 是：证据不足，source_ids=[]，model=null，generation.called=false
  → 否：
    → System：专用 RAG 规则
    → User：明确包围的 Untrusted Repository Context
    → User：本次 Question
    → 本次 Source ID 枚举构成的 Structured Output Schema
    → 现有 Provider.complete_structured，调用一次
    → 严格二次校验 answer/source_ids
    → 非空 IDs：返回回答与 Source IDs
    → 空 IDs：固定证据不足消息
→ 关闭本次 Qdrant / Embedding Client
```

LLM Provider 复用 Stage 1 的缓存 Factory；Embedding 和 Qdrant Client 属于本次请求，在成功、失败和取消时经 `finally` 关闭。没有新增自动索引、扫描、解析或 Tool Calling。无 Context 的请求不要求生成模型已配置可用。

Context 的默认预算仍为 5 个 Source / 16000 字符。已有 `ChatMessage` 最多 20000 字符；服务在构建 Context **之前**预留 Repository、Source ID 列表和外层边界的空间。当配置预算大于这一容量时，降低实际 Context 预算，并在响应的 `context_budget_characters` 中展示；不会生成 Source IDs 后再次切断 Context。这仍是字符计量，不是模型 Token Window 的完整计算。

Prompt 明确说明 README、注释、Issue 和字符串是数据，不能覆盖系统指令。源码仍用 Task 6 的 JSON 转义和独立 Source Wrapper；Context 始终在 user role 中。它属于基础防护，不能保证完全解决 Prompt Injection。

## 我必须理解的知识

1. **Retrieval 与 Generation 使用不同模型、解决不同问题。** `rag/service.py` 将问题交给 `RetrievalService`，Embedding 模型输出向量并找代码；随后把真实片段交给生成模型，输出 `answer/source_ids`。拿到 TopK 只是检索成功，不说明模型已经读懂或正确解释了代码。本次真实 Source S1 找对了方法，模型仍只回答文件名。

2. **Context Injection 是消息构造，不是重新索引。** `rag/prompt.py` 输入本次问题和 `BuiltContext`，输出固定的 system/user/user 消息。System 规定行为，User 承载不可信源码与问题。如果把 README 的指令并入 System，会给仓库文本不应拥有的权限。本次真实生成输入完整保存，核对确实包含 4110 字符 Context。

3. **结构化正确不等于 Grounding 正确。** 动态 Schema 输入本次 Source IDs，输出只允许那些 IDs；本地二次校验拒绝损坏对象。但真实初版 PostgreSQL 问题引用合法 S5，仍错误地用局部证据作出“未实现”判断。Schema 只能约束形状与 ID，不能证明引用支持答案。

4. **模型的 Source IDs 与最终 Citation Metadata 分工不同。** `schemas/code_chat.py` 让模型只选 IDs，后端 Context 保有真实 Path、Symbol、Lines。Task 7 返回 IDs；Task 8 再把它们映射为正式 Citation。自然语言回答里的文件名，即使本次恰好正确，也不能直接充当可信 Citation Metadata。

5. **证据不足和服务故障应有不同处理。** Service 输入空 Context 时输出 200 / `insufficient_evidence`，不调用 LLM；非空 Context 的模型给空 IDs 时也回退。网络、错误索引或不合法输出则使用 503/404/409/502 等受控错误，不能把故障伪装为正常的“没有找到代码”。

## 真实数据示例

真实退款 Query：**退款服务在哪里校验订单编号，然后把退款金额转回钱包？**

本地 Ollama + 生产 `repopilot_code`，1024 维、Cosine、TopK=5。实际 Context 为 **4110 字符**，Source 表如下；这些 Metadata 来自记录的后端 Context，不是模型填入的 Citation：

| Source | Path | Symbol | Lines |
| --- | --- | --- | --- |
| S1 | `backend/service/RefundService.java` | `RefundService.refund` | 12–18 |
| S2 | `README.md` | null | 1–21 |
| S3 | `backend/controller/OrderController.java` | `OrderController.refundOrder` | 13–15 |
| S4 | `backend/controller/OrderController.java` | null | 1–5 |
| S5 | `frontend/src/types.ts` | `RefundRequest` | 1–5 |

S1 内真实存在订单编号校验和 `wallets.credit(userId, amount);`。实际最终 HTTP 200 的核心响应为：

```json
{
  "answer": "backend/service/RefundService.java",
  "source_ids": ["S1"],
  "repository": "demo-repository",
  "status": "answered",
  "model": "qwen2.5:0.5b"
}
```

这份回答指向真实文件，**没有解释方法步骤**。没有替换成导师手写答案或宣称模型回答完整。记录：[response-refund.json](../output/stage3-task7/final/response-refund.json)、[实际消息/Schema/模型输出](../output/stage3-task7/final/generation-001.json)、[真实输入 Chunk 与 Context](../output/stage3-task7/final/context-001.json)。每个 Source 和每个原始检索 Chunk 都经独立脚本逐行核对本地源码。

未索引仓库 `stage3-task7-not-indexed` 的真实请求得到 0 个结果：

```json
{
  "answer": "根据当前索引到的代码，我没有找到足够证据回答这个问题。",
  "source_ids": [],
  "status": "insufficient_evidence",
  "model": null,
  "generation": {"called": false, "latency_ms": 0.0}
}
```

记录：[response-no-index.json](../output/stage3-task7/final/response-no-index.json)。沿用现有 Retrieval 的空结果语义，没有创建这个仓库或修改索引。

PostgreSQL 主从切换问题最终返回证据不足和空 IDs。模型原始结构化结果是 `answer="无" / source_ids=[]`，Service 替换为固定的证据不足消息：[最终响应](../output/stage3-task7/final/response-unsupported.json)、[原始模型结果](../output/stage3-task7/final/generation-002.json)。

两轮各执行上述 3 个真实 HTTP 请求，合计 **6 个 API 请求、4 次真实生成**；每轮的空检索请求都跳过 LLM。通过本地 SDK HTTP 请求 Hook 核对，本次每个有 Context 的请求均只有 **1 条实际生成 HTTP 请求**。Hook 只记录方法、Host 和 Path，没有记录 Header 或凭据。

## 验收方法

| 检查 | 实际结果 |
| --- | --- |
| 后端完整回归 | 790 项收集；784 passed、6 skipped、2 个原有弃用 warning |
| 本次 Code Chat 测试 | 60 项通过 |
| 评测测试 | 42 passed |
| 前端契约测试 | 9 passed |
| 前端类型检查与生产构建 | 通过，1615 个模块 |
| 真实后端 HTTP | 两轮共 6 个请求；真实 Embedding、Qdrant 和生成，无 Mock 推理 |
| 空证据与输出边界 | 空检索跳过生成；空 IDs 回退；错误 ID/字段/对象返回受控 502 |
| Repository 隔离 | 全批次校验在生成前，含预算以外的外仓库 Chunk |
| 原生产索引 | 17 个点、全部 Vector/Payload、Collection Metadata 及其他集合完全一致 |

6 个 skip 和两项 warning 延续已有外部集成 opt-in、Windows 文件符号链接条件及框架弃用接口。没有通过修改 skip 或降低验证让测试通过。后端和评测的实际 JUnit 记录分别为 `output/stage3-task7/backend-tests.xml`、`evaluation-tests.xml`。

按现有 README 启动 API、Ollama 和 Qdrant，发送请求：

```powershell
$body = @'
{"repository":"demo-repository","question":"退款服务在哪里校验订单编号，然后把退款金额转回钱包？","top_k":5}
'@
Invoke-RestMethod -Method Post -Uri http://localhost:8000/api/code-chat `
  -ContentType "application/json" -Body ([Text.Encoding]::UTF8.GetBytes($body))
```

运行新测试：

```powershell
Push-Location services/api
.\.venv\Scripts\python.exe -m pytest tests/test_code_chat.py -q
Pop-Location
```

独立复核已经保存的实测、Source、测试 XML、OpenAPI 与索引保护，不会重新调用生成模型：

```powershell
.\services\api\.venv\Scripts\python.exe output/stage3-task7/verify.py
```

保护证据：[preservation.json](../output/stage3-task7/preservation.json)。完整生产点指纹仍为：

```text
af643856c5172b9ff6225e64d156c4a646e936adbed81fcf80cfb00d11f43e98
```

临时验收 API 位于 `127.0.0.1:8017`，两轮结束均已正常停止，并关闭验收 LLM SDK Client；Qdrant/Ollama 保持运行。生产 API 启动方式保持。

## 当前未实现

没有独立 `CitationValidator`、最终 Path/Symbol/Lines Citation 对象、Code Chat UI、Issue Analyzer、Streaming RAG、回答语义验证或大规模 Answer Evaluation。没有增加 BM25、Rerank、Query Rewrite、自动索引、扫描、Agent Loop、多 Agent 或代码修改能力。

Task 7 对 Source IDs 做结构化边界校验，避免错误 IDs 直接返回；Task 8 仍需独立验证映射记录的 Repository、路径、Symbol 和实际行范围，并输出正式 Citation。

## Bad Case / 风险

**真实无依据的否定判断：** 初版问“当前仓库是否实现了 PostgreSQL 数据库主从切换”，模型返回 `实现位置：未实现`，引用 S5。S5 是 README 1–21，没有 PostgreSQL 主从切换的证据；局部 Top5 不能证明全仓库不存在实现。记录：[初版实际响应](../output/stage3-task7/response-unsupported.json)、[对照记录](../output/stage3-task7/bad-case.json)。

追加明确的 Prompt 规则“Context 没展示不代表全仓库未实现”，保留相同问题、模型、源码和索引再次验证。最终模型给空 IDs，API 返回证据不足。这是该实测的改善，不是对所有问题的安全保证。

**仍存在回答质量问题：** 退款回答前后都仅返回文件名，虽然 S1 正确，未解释校验与入账步骤。当前 0.5B 模型、Prompt 和检索组合的能力有限，后续应通过真实回答评测判断质量；没有因这次结果临时更换模型或补写回答。

**其他限制：** 合法 Source ID 不能证明语义支持，后续 Citation Validator 也不能单独消除幻觉。字符预算不等于 Token Window；完整行截断可能留下不完整方法。应用层只调用一次 Provider，没有修复循环；沿用既有 SDK 的传输重试配置，本次实测每次生成 HTTP 为 1 条。基础 Untrusted Context 边界不能保证完全防御 Prompt Injection。

## 下一任务

Task 8：建立独立 Citation Validator，把模型 Source IDs 与本次后端 Metadata 对照，处理未知/重复/跨仓库/空 Citation/非法行范围，并返回来自服务器真实 Metadata 的 Citation。完成后再进入后续任务；本次停在 Task 7。
