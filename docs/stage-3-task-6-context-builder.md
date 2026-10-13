# Stage 3 / Task 6：Context Builder

验收日期：2026-10-07，Asia/Shanghai。仅完成 Task 6；Task 7 尚未开始。

## 本次完成

新增独立、无 I/O 的 `ContextBuilder`，输入已有 `RetrievalResult`，输出格式化 Context 和不可变 Source 记录。实现完整输入校验、按文件去重、排名保持、Source 数量限制、字符预算、完整行截断、后端 Source ID 和 Source Metadata 映射。没有在 Router 拼接源码，没有调用生成模型。

默认 `MAX_CONTEXT_CHUNKS=5`、`MAX_CONTEXT_CHARACTERS=16000`。数量限制为 1–20，字符限制为 1–200000。字符预算计算整个 `context.text` 的 `len()`，包含 Source 边界、Metadata、JSON 转义和 Source 间的两个换行，属于 Unicode 字符预算。它不计算 Token，也不包含以后 RAG 的系统提示、问题和模型输出。

新增可运行的真实检索 CLI：调用现有本地 Query Embedding 与 Qdrant，只读现有索引，然后交给 Context Builder。`--output` 直接写入 UTF-8，避免本次实际遇到的 PowerShell 管道转码损坏。两次真实运行均禁止 `ProviderFactory.create`，调用次数为 0；Embedding Provider 没有 Mock。

## 修改文件

| 文件 | 职责 |
| --- | --- |
| [context/builder.py](../services/api/app/context/builder.py) | 批量校验、按行去重、预算选择、完整行截断、Source 格式化及配置工厂 |
| [context/models.py](../services/api/app/context/models.py) | `ContextSource`、`BuiltContext`、每次访问新建的 `source_map` |
| [context/__init__.py](../services/api/app/context/__init__.py) | Context 模块的公开入口 |
| [core/config.py](../services/api/app/core/config.py) | 两个 Context 配置字段和范围 |
| [.env.example](../.env.example) | 默认预算与计量说明 |
| [scripts/context_spike.py](../services/api/scripts/context_spike.py) | 真实 Retrieval → Builder → JSON；成功或失败均关闭已创建客户端 |
| [test_context_builder.py](../services/api/tests/test_context_builder.py) | 65 个 Context 用例 |
| [test_context_spike.py](../services/api/tests/test_context_spike.py) | 16 个 CLI / 生命周期 / UTF-8 输出用例 |
| [README.md](../README.md) | 更新实际 Stage 3 进度，增加运行命令 |

新增本验收文档、[verification.json](stage-3-task-6-verification.json) 和 `output/stage3-task6` 的真实结果、复核脚本与保护证据。核对 165 个原有文件，只有 `config.py`、`.env.example`、`README.md` 三个发生预期修改。原 `.env`、依赖锁文件、前端、演示源码、评测数据和历史报告均保持。工作区没有 Git 元数据，沿用 SHA-256 快照审计，没有初始化 Git。

## 调用链 / 数据流

```text
CLI：Repository + Query + TopK + Context Budget
→ 验证参数和预算
→ 现有 EmbeddingProviderFactory（本地 qwen3-embedding:0.6b）
→ 现有 RetrievalService
→ Qdrant Dense Query（Repository Filter 在 TopK 前）
→ 有排名、分数、真实源码和 AST/fallback Metadata 的 RetrievalResult
→ ContextBuilder.build(repository, results)
  → 完整输入校验：全部结果必须属于当前 Repository
  → Deduplicate：同一 Path 已覆盖行不重复输出
  → Budget：依原排名取 Source 前缀，必要时保留完整行前缀
  → Source ID：仅给实际保留的 Source 分配连续 S1、S2…
  → Formatted Context + source_map + 预算/去重统计
→ UTF-8 JSON 文件
→ 关闭 Qdrant Client 与 Embedding Provider
→ 停止；没有 RAG Prompt 或 Generation
```

Builder 先检查整个批次，再处理去重和预算。预算之外的跨仓库结果、错误路径、矛盾的重复 ID、同文件同行内容冲突，也会让整个构建失败，不返回部分 Context。结果会重新进行严格 Pydantic 校验，不能靠模型构造绕过校验或修改已经验证过的字段。

去重按 `path + 真实行号` 进行，输入排名不变。同文件重叠只保留尚未覆盖的连续行；如果唯一行被已覆盖区域隔开，就输出不同 Source，不能把不连续的行伪装为连续行范围。完全覆盖的 Chunk 被删除；相同文本出现在不同文件或不同实际行位置时保留其来源。每个片段都占用一个 Source 名额。

Source 内容放在 JSON 字符串里，原始换行、引号、反斜杠及 Unicode 行分隔符会被转义。格式为 `[SOURCE S1]`、JSON 对象、`[/SOURCE S1]`；源码中写出的假 Source 标记不会形成新的独立边界。JSON 解码能够恢复原始源码。这是格式边界，不能据此宣称已经解决 Prompt Injection。

## 我必须理解的知识

1. **Retrieval TopK 与 Context Source 数量不同。** `RetrievalService` 的输入是问题和 TopK，输出排名列表；`ContextBuilder` 的输入是这个列表，输出预算内的 Sources。本次真实 Top5 最终只保留 S1–S3：去重与预算发生在 Retrieval 之后，不重新排名。忽略这一区别，会把所有 TopK 都塞给模型，突破预期窗口。

2. **Budget 必须计算最终格式。** `builder.py` 计量格式化字符串，输入为已验证的候选行，输出为整个 Context 或安全的完整行前缀。Metadata 和 JSON 转义也耗预算。本次方法完整 Source 为 826 字符，700 字符预算只能容纳 698 字符。只计算源码长度会低估真实输入；字符预算也不能代替 Token 计量。

3. **Source ID 表示本次实际提供的证据。** `models.py` 输出不可变的 `ContextSource`，`source_map` 把 S1 映射到真实 Chunk、Repository、Path、Symbol、实际行范围及原范围。每次 build 从 S1 重新编号。实际引用必须使用截断后的 12–15，原始 12–18 仅供诊断；误用原范围会让以后 Citation 看似包含没有送给模型的行。

4. **去重依赖来源与行范围，不能只比较代码字符串。** `builder.py` 在同文件内记录已覆盖行，先校验重叠行一致，再删除重复区域。其输出可能将一个原 Chunk 拆成多个有真实连续范围的 Sources。跨文件相同代码仍有不同来源；按内容哈希全局删除或把分离片段拼接为连续行，会丢失证据或伪造范围。

## 真实数据示例

真实 Query：**退款服务在哪里校验订单编号，然后把退款金额转回钱包？**

Repository 为 `demo-repository`，Collection 为 `repopilot_code`，模型为本地 `qwen3-embedding:0.6b`，1024 维、Cosine。原 Top5 保持不变，`max_context_chunks=3`、字符预算 16000 的输出为：

| Source | 原排名 | Path | Symbol | 实际 Lines | Strategy |
| --- | ---: | --- | --- | --- | --- |
| S1 | 1 | `backend/service/RefundService.java` | `RefundService.refund` | 12–18 | ast |
| S2 | 2 | `README.md` | null | 1–21 | fallback |
| S3 | 3 | `backend/controller/OrderController.java` | `OrderController.refundOrder` | 13–15 | ast |

该 Context 为 **3015 字符**，5 个候选中保留 3 个，遗漏 2 个；原始这次 Top5 没有重复或重叠 Chunk。完整输出：[real-context-full.json](../output/stage3-task6/real-context-full.json)，格式化文本：[real-context-full.txt](../output/stage3-task6/real-context-full.txt)。

同一真实问题再次检索，字符预算改为 **700**，输出仅有 **S1，698 字符**。实际 `start_line=12`、`end_line=15`，`original_start_line=12`、`original_end_line=18`，`budget_truncated=true`。Metadata 仍来自真实检索：

```json
{
  "source_id": "S1",
  "chunk_id": "66b8fac4-539e-5bd2-802f-2f88b7965a2a",
  "repository": "demo-repository",
  "path": "backend/service/RefundService.java",
  "symbol": "RefundService.refund",
  "node_type": "method_declaration",
  "parent_symbol": "RefundService",
  "start_line": 12,
  "end_line": 15,
  "original_start_line": 12,
  "original_end_line": 18,
  "chunking_strategy": "ast",
  "chunking_version": "ast-v1",
  "budget_truncated": true
}
```

送入 Context 的实际代码逐行如下：

```java
    public void refund(String orderId, String userId, BigDecimal amount) {
        if (orderId == null || orderId.isBlank()) {
            throw new IllegalArgumentException("Order ID is required");
        }
```

第 17 行 `wallets.credit(userId, amount);` 没有进入这个 Context。原方法虽然有该调用，未来模型也不能把它当作已经收到的证据。结果：[real-context-budget.json](../output/stage3-task6/real-context-budget.json)，完整带边界文本：[real-context-budget.txt](../output/stage3-task6/real-context-budget.txt)。文本预览文件多一个终止换行；预算以 JSON 中的 `context.text` 为准。

复核脚本对两次结果的全部检索 Chunk 和实际输出 Source，逐行核对本地源码，独立解析 Source 边界并比对 Metadata、ID、计量及 `source_map`；纯 Builder 重放完全一致。额外用受控重放重复一个真实 S1，仅调整重复输入的 rank，成功去掉 1 个重复 Chunk，格式化结果不变。这个重放不是实际 Qdrant 返回重复的实验结果：[controlled-replays.json](../output/stage3-task6/controlled-replays.json)。

## 验收方法

运行环境沿用现有 Python 3.12 虚拟环境，没有增加依赖。实际验收：

| 检查 | 结果 |
| --- | --- |
| 后端完整回归 | 730 项收集；724 passed、6 skipped、2 个原有弃用 warning |
| 本次新增用例 | Builder 65 + CLI 16 = 81 项，均通过 |
| 评测单测 | 42 passed |
| 前端契约测试 | 9 passed |
| 前端类型检查和生产构建 | 通过，1615 个模块 |
| 真实链路 | 两次真实 Query Embedding + Qdrant Top5 + Context Builder |
| 源码与 Metadata | 所有真实检索/Context 片段逐行一致，Source ID 与边界一一对应 |
| Generation | 真实运行中的 LLM Factory 调用为 0 |
| Repository 隔离 | 完整批次校验，含超出预算的外仓库结果也必须失败 |
| 生产索引保护 | 原 17 个点、全部 Payload/Vector、Collection Metadata 和其他集合完全一致 |

6 个跳过保持原有条件：外部服务集成测试 opt-in，以及 Windows 文件符号链接可用性。两项 warning 来自现有 Starlette/httpx 和 anyio 接口。Task 6 没有修改这些依赖或跳过条件。

在仓库根目录查看预算效果；`output` 目录需已存在：

```powershell
.\services\api\.venv\Scripts\python.exe services/api/scripts/context_spike.py `
  --repository demo-repository `
  --query "退款服务在哪里校验订单编号，然后把退款金额转回钱包？" `
  --top-k 5 --max-chunks 3 --max-characters 700 `
  --output output/context.json
```

把 `--max-characters` 改为 16000 可看到 S1–S3。仅运行 Context 单测：

```powershell
Push-Location services/api
.\.venv\Scripts\python.exe -m pytest tests/test_context_builder.py tests/test_context_spike.py -q
Pop-Location
```

复核本次已经保存的真实输出并核对索引保护（只读 Qdrant；不会重新检索或索引）：

```powershell
.\services\api\.venv\Scripts\python.exe output/stage3-task6/verify_context.py
```

保存的生产索引完整点指纹仍为：

```text
af643856c5172b9ff6225e64d156c4a646e936adbed81fcf80cfb00d11f43e98
```

Task 6 初次实时检查遇到 Qdrant 连接不可用；使用现有 `infra/Qdrant.ps1 up` 恢复同一容器后，恢复快照及最终快照均与任务开始时完全一致。没有重新索引、删除点或创建 Collection。证据：[initial-live-failure.json](../output/stage3-task6/initial-live-failure.json)、[preservation.json](../output/stage3-task6/preservation.json)。Qdrant 和 Ollama 保持运行。

## 当前未实现

尚未实现 `POST /api/code-chat`、RAG Prompt、生成结果、Citation Validator、Code Chat UI、Issue Analyzer 及对生成模型的 Untrusted Context 提示边界。现有 `POST /api/retrieval/search` 仍只返回检索结果。Source 格式和映射已准备好，后续模型只能输出 Source ID，最终 Path / Symbol / Lines 应由后端映射。

没有加入 BM25、Rerank、MMR、Query Rewrite、Agent Loop、多 Agent 或代码修改工具。

## Bad Case / 风险

- **真实预算丢证据：** 700 字符的 S1 只保留订单编号校验，移除了钱包调用。更大的预算也不保证 TopK 包含所有关联实现；后续必须处理证据不足，不能靠源 Chunk 的原范围补造回答。
- **完整行截断不保证完整 AST：** 12–15 保持原文本和真实行号，但 Java 方法没有闭合。`budget_truncated=true` 明确说明不完整，Builder 没有宣称这是可独立编译的方法。
- **预算可能无法容纳任何 Source：** 对真实 Top5 用 1 字符预算重放，输出空文本、零 Sources、`empty_reason=budget_too_small`。不能发出只有 Header 的 S1 或把更低排名小片段替换上来掩盖高排名丢失。
- **优先级前缀可能留下空间：** 遇到无法继续保留的高排名候选就停止，不为填满空间挑选低排名片段。语义相近但行范围不同的内容仍会消耗预算；目前去重仅处理明确的来源重叠。
- **JSON 是边界而非指令隔离：** 转义能够防止伪造独立 Source Wrapper，不能让恶意 README 或注释变得可信。真正的 RAG Prompt 和 Citation 验证留给后续任务。
- **纯 Builder 信任经校验的索引内容：** 它检查行数、Metadata、一致性和仓库范围，不读取磁盘检查索引新旧。本次验收另行核对了实际源码；生产请求的索引新鲜度仍由现有索引流程负责。
- **真实输出编码故障已修复：** 初版 PowerShell 管道中的中文 Query 被损坏。现用 `--output` 直接写 UTF-8，两次真实运行重新执行并核对中文，不使用损坏的结果作为最终证据。

## 下一任务

Task 7：实现 `POST /api/code-chat`，接通 Retrieval → Context Builder → RAG Prompt → 现有 LLM Provider → Structured Result，模型只输出 Source IDs。先完成后端；本次停在 Task 6。
