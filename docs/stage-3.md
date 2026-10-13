# Stage 3：最终验收与学习指南

Stage 3 / Task 13 于 **2026-10-13** 完成功能验收：真实检索、有限 Context、结构化生成、Citation 校验、Code Chat 和 Issue Analyzer 可运行，原始评测与逐案审阅可重放。当前模型的答案质量单独报告为未通过质量验收。

本次保持实际环境、模型、Prompt、Chunker、生产索引和 API 契约，新增验收记录与文档，并修复 Retrieval Debug 取消/超时后迟到响应恢复页面的状态错误。下一步只进入 Stage 4 / Task 1，冻结 Dense 基线。

## 架构、输入与输出

```text
repository + question / issue + top_k
  → RetrievalService：真实 Query Embedding + repository-scoped Qdrant
  → AST / fallback Chunk 的原始 Path、Symbol、行范围
  → ContextBuilder：校验、按行去重、完整行截断、字符预算
  → 单次 Structured Generation：内容与 Source IDs
  → CitationValidator：将选中的 IDs 映射为实际 Sources 坐标
  → 前端契约解析：回显原问题、答案/分析、可展开的 Code Preview
```

- `POST /api/retrieval/search`：返回排名 Chunk、Cosine Score、切块元数据和耗时；TopK 默认 5，范围 1–20。
- `POST /api/code-chat`：输入仓库与问题，输出答案、状态、Sources、Citations 和检索/生成诊断。
- `POST /api/issue-analysis`：输入仓库与 Issue，输出摘要、相关代码、候选链、分析、修改/测试建议、风险与 Sources。
- 空 Context 返回 `insufficient_evidence`，不调用生成模型。无效输出按既有契约返回受控错误；不增加修复循环。

当前实际配置：Embedding `qwen3-embedding:0.6b`；生成 `qwen2.5:0.5b`；`repopilot_code` 中 17 Points / 1024 维 / Cosine；`ast-v1` 与 `ast-fallback-line-v1`；Context 默认最多 5 个 Sources、16000 Unicode 字符。

## 本次实际验证

| 验证 | 结果 |
| --- | --- |
| 后端完整测试，启用真实服务集成 | 1006 passed，1 skipped；五个真实集成案例通过 |
| 评测测试 | 81 passed，包含 61 个既有测试与 20 个新基线测试 |
| 前端契约和状态测试 | 46 passed，包含五个新检索取消/超时回归测试 |
| 前端类型检查及生产构建 | `npm run build` 通过 |
| 历史检索评测 | 12 条真实 HTTP 搜索，新报告保存 |
| Stage 3 评测 | 16 条真实 ASGI 请求，15 次原生模型 HTTP 生成；空仓库零生成 |
| 页面验证 | Retrieval Debug、Code Chat、Issue Analyzer；正常、空证据、Sources 展开与受控迟到响应 |
| 保护核对 | 607 个既有文件；README、评测说明、配置示例及检索页面 Store 四个明确修改，其他 603 个一致；既有 Qdrant Collection 保持一致 |

跳过项是现有测试的环境条件跳过，未将任何已启用的真实集成验证算为跳过。后端另有两项既有依赖弃用提示，当前锁文件与依赖未修改。新测试第一次运行暴露了 Windows 测试夹具换行差异，已将夹具写入改为明确字节；失败日志保留，最终 81 项通过。

页面验证使用真实浏览器与正常 8000 API。取消测试在浏览器内延迟交付真实 HTTP 响应、忽略 AbortSignal，以验证已停止/清空后迟到结果不会恢复页面；该受控调度不代表后端推理已经取消。浏览器调用与下方固定评测分别留证，不混入标准分母。

该测试首先发现 Retrieval Debug 只调用 abort，未排除迟到结果，还会解除新请求的忙碌状态。最小修复在 Store 内校验当前 AbortController 身份、立即释放取消/超时状态，仅允许当前请求更新结果/错误/忙碌标记。原失败日志、新的通过记录和五个回归测试一起保留。Code Chat 与 Issue 的既有防护未修改。正常 Issue 页面还出现将 Context 标记复制为建议的真实生成失败，独立留证。

## 新评测与答案审阅

标准 12 条问题和已有标签保持原样；两个 Issue、空仓库、700 字符案例另外报告。

| 指标 | 新运行结果 |
| --- | ---: |
| 文件 Hit@5 | 11/12，91.67% |
| 文件 Macro Recall@5 | 84.72% |
| 完整标注范围 Macro Recall@5 | 73.61% |
| Symbol Macro Recall@5 | 69.05%，七个有 Symbol 标签的问题 |
| 实际 Context 覆盖全部所需证据 | 8/12 |
| Answer Supported | 5/12 |
| 完整实质回答 | 3/12 |
| Citation 坐标与预览 | 15 批回复共 46 条均有效；空对照为 N/A |

审阅由 Codex 开发助手逐案对照本次回复、选中 Sources、完整 Context 与源文件完成，并绑定新报告/回复 SHA-256。独立人类审阅待完成。准确 Symbol/Path 可以满足位置问题；多字段、多文件和行为问题还需覆盖全部要求。部分正确事实与整批引用支持分别统计。

成功案例：`refund-service` 准确定位 `RefundService.refund`，S1 12–18 行包含校验与钱包调用；`wallet-credit` 给出完整真实方法体，关键证据是 S4 10–18 行，另有多余引用。

需要关注的真实失败：

- `refund-controller` 的 Top5 命中 Controller imports/类字段，却没有 `refundOrder` 13–15 行；模型还把入口指向 Service。
- `wallet-default-balance` 的 S1 明确返回 `BigDecimal.ZERO`，模型仍说“未找到”。
- `orders-schema` 的 SQL DDL 已进入 Context，模型仍声称字段定义在 Java 文件。
- `refund-request-type` 只回答 `orderId: string`，遗漏同一 Source 中的其余两字段。
- `refund-call-chain` 仅覆盖一部分必要方法，答案只给出一个实现文件。
- `issue-direct` 把 README 作为调用节点，把修改建议作为测试建议；`issue-refund` 在完整方法可见时声称代码被截断。
- `refund-budget700` 实际输出 698 字符，Source 与 Citation 缩到 12–15 行，17 行钱包调用缺失，模型只复述问题。

这些失败和全部原始输出留存，不据此修改本次模型、Prompt 或标签。

## 运行和重放

从项目根目录执行，复用既有模型与索引：

```powershell
.\infra\Qdrant.ps1 up
.\Start-RepoPilot.ps1 -NoBrowser

# 输出使用新的时间戳，不覆盖本次报告
& '.\services\api\.venv\Scripts\python.exe' evaluation/retrieval/evaluate.py
& '.\services\api\.venv\Scripts\python.exe' evaluation/stage3/evaluate.py

# 离线测试
& '.\services\api\.venv\Scripts\python.exe' -m pytest evaluation/tests -q
Push-Location services/api
& '.\.venv\Scripts\python.exe' -m pytest tests -q
Pop-Location
Push-Location apps/web
npm.cmd test
npm.cmd run build
Pop-Location
```

真实集成验证在后端测试进程中设置 `REPOPILOT_QDRANT_INTEGRATION`、`REPOPILOT_EMBEDDING_INTEGRATION`、`REPOPILOT_INDEXING_INTEGRATION`、`REPOPILOT_RETRIEVAL_INTEGRATION` 为 `1`。测试自行使用临时仓库和唯一 Collection，并清理它们；勿将生产索引用作测试写入目标。

新的生成结果必须新建对应审阅文件。`output/stage3-task13/review_this_run.py` 的明确判定只绑定本次报告指纹，禁止给未来推理套用旧判定。

本次报告：

- [12 条检索报告](../evaluation/reports/stage3_retrieval_20261013_task13.json)及同名 `_cases` 目录的投影查询收据。
- [16 条原始报告](../evaluation/reports/stage3_grounding_20261013_task13_raw.json)、同名 `_cases` 原始收据目录、[逐案审阅](../evaluation/reports/stage3_grounding_20261013_task13_review.json)、[可重算汇总](../evaluation/reports/stage3_grounding_20261013_task13_summary.json)。
- [最终机器验收记录](stage-3-task-13-verification.json)、`output/stage3-task13/` 测试与保护快照、`output/playwright/stage3-task13-*` 页面证据。

```powershell
# 只重算本次审阅，不调用模型；输出另用新文件名
& '.\services\api\.venv\Scripts\python.exe' evaluation/stage3/review.py `
  --report evaluation/reports/stage3_grounding_20261013_task13_raw.json `
  --review evaluation/reports/stage3_grounding_20261013_task13_review.json `
  --output output/my-stage3-summary.json
```

## 必须理解的概念与后续依赖

1. **AST 是语法结构。** Tree-sitter 提供节点、Symbol、父 Symbol 和范围；不证明调用边或业务含义。Vue、SQL、配置与文档走既有 fallback。
2. **Chunk 是排名单位。** 同文件多个片段会占多个 TopK 位置；文件命中仍可能漏掉目标方法。
3. **Context 是实际证据。** Builder 按原排名去重与截断，字符预算包含序列化边界、元数据及转义；它不是 Token 预算。
4. **Citation 坐标来自后端。** 模型只选择 Source IDs；实际输出行范围优先于原 Chunk 范围。
5. **结构化输出保证结构。** 合法 JSON、HTTP 200、有效引用坐标仍需另外审阅语义支持与完整性。
6. **候选调用链是待核实假设。** 可引用文件不是可调用节点，节点存在也不证明边存在。
7. **仓库内容属于非可信数据。** README、注释、Issue 和代码字符串进入用户消息中的证据区；现有有限防护与真实失败记录见 Task 9 文档。
8. **可比较性依赖固定身份。** 源代码、Chunker、Embedding、索引、TopK、Context 预算和标签必须固定，失败保留在分母中。

任务 1–12 的实现与实验边界分别保留在 `docs/stage-3-task-*.md`。Task 13 的交付是最终功能验收、新报告、复现说明与学习指南。Stage 4 / Task 1 依赖本次已验收的新报告、原始收据、配置和索引快照，见 [Dense 基线冻结](stage-4-task-1-baseline.md)。
