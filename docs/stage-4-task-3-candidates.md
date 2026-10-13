# Stage 4 / Task 3：内部候选层

本任务将召回数量与最终返回数量分开，交付 `RetrievalCandidate`、`RetrievalChannel` 和最终结果适配器。新增 Dense 通道可召回 30 个候选，内部容量上限 60；适配器输出仍为现有 `RetrievalResult`，默认 TopK 5、范围 1–20。生产 `RetrievalService.search` 继续使用原路径。

## 接口与数据流

实现位于 [`candidates.py`](../services/api/app/retrieval/candidates.py)、[`dense.py`](../services/api/app/retrieval/dense.py)，Qdrant Store 新增 `search_repository_candidates`。原 `search_repository` 仍拒绝超过 20 的 TopK，两者共用相同查询实现、仓库过滤、Payload 选择和错误映射。

输入 `CandidateSearchRequest(repository, query, candidate_limit=30)` 的仓库名与问题沿用原验证。`RetrievalChannel.recall` 返回 `CandidateBatch`，包含有序候选、仓库、问题、通道名、候选上限、索引指纹和实测耗时。

每个 `RetrievalCandidate` 保存：

- `chunk_id`：规范 UUID；同名 Symbol、相同路径或重叠范围不能代替 Chunk 身份。
- `repository` 与 `index_revision`：隔离仓库及快照。没有原生修订号时，默认明确为 `None`；调用者可传入已核对完整快照的 `sha256:<64 位小写摘要>`。
- `source_metadata`：路径、语言、完整内容、真实 inclusive 行范围和 AST/fallback 元数据；复用现有结果验证，保留缺少全部元数据的旧 Simple Chunk，拒绝部分损坏的 AST 元数据。
- `channel_scores` / `channel_ranks`：记录各通道原始分数及排名，可容纳大于 20 的排名。分数字典与排名字典冻结，拒绝非有限分数和不匹配的键。
- `matched_queries`：保存命中的原始问题；不可为空或重复。

适配器 `to_retrieval_results` 接收已经排序的候选池，核对全部候选的仓库、指纹、唯一身份和评分通道后，切出 TopK 并连续编号。第 30 条里的外仓库或错误身份也不能被 Top5 切片隐藏。它不做融合或重排，不把候选调试字段加入生产响应。

```python
from app.retrieval.candidates import CandidateSearchRequest, to_retrieval_results
from app.retrieval.dense import DenseCandidateChannel

# provider/store/settings 由调用者管理，使用已有 Embedding 和 Qdrant 配置。
channel = DenseCandidateChannel(provider, store, settings)
pool = await channel.recall(CandidateSearchRequest("stage4-commerce", "钱包入账在哪里？", 30))
results = to_retrieval_results(pool.candidates, repository=pool.repository,
                               index_revision=pool.index_revision, top_k=5)
context = builder.build(pool.repository, results)
```

Dense 通道先验证 Collection Embedding 身份，再调用一次查询 Embedding，并检查维度、全部 Point 的身份、仓库、模型、schema、内容和坐标。它不创建 Collection、写入索引或绑定 Metadata。调用者负责关闭 Provider/Client；取消和上游错误直接向上传递。

`index_revision` 只存在于内部对象，表示调用者绑定的快照摘要，不是 Qdrant 原生修订号。通道自身不会每次扫描完整索引，也不提供并发更新检测；本次验收工具在查询前后核对完整快照。Task 5 再建立正式的索引快照生命周期。

## 三个数量限制

| 限制 | 当前值 | 作用 |
| --- | --- | --- |
| Candidate Limit | Dense 30，内部上限 60 | 通道召回可进入后续排序的 Chunk 数量 |
| TopK | 默认 5，范围 1–20 | 最终适配成公共结果的数量 |
| Context Budget | 5 Sources / 16000 字符 | ContextBuilder 按去重和真实截断后提供的证据数量与长度 |

`CandidateLimits` 冻结计划中的实验起点：Dense 30、BM25 30、Exact 10、融合 60、Rerank 30、TopK 5。后几项是后续通道的参数定义，本任务只实现 Dense。限制独立于 `.env` 和公共请求；没有新默认策略或页面字段。

## 复现与收据

运行工具 [`verify_candidates.py`](../evaluation/stage4/verify_candidates.py) 读取 Task 2 报告并先离线核对，再把已保存的 129 个 Payload/Vector 复制到随机临时 Collection。学习仓库不重新索引，生产索引不写入。

每案仅调用一次真实查询 Embedding，将同一向量用于内部 30 候选以及旧路径 Top1/5/20，避免两次 Embedding 的输入差异影响兼容判断。这个缓存只属于验证工具；生产 Provider 不使用它。候选、原始向量、三种完整旧响应、实际 Builder Context、每案指标和完整索引均独立留存。离线核对重新计算 Cosine（服务 float32 分数容差 2e-6），检查未遗漏更高分 Chunk、排名、适配结果、Context/Citation 和汇总。

```powershell
# Qdrant 与已有 Embedding 服务需可用；默认输出新时间戳报告
& '.\services\api\.venv\Scripts\python.exe' evaluation/stage4/verify_candidates.py

# 完全离线复算当前收据
& '.\services\api\.venv\Scripts\python.exe' evaluation/stage4/verify_candidates.py `
  --verify-only evaluation/reports/stage4_candidates_20261013_task3.json

Push-Location services/api
$env:REPOPILOT_QDRANT_INTEGRATION = '1'
$env:REPOPILOT_EMBEDDING_INTEGRATION = '1'
$env:REPOPILOT_INDEXING_INTEGRATION = '1'
$env:REPOPILOT_RETRIEVAL_INTEGRATION = '1'
& '.\.venv\Scripts\python.exe' -m pytest -q
Pop-Location
& '.\services\api\.venv\Scripts\python.exe' -m pytest evaluation/tests -q
```

`--reference-report` 和 `--output` 可指定路径；已有输出或部分收据目录拒绝覆盖。任一案例失败会保存案例 ID 和错误类型并中止，失败案例不会被删除后发布成功报告。清理只针对本次随机创建的 Collection；未确认删除或既有索引变化会阻止报告发布。保留集 20 问仍未检索。

## 实际结果

52 个历史/开发问题全部完成，**156 次 Top1/5/20 比较逐项一致**；其中 37 问实际返回 30 个候选，超过原有 20 条限制。历史与开发集的全部 Top5 质量指标都与 Task 2 相同，保留集未检索。

后端 **1061 项通过、1 项跳过**，包含五个真实 Embedding/Qdrant/索引/检索集成用例；新增 55 个候选测试覆盖 60 候选、TopK/Context 边界、仓库与指纹隔离、无效尾部、缺失元数据、冻结通道溯源、空结果及原 Dense 兼容。评测 **117 项通过**，新增十个收据校验测试；此前两项依赖弃用警告仍存在。前端文件与公共类型未改动，本次不重复执行前端构建。

成本为 52 次真实查询 Embedding Provider 调用、52 次候选查询、156 次旧路径 Qdrant 查询、156 次验证缓存命中，零次索引 Embedding、零次生成。候选通道的开发集 p50 / p95 为 725.05 / 927.32 ms；历史 12 问为 777.24 / 2322.29 ms，包含首次模型加载。旧路径的缓存耗时仅保留在原收据，不作为独立性能基准。这个运行验证容量与兼容性，不据此声称质量或性能提升。

后端和离线验证的最终验收见 [机器核对文件](stage-4-task-3-verification.json)。原始报告见 [`stage4_candidates_20261013_task3.json`](../evaluation/reports/stage4_candidates_20261013_task3.json) 及其 `_artifacts`：52 条逐案收据、129 点完整索引和配置快照。临时 Collection `repopilot_stage4_task3_202254a236e54357951b89bc7617c4e9` 已删除，两个既有 Collection 的完整配置、Metadata、Payload、Vector 一致。

## 成功与未解决案例

`shipping-error` 的正确异常方法在 Top1，适配保留其完整内容和坐标。`wallet-symbol` 的正式 `WalletService.credit` 在 Rank 5，`backend/legacy/WalletService.java:4–6` 的同名方法在 Rank 10，两个 Chunk 都在候选池；最终 Top5 仍只有正式方法，完整范围 Recall=1/2、逐行 Coverage=7/10。`ledger-chain` 的入口 `backend/ledger/WorkflowController.java:7–9` 在 Rank 8，Top5 必要证据范围/逐行覆盖均为 0。内部候选增多不自动改变最终排序或证据覆盖，`missing-symbol-a` 仍返回无关代码。

下一任务为 **Task 4：代码专用 Tokenizer**。它将为未来词法通道建立文档与查询一致的 Token 规则，可接入本次通道接口；BM25、Exact、Hybrid、Rerank、Expansion、Context Selection 和产品接入仍在后续任务。
