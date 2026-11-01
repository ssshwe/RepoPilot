# Stage 4 / Task 11：受限 Query Expansion

2026-11-01 完成。新增确定性扩展、可选 LLM Structured Output、通道内合并与受快照保护的扩展检索入口。正式 52 问未观察到 K=1/3/5 指标提升，扩展继续默认关闭；另保存四个固定 LLM 示例中的改善、拒绝与无效输出。没有修改旧检索算法、生成 Prompt、生产请求/响应类型或页面开关，本任务停在 Task 11。

## 输入、输出和规则

实现位于 `services/api/app/retrieval/expansion.py`、`expansion_llm.py` 和 `expanded_retrieval.py`。`QueryExpander.expand` 默认不启用，支持显式选择 `deterministic` 或 `llm`。`ExpandedRetriever` 接收现有 `HybridSearchRequest` 和同一个 `SnapshotCoordinator` 下的 Dense、BM25、可选 Exact 通道，输出原请求、扩展计划、逐次调用、原始通道结果、合并排名、融合池、原问题重排和最终结果。

原始问题始终位于 `queries[0]`，不改文字或回显。每次最多两个额外查询，总计三个。确定性规则按固定顺序提出：保护范围之外的 NFKC/空白归一化、追加 camel/snake/dotted/path 标识符分词，以及保留用户定位词的聚焦表达。最多保留前两个通过检查的变体，不根据答案或标注选择。

引号/反引号内容、路径、ASCII 标识符和字词按原拼写锁定；例如 `WalletService.credit`、`backend/wallet/WalletService.java`、`"Amount  must be positive"` 在每个接受的变体中都原样存在，引用内的空格也不折叠。显式引用的 Unicode Symbol 同样保留。这个保护器是保守词法识别，不承诺理解任意自然语言中的隐含实体；中文自由文本可以被改写。

变体需通过非空、长度、受保护文本、去重与预算检查。重复建议、丢失定位词、越界或无效文本均有明确拒绝原因；不通过补调模型修复。纯归一化可保留一个明确不同于原文本的规范形式，其余等价空白改写去重。

| 默认限制 | 数值 |
| --- | ---: |
| 允许参与扩展的原问题长度 | 2000 字符，超过则只用完整原问题 |
| 单个变体 / 三条查询总长度 | 2000 / 6000 字符 |
| 额外查询 / LLM 调用 | 最多 2 / 最多 1 |
| LLM 输出 / 温度 / SDK 重试 | 最多 512 tokens / 0 / 0 |
| LLM 截止时间 / 全部变体召回截止时间 | 15 秒 / 60 秒 |
| 每个查询的 Dense / BM25 / Exact | 30 / 30 / 10 |
| 预留原始候选槽位 | 最多 210，即 3×(30+30+10) |
| 通道合并 / 最终融合池 | 30/30/10 / 最多 60 |
| 重排候选 / 最终 TopK / Context | 30 / 5 / 5 Sources、16000 字符 |

候选预算按完整查询组预留，不能只执行某个变体的一部分通道。预算仅能容纳原问题时，不调用扩展模型；空过滤范围也不扩展或新增 Embedding。所有限制均独立于外部服务自身的超时。

## 合并、原始身份和故障

先在每个通道内合并变体结果：对同一 Chunk 取最佳原始查询排名，排名相同优先原问题、再优先较早变体，最后按 UUID。保留该胜出查询的原始分数，重新编号通道合并排名，应用通道上限。每个通道只给一次 `1/(60+rank)` 的 RRF 投票；重复命中不累加查询票数。三个通道的融合分数不超过 `3/61`。

`ExpandedCandidateBatch` 明确区分“原请求问题”和“实际命中查询”。仅由变体找到的 Chunk，其 `matched_queries` 不包含原问题；完整原始批次保留查询、排名和分数，可离线追溯胜出依据。所有候选继续使用原 UUID、repository、索引 SHA-256、文件、Symbol、代码和行坐标。融合后使用 Task 10 的独立 BGE 适配器，评分问题仍是原问题，而不是变体。

扩展模型不可用、超时、输出无效或全部建议被拒绝时，只用原问题。某个变体的召回失败或超时，会丢弃所有变体的合并效果，恢复完整原问题三通道结果；已完成的变体批次仍留作诊断。原问题召回失败继续按既有错误处理。取消传播，索引漂移中止整个请求，不能伪装成可用的旧代码回退。外层快照检查覆盖原召回、扩展、全部变体和最终重排。

可选 LLM 复用当前 `qwen2.5:0.5b` 配置，通过一次 Structured Output 请求返回 `queries` 数组。保留原始文本、finish reason、token usage、请求关联和 HTTP 尝试数，不保存认证信息或上游错误文本。取消/截止时间限制的是客户端请求；不能据此证明远端模型已停止计算。未确认的 token 用量需单列为未知，此次四次调用均有确认的 usage。

## 固定真实评测

主报告：[stage4_expansion_20261101_task11.json](../evaluation/reports/stage4_expansion_20261101_task11.json)。逐案原始收据独立保存，报告通过 SHA-256 引用。输入为 Task 8 的 52 个原问题/原 Dense 排名和相同 129 个 Point；新变体使用自己的真实 `qwen3-embedding:0.6b` 向量，在独立 Qdrant Collection 的精确副本上检索。BM25、Exact 使用同一冻结代码映像。私有副本在运行前后核对并删除；生产 Collection 不变。

正式集合仍为 12 条历史问题、32 条开发正例、8 条负例，保留集未查询，数据集未增加。每条原问题及正式输出只运行一次，未根据结果改变规则或挑选答案。50 条非空问题真实重排，2 条空仓库问题保留在分母中。另做四个预声明的 LLM 示例和三个实际 Qdrant guard/filter 范围验证，均不替换正式案例。

| @5 指标 | Hybrid | Hybrid+确定性扩展 | Hybrid+Rerank | Hybrid+扩展+Rerank |
| --- | ---: | ---: | ---: | ---: |
| 开发完整范围 Recall | 89.58% | 89.58% | 92.71% | 92.71% |
| 开发完整证据 | 24/32 | 24/32 | 25/32 | 25/32 |
| 开发 Symbol Recall | 88.10% | 88.10% | 91.67% | 91.67% |
| 开发 Evidence MRR | 0.8776 | 0.8776 | 0.9844 | 0.9844 |
| 历史完整范围 Recall | 73.61% | 73.61% | 81.94% | 81.94% |
| 历史完整证据 | 8/12 | 8/12 | 9/12 | 9/12 |
| 历史 Evidence MRR | 0.4667 | 0.4667 | 0.7917 | 0.7917 |

K=1/3/5 的 File、Symbol、完整范围、必要行覆盖及 MRR 均与各自对照相同。Symbol 分母保持开发 28 / 历史 7。8 条负例仍为 2 条空结果、6 条有仓库内候选，跨仓库结果为 0。实际 ContextBuilder Sources 保持上述覆盖率；没有向答案模型发送这些 Sources，不声称答案质量变化。

扩展改变了候选池和部分融合排名，但没有改变这些正式截点指标。七条调用链的 `backend/shared/SharedRecorder.java:4–6` 仍不在融合池；四个历史标注范围仍在最终 Top5 之后。它们继续保留，不能通过扩展或重排凭空补齐。

## 实际改善、退化和 LLM 限制

改善示例来自独立 LLM `wallets-schema`：原问题“钱包表余额的默认值如何设置为零？”得到两条建议“设置钱包表余额的默认值为零”“查看设置钱包表余额的默认值”。`a97c6dde-939b-572a-ad5b-676b3cd6f75f`，`database/schema.sql:1–11` 从原融合第 6 名升到第 3 名，覆盖需要的 8–11 行，Fusion Recall@5 从 0 变为 1。原先加 Rerank 已能覆盖该问题，因此这个示例没有给 Rerank 对照带来额外收益，也不计入正式 52 问的确定性指标。

排序退化来自正式 `inventory-chain`：需要的 `WorkflowController.handle`，`948223e9-4ada-5eb8-a7e2-7c6b781e2e23`、`backend/inventory/WorkflowController.java:7–9`，由融合第 2 名降到第 3 名；同文件 2–3 行的声明 Chunk 升到第 2 名。它体现了扩展表达引入的排序干扰。本次 Top3/Top5 与重排后覆盖未下降，不能将该排名下降描述为整体 Recall 下降。具体查询、Chunk 与前后排名见 [最终分析表](../evaluation/reports/stage4_expansion_20261101_task11_analysis_final.json)。

四个固定 LLM 示例：

| 案例 | 实际结果 | 处置 |
| --- | --- | --- |
| wallet-symbol | 重复生成至 512 token 上限，finish reason=`length`，JSON 不完整 | `invalid_output`，一次调用后回退原问题，raw 保留 |
| wallet-behavior | 两条变体通过保护规则 | 执行，Fusion 覆盖仍为 0，Rerank 覆盖仍为 1 |
| refund-chain | 两条建议漏掉 `backend/refund` | 两条均 `protected_missing`，只用原问题 |
| wallets-schema | 两条变体通过 | 独立 Fusion 示例覆盖改善；Rerank 对照不变 |

这些输出说明结构约束与词法保护能阻止越界建议进入检索，但不能保证建议相关。没有为了取得成功输出而重调 Prompt、模型或重复调用。小样本示例不用于估计总体收益。

## 运行成本

正式查询数分布为：2 条只用原问题，18 条使用两条查询，32 条使用三条查询。正式新增 82 次 Embedding；四个 LLM 示例新增 4 次 Embedding；真实范围验证新增 6 次，共 92 次 Embedding 和 92 次 native Qdrant 查询。56 次原问题 Dense 排名复用有明确的原收据关联，未将原向量用于不同问题。

LLM 只有 4 次逻辑调用、4 次 HTTP 尝试，合计确认 463 prompt / 599 completion / 1062 total tokens；失败的 512-token 输出仍计入。共 54 次重排请求全部完成。答案生成调用为 0。

正式内部检索流水线操作耗时 P50 2004.889ms、P95 2184.566ms，含归档原排名加载、冻结快照检查、新变体 Embedding/Qdrant 和重排；不含评测写盘与离线复核。真实范围验证另外包含完整 Qdrant 快照保护，不能把两类耗时混作生产性能基准。Task 10 的 183ms 是重排阶段耗时，不能直接相减得到扩展增量。

每条实际操作耗时位于 `expanded.retrieval_latency_ms`，汇总见最终分析 JSON。复用指标帮助器的 `strategies.expansion.retrieval.retrieval_latency_ms` 是零占位，`expansion_rerank` 中同字段是重排阶段耗时；这些 strategy `latency_ms` 汇总不用作端到端成本。分析工具明确从原始实际操作字段重算。最初的派生分析表保留，`analysis_final` 另增加标注证据的排名变化，没有增加模型调用。

## 验证和复现

后端 1375 项通过，6 项既有外部集成测试按默认选择跳过；新增扩展测试 59 项。评测套件 258 项与独立派生分析测试 2 项均通过，共覆盖当前 260 项；前端 76 项及构建通过。三个真实范围为全部、Java + `backend/wallet/`、Rust 空范围，验证过滤在限制数量之前完成，空范围无新增模型调用。全部原有文件与 Qdrant 保护核对见 [机器验收记录](stage-4-task-11-verification.json)。

从根目录离线验算，无需权重、CUDA、Ollama、Qdrant 或网络：

```powershell
& services/api/.venv/Scripts/python.exe evaluation/stage4/evaluate_expansion.py --verify-only evaluation/reports/stage4_expansion_20261101_task11.json
& services/api/.venv/Scripts/python.exe evaluation/stage4/analyze_expansion.py evaluation/reports/stage4_expansion_20261101_task11.json --output evaluation/reports/stage4_expansion_20261101_task11_analysis_final.json --verify-only
& services/api/.venv/Scripts/python.exe -m pytest evaluation/tests -q
```

完整祖先链验算需要数分钟。离线重算检查源文件/索引关联、每个真实查询自己的 Cosine 分数、BM25、Exact 原因、通道内合并、一次投票 RRF、保存 logits 的重排、真实 Context、指标与调用数；不重新推理 Embedding、LLM 或 BGE。

新跑需要现有本地 Ollama 两个模型、Qdrant，以及 Task 10 的固定 BGE 模型和隔离 runtime。使用全新输出路径；工具拒绝覆盖已有报告或 artifacts，不自动重新索引，不改实际 `.env`：

```powershell
.\infra\Qdrant.ps1 up
& services/api/.venv/Scripts/python.exe evaluation/stage4/evaluate_expansion.py --output evaluation/reports/stage4_expansion_reproduction.json
& services/api/.venv/Scripts/python.exe evaluation/stage4/analyze_expansion.py evaluation/reports/stage4_expansion_reproduction.json --output evaluation/reports/stage4_expansion_reproduction_analysis.json
```

新运行会真实调用最多两个变体的 Embedding，并对四个固定问题各发一次 LLM 扩展请求。LLM 文本、时间及独立 Embedding 浮点结果不要求逐字复现；必须保存新收据，不替换本次正式数据。LLM 与 Rerank 的默认产品开关保持关闭。

## 下一任务

Task 12 在不改 Chunker 的前提下比较 Context Selection，保存实际选入的 Sources、覆盖和代价。Task 13 才把可选策略接入 Debug、Code Chat 与 Issue Analyzer；原问题回显、原引用坐标和错误/降级诊断都要继续保留。最终默认配置仍由 Task 14 的冻结开发配置及保留集消融决定。
