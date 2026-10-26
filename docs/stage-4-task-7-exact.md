# Stage 4 / Task 7：Exact Search 与一致过滤

已实现独立 Exact 通道、匹配诊断和三个内部检索通道共用的过滤契约。2026-10-26 的真实检索收据原样保留，最终回归与验收于 2026-10-27 完成。生产服务仍使用原 Dense 实现；本任务没有更改公共请求、响应或页面。

## 输入、输出与匹配规则

实现分为 [`scope.py`](../services/api/app/retrieval/scope.py)、[`filtered.py`](../services/api/app/retrieval/filtered.py) 和 [`exact.py`](../services/api/app/retrieval/exact.py)。它们复用 Task 3 的 Candidate、Task 4 Tokenizer 和 Task 5 受保护共享快照，未修改 Task 1–6 已冻结的实现。

`ExactSearchRequest` 继承内部 `ScopedSearchRequest`，增加 `ExactMatch(kind, value, mode='full')`，默认候选上限 10，允许范围仍为 1–60。输出 `ExactCandidateBatch` 保留 Chunk UUID、repository、仓库快照 SHA-256 `index_revision`、完整源码、原查询、Exact 分数和排名，并添加过滤范围指纹、实际 selector 和逐 Chunk 的 `MatchReason`。

| kind | full 规则 | 显式 partial 规则 |
| --- | --- | --- |
| `symbol` | 完整 Symbol 元数据相等，如 `WalletService.credit` | Symbol 字符串包含目标 |
| `short_symbol` | Symbol 最后一段相等，如 `credit` | 最后一段包含目标 |
| `path` | 完整仓库相对路径相等 | 路径字符串包含目标 |
| `filename` | 路径最后一段相等 | 文件名包含目标 |
| `text` | 整个引号内文本或整个源码物理行相等 | 原始源码包含目标 |
| `literal` | 整个词法字面量原文相等，包括字符串引号 | 某个词法字面量原文包含目标 |

规则区分大小写，保留源码原文，不做词干化、Unicode 归一化或转义解码。数字 `0` 不会完整匹配 `1000`，字符串 `"PAID"` 的 literal 目标包含引号；text 目标 `PAID` 匹配整个字符串内部。词法扫描识别引号、数字和布尔/空值关键字，并跳过 Python、Java/JS/TS/Vue、SQL 的常见注释。这是有限的词法定位器，没有执行代码，也没有声称支持所有语言的完整字面量语法。

只有显式 `mode='partial'` 才容许部分匹配；即使启用 partial，实际相等仍标注为 `full`。一次查询只有一个 selector，不会在 Symbol 不存在时降级到正文词匹配。完整 Symbol 找不到就返回空候选，即使代码字符串中恰好出现该名称。

普通 `ScopedSearchRequest` 支持确定性推断：`symbol:` / `text:` 等前缀、单个 Markdown 代码片段、完整或自然语言中唯一的英文限定 Symbol/路径、带错误/字面量提示的引号文本。多个明确定位对象返回无 selector 的空结果；复杂歧义使用显式 `ExactMatch`。例如 `WalletService.missing 在哪里？` 仍按完整 Symbol 查找。推断没有模型调用，也不将多对象问题扩展成多次检索。

`MatchReason` 包含 kind、full/partial、匹配位置、真实片段、行范围、字符偏移与 occurrence 数。元数据命中使用整个 Chunk 的行范围，字符偏移为 null；正文命中使用该片段的实际坐标。偏移按 Python Unicode 字符计数，从 Chunk 内容起点 0 开始，结束值为 exclusive，不是 UTF-8 字节或 UTF-16 下标。LF、CRLF、CR 都按实际切行计算，末尾换行不会多算一行。重复出现时返回最早的最佳匹配并记录该强度的出现次数。

Exact 分数是固定排序档位：完整 symbol/short_symbol/path/filename/text/literal 分别为 100/90/80/70/60/60，部分分别为 40/35/30/25/20/20。同分按 Chunk UUID 升序稳定排序。它不是概率，也不能直接与 Dense Cosine 或 BM25 分数相加；RRF 融合留给 Task 8。

## 过滤契约

版本为 `retrieval-scope-v1`。repository 仍必填并使用原校验；`SearchFilters(language=None, path_prefix=None)` 两项以 AND 组合。language 必须为小写索引语言名；合法但未出现的语言返回空范围。path prefix 使用 `/` 分隔的仓库相对路径，拒绝绝对路径、盘符、反斜线、遍历、空分段、控制字符和 glob。

路径前缀是区分大小写的字面字符前缀。`backend/wallet` 同时包含 `backend/wallet-old/`；指定目录边界应使用 `backend/wallet/`。过滤没有隐式路径补全或通配符。空过滤保留全仓库范围。

- Dense：从协调器验证过的快照计算允许的 Chunk UUID，使用 Qdrant 原 repository 条件 AND `HasIdCondition`，在 Qdrant 排名与 limit 之前过滤。无过滤时保留原查询条件；空范围直接返回空候选，不调用 Embedding 或 Dense 查询。
- BM25：使用原仓库的 N、DF 和平均长度，在 postings 评分阶段限制候选资格，然后排序截断。过滤不重新计算局部 IDF，所以一个 Chunk 在过滤前后的 BM25 分数一致。
- Exact：先过滤快照中的 Chunk，再做匹配、排序和截断。

三个通道都先后检查共享快照和 Dense 映像，空结果也不能绕过 `rebuilding/incomplete`。scope fingerprint 绑定版本、repository 和过滤条件；仓库 `index_revision` 仍绑定完整索引映像。生产 API 未新增这些字段。后续合并通道必须同时核对 repository、index_revision 和过滤范围。

## 运行与复现

从仓库根目录执行，已冻结的查询不需要模型或 Qdrant：

```powershell
& '.\services\api\.venv\Scripts\python.exe' evaluation/stage4/verify_exact.py `
  --repository stage4-commerce --query 'WalletService.credit' `
  --language java --path-prefix 'backend/wallet/'

& '.\services\api\.venv\Scripts\python.exe' evaluation/stage4/verify_exact.py `
  --repository stage4-commerce --query '定位订单状态字面量' `
  --kind literal --needle '"PAID"' --path-prefix 'backend/orders/'

& '.\services\api\.venv\Scripts\python.exe' evaluation/stage4/verify_exact.py `
  --verify-only evaluation/reports/stage4_exact_20261026_task7.json

# 新运行使用唯一输出文件；加 --live 要求真实 Qdrant 服务
& '.\services\api\.venv\Scripts\python.exe' evaluation/stage4/verify_exact.py --live

Push-Location services/api
& '.\.venv\Scripts\python.exe' -m pytest -q
Pop-Location
& '.\services\api\.venv\Scripts\python.exe' -m pytest evaluation/tests -q
```

其他参数为 `--limit`、`--partial` 和 `--output`。`--needle` / `--partial` 必须搭配 `--kind`。新报告与收据目录拒绝覆盖；探针失败会写入错误收据并中止，不删除失败或挑选重试答案。

正式输入为 Task 5 的 129 个冻结 Chunk、Task 6 的 52 条 BM25 收据和 Task 3 的真实查询向量。输出为 [`stage4_exact_20261026_task7.json`](../evaluation/reports/stage4_exact_20261026_task7.json) 及其 `_artifacts`，保存 24 条探针、5 组真实请求/原始 Qdrant 返回/三个通道候选、完整 Payload/Vector 克隆快照和引用指纹。[机器验收](stage-4-task-7-verification.json) 绑定源码、测试日志、独立 CLI 和保护清单。

## 实际验证与案例

24 条 Exact 功能探针全部通过，52 条未过滤 BM25 查询的完整候选、分数、排名和版本与 Task 6 完全一致。独立 CLI 正确返回正式 `WalletService.credit:11–17`。真实 Qdrant 在唯一临时 Collection 中装入原 129 个 Point，使用同一个已保存的真实 1024 维查询向量验证过滤；没有调用新的 Embedding 或生成模型。

| 真实过滤场景 | 合格 Chunk | Dense 候选 | BM25 候选 | Exact 候选 |
| --- | ---: | ---: | ---: | ---: |
| stage4-commerce 全仓库 | 106 | 30 | 30 | 2 |
| java AND backend/wallet/ | 8 | 8 | 8 | 1 |
| java AND backend/legacy/ | 3 | 3 | 3 | 1 |
| json | 1 | 1 | 0 | 0 |
| python，空资格范围 | 0 | 0 | 0 | 0 |

五次归档向量使用包含四次非空 Dense 查询和一次原 Dense 兼容比较；空范围不查询。每组正式通道内部候选上限为 30，仍属于内部候选范围；独立 Exact Spike 默认 10。Qdrant 请求收据明确含过滤 UUID 和 limit，离线重算验证 Cosine、BM25、Exact 全部候选及原始返回投影。无过滤 Dense 与原通道完全一致。另一个 71 Point 单元场景将目标放在未过滤 Top60 之外，确认三个通道过滤后仍能在 Top1 返回它。

- 同名方法：完整 `WalletService.credit` 返回 legacy 的 `4–6` 行和正式实现的 `11–17` 行；formal 过滤仅保留后者。Exact 不能单凭同名判断哪个业务实现正确，需要调用方提供范围或后续排序。
- 错误文本：`Amount must be positive` 命中正式实现第 13 行，正文偏移 `[130,153)`；显式部分目标 `must be positive` 标记为 partial，偏移 `[137,153)`。
- 字面量：`"PAID"` 命中 `OrderService.submit` 第 15 行；JSON 数字 `1000` 命中第 3 行，`0` 完整匹配为空，只有显式 partial 才返回该数字。
- 无匹配：不存在或大小写错误的 Symbol、过滤后无对应代码、空仓库和多个对象的歧义查询均保留为空收据。同名 shadow 仓库只能在指定 shadow 范围内命中。

这些是独立功能探针，没有并入标准检索评测的分母，也没有调整既有 Dense/BM25 指标。20 条保留集问题没有查询。精确定位按原文字面相等，BM25 按词法重叠，Dense 按向量语义相关；三者解决的问题不同，本任务未发布检索质量提升结论。

真实通道耗时约 7.22–7.96 秒，包含当前实验协调器多次全索引读取及快照校验；既有向量被复用，不含网络 Embedding。逐请求耗时保存在收据中，不能当作未来产品延迟或与旧 Dense 计算加速比。协调器的本地协作写入边界沿用 Task 5 文档；未声称解决绕过协调器的外部 ABA 写入。

新增 44 项后端测试，默认后端 **1221 通过、6 跳过**；新增 17 项收据测试，评测 **172 通过**。两项既有依赖弃用警告保留。收据测试首次发现校验器误把查询投影 Payload 与完整索引 Payload 比较，已修正为既有字段投影；原检索收据未重跑或改写，失败与最终测试日志分别保留。最终离线复算禁止网络连接，并回放 Task 1–6 的既有证据。前端与既有真实模型集成用例本次未重跑。

保护核对覆盖实施前的 1089 个文件；只有 README 和评测说明按计划更新，其余 1087 个既有文件指纹一致。实际 `.env`、依赖锁、源数据集和旧报告保持原样，两个既有 Qdrant Collection 完整配置/Metadata/Payload/Vector 不变，临时 Collection 已删除。最终核对时服务曾停止，使用现有 `infra/Qdrant.ps1 up` 恢复同一持久化服务后，完整快照比较通过。

下一任务为 Task 8：使用相同版本与过滤范围融合 Dense/BM25/Exact，按身份去重并实现 RRF。当前没有启用 Hybrid、Rerank、Expansion 或产品接入。
