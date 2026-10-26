# Stage 4 / Task 8：Hybrid Search 与 RRF

2026-10-27 完成独立 Hybrid Retriever、排名融合和 Dense/BM25/Hybrid 对照。开发集完整范围 Recall@5 达到 89.58%，历史集的 Top5 覆盖保持原水平，但首个相关结果排序退化。实现和完整收据已冻结，生产默认仍为 Dense；页面集成留给 Task 9。

## 输入、输出与融合契约

新增 [`rrf.py`](../services/api/app/retrieval/rrf.py) 与 [`hybrid.py`](../services/api/app/retrieval/hybrid.py)，复用原 Candidate、共享快照、过滤通道与 Exact 实现。Task 1–7 的实现、旧报告和数据集未修改。

`HybridSearchRequest` 接收 repository、query、`SearchFilters`、`CandidateLimits` 和可选的显式 `ExactMatch`。初始参数在运行前固定并保存于 [`parameters-before.json`](../output/stage4-task8/parameters-before.json)：Dense 30、BM25 30、Exact 10、融合池最多 60、最终 TopK 5，Context 仍为 5 Sources / 16000 字符。既有 `CandidateLimits.rerank=30` 只是预留字段，本任务没有执行 Rerank。

RRF 参数版本为 `rrf-v1`：

```text
score(chunk) = sum(1 / (60 + original_channel_rank))
rank 从 1 开始；缺席通道贡献 0
```

每一路排名第一贡献 `1/61`，第二贡献 `1/62`。融合没有直接相加、归一化或加权 Dense Cosine、BM25、Exact 的原始分数。RRF 分数只用于排序，不是答案可信度。参数 k 通过 `RRFParameters` 显式配置；本次对照只使用预先固定的 60，没有调参。迁移到其他引擎的原生融合时仍需核对排名起点与参数定义。

按 `(repository, index_revision, chunk_id)` 去重，每个原始通道最多投一票。输入必须是不同的 Dense/BM25/Exact 单通道批次；拒绝重复通道和已经混入其他通道的候选。即使空批次，也必须具有真实快照版本，并与其他批次的 repository、query、filters 一致。

同一身份的任一源码字段不一致都会抛出索引异常；不会挑一份继续使用。所有输入在截断前验证，位于最后一名或最终 TopK 之外的冲突同样阻断。通道原始分数、原始排名和查询信息保留；输入不受修改。RRF 按分数降序、Chunk UUID 升序稳定排序，在完整合并后截取融合池，并从 1 重新编号。最终适配器再按 TopK 截取并编号，仍使用既有 `RetrievalResult`。

`HybridResult` 保留 request、parameters、各通道完整批次（含 Exact reasons）、未截断去重数量、融合候选、逐通道贡献、最终结果及耗时。每个结果的 `channel_scores` / `channel_ranks` 同时包含原通道数据与 `rrf` 数据；`rrf_contributions` 明确列出原排名、原始分数和倒数贡献。反序列化会重新计算融合、贡献、身份和最终结果，拒绝篡改。

## 运行边界

`HybridRetriever(coordinator, channels)` 要求 Dense/BM25 必选，Exact 可选，所有通道必须使用同一个 `SnapshotCoordinator`。一次 `search` 在外层受保护快照中按 Dense、BM25、Exact 顺序执行，并核对每一路候选属于该完整索引映像；各通道保留自己的前后检查。外层在返回前再次核对索引和持久化 ticket，避免在两路召回之间重建后混合版本。

通道异常、取消、重建、非 ready 状态或索引漂移都会丢弃整次请求，没有静默忽略通道或重试挑选。未来若实现降级，需要另行定义并评测。language/path prefix 仍在各通道排名与上限之前应用，融合阶段不会用后过滤弥补召回遗漏。

这些能力是内部可选接口，公共路由、请求/响应、页面和生产 Dense 路径不变。协调器对外部绕过写入和 ABA 的边界沿用 Task 5 文档，没有新增生产 `index_revision` 字段。

## 对照方法与复现

正式输入为 Task 3 真实 52 问 Top30 Dense 排名/分数、Task 6 的 BM25 收据、Task 5 的完整共享快照。先回放旧报告，再逐仓库核对全部原始 Point、Payload、Vector、配置和 Chunk；确认相同后，将 Task 3 的整个临时 Collection 指纹绑定到 Task 5 的仓库快照版本。改变的只有版本标识形式，Dense 候选、源码、分数和排名完全保留。

评测专用 `ArchivedDenseChannel` 只接受这些已保存问题的原始未过滤 Top30 请求，不能用于任意新问题或过滤后的检索。真实 Hybrid 仍使用 `FilteredDenseChannel`。标准对照复用实际 Dense 结果，不重复 Embedding 或生成；真实冒烟复用对应问题本来的 1024 维向量，没有用其他问题的向量代替。

```powershell
# 独立冻结案例 Spike，输出全部来源、RRF 贡献和最终结果
& '.\services\api\.venv\Scripts\python.exe' evaluation/stage4/evaluate_hybrid.py `
  --case-id wallet-symbol

# 已保存报告离线复算
& '.\services\api\.venv\Scripts\python.exe' evaluation/stage4/evaluate_hybrid.py `
  --verify-only evaluation/reports/stage4_hybrid_20261027_task8.json

# 新运行使用唯一输出路径；--live 另需真实 Qdrant
& '.\services\api\.venv\Scripts\python.exe' evaluation/stage4/evaluate_hybrid.py --live

Push-Location services/api
& '.\.venv\Scripts\python.exe' -m pytest -q
Pop-Location
& '.\services\api\.venv\Scripts\python.exe' -m pytest evaluation/tests -q
```

正式输出为 [`stage4_hybrid_20261027_task8.json`](../evaluation/reports/stage4_hybrid_20261027_task8.json) 及其 `_artifacts`。52 条逐案收据包含三通道候选、两/三通道融合、逐项贡献、Top5、真实 Builder Context、指标和对 Dense/BM25/不含 Exact 的逐案差值。所有旧报告单独保留。已有报告或收据目录拒绝覆盖，失败会保存错误收据并中止。

[`case-analysis.json`](../output/stage4-task8/case-analysis.json) 将每个标注范围关联到索引、通道并集、融合池、Top5、Context 的实际覆盖及重叠 Chunk/排名；[机器验收](stage-4-task-8-verification.json) 绑定源码、测试、旧报告回放和保护清单。负例单独统计，标准 12 条历史问题、32 条开发正例、8 条开发负例都保留；20 条保留集问题未查询。

## 实际质量结果

| Top5 指标 | Dense | BM25 | Dense+BM25 RRF | Dense+BM25+Exact RRF |
| --- | ---: | ---: | ---: | ---: |
| 历史 12 问：文件 Hit | 91.67% | 25.00% | 91.67% | 91.67% |
| 历史：完整范围 Macro Recall | 73.61% | 25.00% | 73.61% | 73.61% |
| 历史：Evidence MRR | 0.6042 | 0.2500 | 0.4667 | 0.4667 |
| 历史：全部证据 / Context 完整覆盖 | 8/12 | 3/12 | 8/12 | 8/12 |
| 开发 32 正例：文件 Hit | 100.00% | 96.88% | 100.00% | 100.00% |
| 开发：完整范围 Macro Recall | 79.69% | 86.46% | 89.58% | 89.58% |
| 开发：逐行 Macro Coverage | 81.27% | 88.70% | 91.83% | 91.83% |
| 开发：Symbol Macro Recall，28 问 | 76.79% | 88.10% | 88.10% | 88.10% |
| 开发：Evidence MRR | 0.5661 | 0.8307 | 0.8620 | 0.8776 |
| 开发：全部证据 / Context 完整覆盖 | 22/32 | 23/32 | 24/32 | 24/32 |

历史 Symbol 分母为七问，完整 Hybrid Macro Recall 为 69.05%，与 Dense 相同。八条负对照在四种策略中均只有两个空结果，六问仍有无关结果，外仓库 Chunk 均为 0。融合没有提供语义拒答能力。

Exact 的本轮主要作用是提前相关证据：`wallet-symbol` 的 MRR 从两通道 RRF 的 0.5 升至 1.0；Top5 覆盖没有额外增加。历史集 MRR 的退化保留在结果中，不能仅凭开发集收益就替换默认策略。保留集与完整端到端消融仍属后续任务；本次没有评测生成答案。

## 可追溯案例与失败位置

`wallet-symbol` 同时覆盖正式与 legacy 的同名 `WalletService.credit`，从 Dense 的 1/2 完整范围升至 2/2：

| Chunk 源码 | Dense 原排名 | BM25 原排名 | Exact 原排名 | RRF 排名 / 分数 |
| --- | ---: | ---: | ---: | ---: |
| backend/wallet/WalletService.java:11–17 | 5 | 2 | 2 | 1 / 0.04764268 |
| backend/legacy/WalletService.java:4–6 | 10 | 1 | 1 | 2 / 0.04707260 |

第一项分数为 `1/65 + 1/62 + 1/62`。原始 Cosine、BM25 和 Exact 分数均保存在收据中，没有参与这一加法。`refund-symbol` 的方法 `backend/refund/RefundService.java:11–17` 从 Dense 第 7 名进入 Hybrid 第 4 名，完整范围从 0 升至 1。

历史 `refund-service` 出现排序退化：`frontend/src/RefundPanel.vue:1–22` 同时出现在 Dense 第 6、BM25 第 1，融合分数约 0.03154496，排在第 1；正确的 `backend/service/RefundService.java:12–18` 仅来自 Dense 第 1，分数约 0.01639344，排至第 2。完整证据仍在 Top5，但 MRR 从 1 降为 0.5。

`wallet-chain`、`ledger-chain` 等调用链仍缺少 `backend/shared/SharedRecorder.java:4–6`。索引有该范围，但它没有进入任一召回通道的本次候选，RRF 无法补回。完整分析中有 7 个标注范围首先缺在通道并集，6 个范围在融合池中但排在 Top5 后；没有索引缺失、融合截断损失或新增 Context 损失。这些类别针对未完整覆盖的标注范围统计，不是问题数量。

自动 Exact 仍有定位意图限制：`refund-symbol` 问题同时给出目录 `backend/refund` 与方法名，既有推断优先选择该目录作为完整 path，因索引只存文件路径而返回空。指定显式 `ExactMatch` 可以确定定位目标；本任务保留 Task 7 规则，没有按本轮结果调整推断。

## 真实服务验证与耗时

唯一临时 Qdrant Collection 装入原 129 个 Point，三组实际 `HybridRetriever.search` 验证如下。每次请求保留原始 Dense 查询过滤条件、limit 和返回，以及整个 Hybrid 结果：

| 请求过滤 | Dense / BM25 / Exact 候选 | 去重并集 / 融合池 | 最终结果 | 实测总耗时 |
| --- | --- | --- | ---: | ---: |
| stage4-commerce 全仓库 | 30 / 30 / 2 | 48 / 48 | 5 | 28070.975 ms |
| java AND backend/wallet/ | 8 / 8 / 1 | 8 / 8 | 5 | 27761.296 ms |
| python，空资格范围 | 0 / 0 / 0 | 0 / 0 | 0 | 27174.973 ms |

两次原向量复用，实际 Embedding 与生成调用均为 0。空范围不执行 Dense 查询。原始索引读前读后相同，临时 Collection 已确认删除；既有两个 Collection 的完整配置、Metadata、Payload、Vector 不变。

总耗时包含当前实验实现每路与外层协调器的多次完整索引校验；纯 RRF 只占约 0.01–0.53 ms。本轮未优化这些守卫 I/O。标准离线对照的 Dense 输入是原排名，Hybrid 时间不含真实 Embedding/Qdrant，双通道表中时间仅为融合计算；这三种边界分别标记，不能据此计算产品端到端加速比。

## 回归与下一任务

新增 33 项后端测试，覆盖倒数排名、原分数保留、去重、稳定排序、完整身份冲突、70 候选到 60 的截断、最终编号、空批次、版本/过滤错配、重建/异常/取消。后端 **1254 通过、6 跳过**，两项既有依赖弃用警告保留。新增 18 项收据测试，评测 **190 通过**，包括来源/贡献/Context/指标篡改、错关联、漏案例、固定参数和真实查询证据。

独立 CLI 与无网络回放通过；旧 Task 1–7 证据继续可重算。前端和此前真实模型集成用例本次未重跑。保护清单共 1147 个实施前文件，只有 README 和评测说明计划更新，其余 1145 个指纹一致；实际 `.env`、锁文件、旧实现、数据集和评测产物保持原样。

下一任务为 Task 9：在 Retrieval Debug 页面接入 Dense/BM25/Hybrid 选择、来源/融合/最终排名、分数类别及各阶段诊断。Rerank、Expansion、Context Selection 和 Code Chat/Issue 产品接入仍在后续任务。
