# Stage 4 / Task 10：独立 Reranker 与真实对照

2026-10-31 完成。新增独立 `RerankerAdapter`、离线 Transformers worker、受快照保护的内部调用入口，以及固定候选的真实重排评测。使用 BAAI/bge-reranker-v2-m3，没有更换 Embedding、生成模型、Prompt、Chunker 或召回算法。本任务停在 Task 10；生产请求、页面契约及禁用的 Rerank/Expansion 开关保持现状。

## 输入、输出与边界

`services/api/app/retrieval/rerank.py` 接收已经验证的 `ScopedCandidateBatch(channel="rrf")`、原始问题和明确的 `RerankPolicy`。默认不启用。启用时只评分融合池前 30 个候选，最终取 5 个。每个 passage 固定按以下格式组成：

```text
Path: <原路径>
Symbol: <原 Symbol 或空值>
Content:
<原 Chunk 内容>
```

问题原样送入 tokenizer，最多 256 tokens；超过时返回 `input_limit`，不删改问题。passage 最多 16000 个 Unicode 码点，pair 最多 512 tokens，包含 XLM-R 的 4 个特殊 token；只截断 passage。记录完整文档及截断文档 SHA-256、字符数、原始/使用 token 数和实际 input IDs。模型输入截断不改变返回代码、行范围或后续 Context 的引用坐标。

模型输出是未归一化的 cross-encoder logit。按分数降序排序，相同分数保留原融合顺序；不解释为概率或答案可信度。增加 `rerank` 分数/排名，保留 UUID、repository、index_revision、完整 SourceMetadata、原通道分数/排名及 matched_queries。未评分的融合尾部另列 `unscored_tail_ids`，不与 logit 混排。

`RerankResult` 同时保存原融合池、全部评分输入/输出、重排候选、最终结果、阶段耗时和明确的状态/降级原因。反序列化会重算排序、身份、坐标、TopK、分数来源和尾部清单。`RerankingRetriever` 将 Hybrid 召回和重排放在同一个 `SnapshotCoordinator.use` 内；模型错误可以回退，索引漂移则中止整个请求，不能发布旧代码。

## 有界执行与模型身份

模型固定为 [`BAAI/bge-reranker-v2-m3`](https://huggingface.co/BAAI/bge-reranker-v2-m3)，revision `953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e`。权重为 2271071852 字节，SHA-256 `d9e3e081faff1eefb84019509b2f5558fd74c1a05a2c7db22f74174fcedb5286`。下载器验证 LFS SHA-256 或 Git blob 身份；worker 再验证全部本地文件，并固定权重、tokenizer 和配置的上游身份。使用 safetensors、`trust_remote_code=False`、`local_files_only=True` 和离线环境变量，运行时不下载文件。

采用模型卡的 Transformers pair-scoring 路径；[Sentence Transformers 文档](https://www.sbert.net/docs/cross_encoder/usage/usage.html)解释了 Cross-encoder 对候选逐对评分的两阶段检索用法。本实现不依赖 Sentence Transformers 包。

依赖位于 `.tools/reranker-venv`，模型位于 `.tools/reranker-model`，均不提交。API 环境、实际 `.env`、生产依赖及锁文件不变。固定 PyTorch 2.8.0+cu128、Transformers 4.57.6；完整传递依赖见 [worker-freeze.txt](../output/stage4-task10/worker-freeze.txt)。目标机是 Windows 11、Python 3.12.14、RTX 4060 Laptop 8GB、约 16GB 系统内存；此次明确选择 CUDA / FP16 / SDPA。

默认 batch 4、并发 1、执行超时 30 秒，显式预热最多 90 秒。每个适配器拒绝排队；同一配置模型目录使用 OS 文件锁，第二个进程立即得到 `busy`。复制模型目录可形成另一个独立运行实例，因此这不是跨所有 GPU 应用的资源配额。

超时或取消会 kill 并等待 worker 退出，确认释放模型锁与 GPU 资源，清理等待最多 5 秒。执行期限不等于包含进程清理的端到端返回时间：此次 1ms 超时注入实际约 317ms 返回。非预热请求的 30 秒包含冷启动。不会将无法终止的推理线程留在后台。

`timeout`、`busy`、`model_unavailable`、`out_of_memory`、`worker_failed`、`invalid_response`、`input_limit`、`closed` 均完整回退到原融合顺序和 RRF 分数，丢弃所有局部评分；错误只返回固定原因。`disabled`、`empty_pool` 不调用模型。取消继续传播，不伪造成功回退。没有自动改用 CPU；CPU 需显式指定，此次未做 CPU 性能评测。

## 固定评测与结果

正式报告：[stage4_rerank_20261031_task10_final.json](../evaluation/reports/stage4_rerank_20261031_task10_final.json)。输入是 Task 8 已冻结的完整融合池及原始 52 问，12 条历史问题、32 条开发正例、8 条负例分别统计。保留集没有调用。没有重新召回、Embedding 或生成调用；两条空仓库案例保留在 52 条中并跳过重排。50 次正式推理评分 1320 个 pair、358 次 forward，全部成功，无降级。参数在运行前固定，没有根据结果调参。

| 指标 @5 | 开发 Hybrid | 开发 +Rerank | 历史 Hybrid | 历史 +Rerank |
| --- | ---: | ---: | ---: | ---: |
| 完整范围 Recall | 89.58% | 92.71% | 73.61% | 81.94% |
| 必要行覆盖 | 91.83% | 94.95% | 73.38% | 81.72% |
| Symbol Recall | 88.10% | 91.67% | 69.05% | 69.05% |
| Evidence MRR | 0.8776 | 0.9844 | 0.4667 | 0.7917 |
| 完整证据案例 | 24/32 | 25/32 | 8/12 | 9/12 |

Symbol 分母是 28 条开发正例、7 条历史正例，没有 Symbol 标注的案例不进入该分母。报告同时保存 K=1/3/5 的 File、Symbol、完整范围指标。实际 ContextBuilder 使用 5 Sources / 16000 字符；此次 Context 覆盖率等于表中 Top5 覆盖率。Sources 未发送给生成模型，不据此声称答案质量提升。

8 条负例重排前后均为 2 条空结果、6 条有仓库内候选，跨仓库结果为 0。重排不执行拒答或阈值过滤。逐案比较未出现范围 Recall 或 Evidence MRR 下降，仍保留全部失败案例。

| 实际案例 | 原排名 → 重排排名 | 证据 |
| --- | --- | --- |
| `wallets-schema` | 6 → 1 | `a97c6dde-939b-572a-ad5b-676b3cd6f75f`，demo `database/schema.sql:1–11`，覆盖要求的 8–11 行 |
| `wallet-behavior` | 6 → 1 | `c332ddab-5cbd-5051-ad0c-2d59b441d44e`，commerce `backend/wallet/WalletService.java:11–17` |
| `refund-service` | 2 → 1 | `66b8fac4-539e-5bd2-802f-2f88b7965a2a`，demo `RefundService.refund:12–18`，超过原第 1 名 UI 片段 |
| `refund-chain` 等 7 条调用链 | 仍缺失 | `backend/shared/SharedRecorder.java:4–6` 不在融合池，重排无法补回 |
| `refund-controller` | 仍在 Top5 之后 | demo `backend/controller/OrderController.java:13–15` |
| `refund-validation-files` | 仍在 Top5 之后 | demo `backend/service/WalletService.java:10–18` |

全部 64 个标注范围的阶段诊断为：53 个覆盖、7 个不在融合池、4 个仍在最终 Top5 之后；4 个范围分布在上述两个历史案例和 `refund-call-chain` 中。没有范围因本次 Top30 预算或 Context 截断额外丢失。每个诊断均关联实际 Chunk、原/新排名及行范围。

## 目标机性能与故障收据

预热加载耗时 8914.513ms，包含文件验证、导入与模型加载。正式 50 条非空问题的重排阶段 P50 183.404ms、P95 223.810ms、最大 473.254ms；包含传输、tokenizer、模型和校验，不包含原召回与索引保护成本。正式最长 pair 为 341 tokens，没有发生截断。

正式查询的最大观察进程 RSS 为约 2.20GiB，PyTorch 峰值 allocated 显存约 1.10GiB、reserved 约 1.13GiB。RSS 是返回时采样值的最大值，不是系统级峰值；显存数字也不包含桌面及其他进程。worker 已关闭并释放 GPU。

另做 9 个预先声明的性能探针：固定 wallet-symbol 前 30 候选，将首个 passage 确定性重复以触发截断，专门观察输入长度与 batch。它们是合成性能输入，独立于正式质量指标和分母。每格只有一次实测，未用于重新选择正式配置。

| pair 上限 | batch 1 | batch 4 | batch 8 |
| --- | ---: | ---: | ---: |
| 128 | 491.441ms | 171.936ms | 118.575ms |
| 512 | 646.674ms | 204.689ms | 212.568ms |
| 1024 | 674.994ms | 280.405ms | 349.702ms |

9 个探针全部完成；最大 PyTorch allocated 显存约 1.25GiB。批内 padding 和输入长度会影响耗时，因此单次值不足以形成一般硬件结论。正式与探针总共完成 59 个重排请求、484 次 forward。

真实故障验证还包含同模型目录第二进程 `busy`、1ms 超时后确认 worker 已退出、缺失模型的融合回退。超时发生在没有成功收据的请求中，实际完成了多少 forward 未知，报告保留为 1 个中断请求，不声称零计算。故障输出和性能探针分别保存，不能替换正式结果。

初次工具运行还发生过收据校验阻断：错误地复用了要求线上字段的校验器。第一次加载与一次模型推理成功，但原实现先校验再写收据，退出后首条 raw score 未留存；不能离线核验其分数或 forward 数。失败目录、日志及 [first-run-failure.json](../output/stage4-task10/first-run-failure.json)完整保留。修复后先写逐案收据，再校验，完整新跑 52 条；没有查看或挑选初次分数。该额外一次推理不计入正式指标，单独计入实施调用记录。整个 Task 10 没有 Embedding 或生成调用。

## 复现

从仓库根目录运行。离线验算只需要现有 API Python 环境，不需要权重、CUDA、Qdrant、Ollama 或网络：

```powershell
& services/api/.venv/Scripts/python.exe evaluation/stage4/evaluate_rerank.py --verify-only evaluation/reports/stage4_rerank_20261031_task10_final.json
& services/api/.venv/Scripts/python.exe -m pytest evaluation/tests -q
```

它验证 Task 8 及更早的输入关联、源码和索引快照，再从保存的 logits 重算排名、坐标、Context、指标、性能汇总和调用计数。它不重新计算模型 logits；重现推理需要下面的完整模型与 runtime。

在新的隔离目录安装并验证目标 CUDA 环境；下载器拒绝覆盖文件：

```powershell
& services/api/.venv/Scripts/python.exe -m venv .tools/reranker-venv-new
& .tools/reranker-venv-new/Scripts/python.exe -m pip install torch==2.8.0 --index-url https://download.pytorch.org/whl/cu128
& .tools/reranker-venv-new/Scripts/python.exe -m pip install -r output/stage4-task10/worker-freeze.txt
& services/api/.venv/Scripts/python.exe evaluation/stage4/download_reranker.py .tools/reranker-model-new
& services/api/.venv/Scripts/python.exe evaluation/stage4/evaluate_rerank.py --python .tools/reranker-venv-new/Scripts/python.exe --model-directory .tools/reranker-model-new --output evaluation/reports/stage4_rerank_reproduction.json
```

首次实施中大文件传输改为分段下载以绕过慢速单连接；[download_torch.py](../output/stage4-task10/download_torch.py)验证官方 wheel SHA-256，[resume_model.py](../output/stage4-task10/resume_model.py)对保留的前缀及补全部分再次验证完整上游身份。它们属于下载修复记录，没有改变模型内容。时间、进程 PID 和硬件噪声不要求逐字复现；质量对照必须保持同一来源、候选、模型与参数。

旧报告和 artifacts 均拒绝覆盖，源文件、Task 8 收据或模型关联漂移会中止。测试命令及实际结果见 [验收记录](stage-4-task-10-verification.json)；新增测试覆盖身份/坐标、Top30 尾部、各类模型故障、取消清理、快照漂移、报告关联、分母、真实 Sources、探针隔离和离线重算。

## 默认设置与下一步

本次结果支持把 BGE 重排保留为可选能力：Top5 完整覆盖各增加 1 个案例，约增加 183ms 中位阶段耗时及独立 GPU 模型驻留；仍有 7 条调用链缺少召回证据。默认继续关闭，Task 14 在开发配置冻结后再用保留集决定最终默认值。当前 Debug API 仍拒绝 `rerank=true`，没有创建生产 index_revision 字段。

Task 11 是受限 Query Expansion，应保留原问题、限制变体及候选预算，合并同通道变体后再融合，并用本适配器对原始问题重排。Task 12 的 Context Selection 和 Task 13 的产品接入尚未开始。
