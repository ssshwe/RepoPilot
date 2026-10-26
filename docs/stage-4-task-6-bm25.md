# Stage 4 / Task 6：BM25 独立检索

实现 [`BM25Index` 与 `BM25CandidateChannel`](../services/api/app/retrieval/bm25.py)，对 Task 5 共享快照中的真实 Chunk 独立进行关键词检索。默认参数在运行开发集前固定为 `k1=1.5`、`b=0.75`，未按结果调参。生产检索、请求/响应和页面仍保持原 Dense 路径。

## 输入输出与评分

每个仓库独立计算文档数 N、Token 词频 TF、文档频率 DF 和平均文档长度。输入字段仍为 Task 4 的内容、路径和 Symbol；文档长度指词法 Token 数，不是行数、字符数或模型 Token 数。其他仓库不参与本仓库的 DF/平均长度统计。

使用正 IDF 的 Okapi BM25 变体，版本 `bm25-okapi-v1`：

```text
IDF(t) = log(1 + (N - DF(t) + 0.5) / (DF(t) + 0.5))
L(d)   = 1 - b + b * length(d) / average_length
score  = sum_unique_query_terms(IDF(t) * TF(t,d) * (k1+1) / (TF(t,d) + k1*L(d)))
```

文档中重复词保留词频；查询中同一词只贡献一次。没有停用词、翻译、扩展、字段加权或 Exact 匹配。`k1` 控制 TF 饱和速度，越大越容许重复词增加分数；`b=0` 关闭长度归一化，`b=1` 完全按相对长度归一化。输入拒绝非有限参数，允许 `0 < k1 <= 100`、`0 <= b <= 1`。

只返回有共享词且分数为正的候选；空语料、无有效 Token、零匹配均返回空候选，不补入零分 Chunk。分数相同按 Chunk UUID 升序排序，以保证复现。BM25 分数不作为答案正确性置信度，也不与 Cosine 分数直接相加。

通道实现 Task 3 的 `RetrievalChannel.recall(CandidateSearchRequest)`，默认 30 个候选、内部上限 60。候选保留真实 Chunk ID、repository、仓库 `index_revision`、完整源码坐标和 `matched_queries`，并提供 `channel_scores['bm25']` / `channel_ranks['bm25']`。最终适配仍为 TopK 5（范围 1–20），Context 仍为 5 Sources / 16000 字符。

通道通过 Task 5 协调器读取同一个冻结快照，前后核对索引和 ticket 后才返回结果；即使无有效词，也不能绕过 `rebuilding/incomplete` 状态。每个通道最多缓存八个语料索引，缓存键包括快照完整版本及 k1/b；重建或参数变化不会复用旧统计。解释接口 `BM25Index.explain` 给出每个命中词的 TF、DF、IDF、文档/平均长度、归一化值和分数贡献。

## 独立入口与复现

```powershell
# 独立 Spike：使用冻结索引，无需模型或 Qdrant
& '.\services\api\.venv\Scripts\python.exe' evaluation/stage4/evaluate_bm25.py `
  --repository stage4-commerce --query 'WalletService.credit' --candidate-limit 10 --top-k 5

# 新报告：历史 12 + 开发 40；默认纯快照检索
& '.\services\api\.venv\Scripts\python.exe' evaluation/stage4/evaluate_bm25.py
# 加上 --live-smoke 执行只读真实 Qdrant 正常/零匹配/无词/空索引冒烟

# 离线重算已保存对照
& '.\services\api\.venv\Scripts\python.exe' evaluation/stage4/evaluate_bm25.py `
  --verify-only evaluation/reports/stage4_bm25_20261026_task6_final.json

Push-Location services/api
& '.\.venv\Scripts\python.exe' -m pytest -q
Pop-Location
& '.\services\api\.venv\Scripts\python.exe' -m pytest evaluation/tests -q
```

支持 `--k1` / `--b` 的显式实验配置；标准评测固定候选 30 / TopK 5，20 条保留问题未查询。已有报告和收据目录拒绝覆盖；失败案例会保存错误类型并中止。`--reuse-cases` 可沿用完整逐案收据，逐条重算分数、坐标、Context 和指标后才接受，原始收据以 SHA-256 关联。

最终报告为 [`stage4_bm25_20261026_task6_final.json`](../evaluation/reports/stage4_bm25_20261026_task6_final.json)，其 `_artifacts` 含 52 条逐案收据、候选 TF/IDF 解释、实际 Context 和四个真实只读冒烟收据；[机器验收](stage-4-task-6-verification.json) 绑定测试与保护清单。

## 相同语料的 Dense 对照

BM25 使用 Task 5 的 129 个冻结 Chunk，逐案核对与 Task 2 Dense 收据的 ID、内容和坐标相同。直接复用已保存的真实 Dense 结果，没有重新调用 Embedding 或生成模型。首轮 52 个 BM25 案例已全部成功保存；随后发现独立冒烟错误地使用了不存在的 `RefundService.refundOrder`，将其修正为实际方法 `RefundService.refund`。正式 52 条收据原样沿用，未重跑或挑选其结果。

| @5 指标 | 历史 Dense，12 问 | 历史 BM25，12 问 | 开发 Dense，32 正例 | 开发 BM25，32 正例 |
| --- | ---: | ---: | ---: | ---: |
| 文件 Hit | 91.67% | 25.00% | 100.00% | 96.88% |
| 完整范围 Macro Recall | 73.61% | 25.00% | 79.69% | 86.46% |
| 逐行 Macro Coverage | 73.38% | 25.00% | 81.27% | 88.70% |
| Symbol Macro Recall | 69.05%，七问 | 14.29%，七问 | 76.79%，28 问 | 88.10%，28 问 |
| Evidence MRR | 0.6042 | 0.2500 | 0.5661 | 0.8307 |
| 全部证据 / Context 完整覆盖 | 8/12 | 3/12 | 22/32 | 23/32 |

八条开发负对照单独统计：BM25 与 Dense 都只有两个空结果，均有六问返回无关候选；外仓库结果均为 0。全部标准案例与失败均在分母内，没有删除纯中文查询。

BM25 的快照通道 p50 / p95 为历史 15.86 / 18.10 ms、开发 135.25 / 170.74 ms，包含 SQLite 加载、Tokenizer/快照完整性校验和冻结映像检查。它没有执行网络 Embedding 或 Qdrant 查询，不能与旧 Dense 的真实服务耗时计算加速比。另行完成四个真实 Qdrant 只读通道冒烟，其实测耗时保存在独立收据中。

这些是受控学习仓库上的对照。结果支持保留两个互补通道；BM25 在历史中文语义问题上明显弱于 Dense，本任务没有替换生产默认策略。

## 可解释成功与失败

- `wallet-symbol`：Top1 为 `backend/legacy/WalletService.java:4–6`，Top2 为正式 `backend/wallet/WalletService.java:11–17`，完整覆盖两个同名 `credit` 方法。Dense 只覆盖正式方法，范围 Recall 为 1/2。对 Legacy 方法，`walletservice.credit` 的 DF=2、IDF=3.7565，贡献 4.0534；`credit` 的 TF=2、DF=3，贡献 5.1555。完整分数 19.8013 来自所有命中词贡献之和，而非 Exact 规则。
- `orders-error`：`OrderService.submit:11–17` 在 Top1，包含真实错误字面量 `Cart must contain an item`。源码行和引用坐标通过 Builder/Citation 校验。
- `ledger-chain`：BM25 召回了入口与 Service 两个必要范围，但缺少 `SharedRecorder.record:4–6`，只覆盖 2/3 范围、10/13 必要行，仍未完整解决调用链。
- 历史 `refund-service`：纯中文问题返回 `frontend/src/RefundPanel.vue:1–22`，漏掉 `RefundService.refund:12–18`；`config-retries` 返回空结果，未覆盖 `config/application.json:2–3`。分词没有将中文概念翻译为英文实现。

## 验证与下一任务

新增 17 项 BM25 数学/通道测试；默认后端 **1177 通过、6 跳过**。新增 14 项收据完整性测试；评测 **155 项通过**。两个既有依赖弃用警告保留。真实只读冒烟验证了正确 Symbol、零重叠、标点/Emoji 和空索引范围，未写生产索引、未调用模型。前端、实际 `.env`、依赖锁、旧数据集及 Task 1–5 产物保持原样；前端和既有真实模型集成用例本次未重跑。

下一任务为 Task 7：Exact Search 与一致的 language / path prefix 过滤契约。Hybrid、Rerank、Expansion 和产品接入仍在后续任务。
