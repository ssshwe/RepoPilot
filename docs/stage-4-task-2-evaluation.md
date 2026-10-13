# Stage 4 / Task 2：数据集、指标与评测器

本任务完成固定标签、分组拆分、Dense 真实运行和离线重算。数据集共 **72 问：原 12 条历史回归 + 40 条开发案例 + 20 条保留案例**。生产模型、Chunker、检索与 API 保持原样；评测写入独立临时 Collection，并在结束后删除。

## 数据来源与标签

新增可控的本地学习仓库：`stage4-commerce` 含 29 个文件 / 106 个 Chunk，`stage4-shadow` 含两个干扰文件 / 6 个 Chunk，`stage4-empty` 是空仓库负对照。另保留 `demo-repository` 的八个文件 / 17 个原 Chunk。

Commerce 覆盖钱包、退款、订单、库存、发货、登录、账本、发票、订阅、风控、通知、优惠券、SQL、配置与文档。它包含正式/legacy 两个 `WalletService.credit`、不同目录下的同名 `WorkflowController.handle`；shadow 中也有同名钱包方法和独有 Symbol。代码是手写学习夹具，不作为生产系统或真正未见过的外部仓库。

每案保存：`id`、原始 `query`、`repository`、`category`、`group_id`、`split`、`source_revision`、`source_hash`、`expected_paths`、`expected_symbols`、带 snippet 的 inclusive `evidence`、`required_facts` 和 `annotator`。无 Git 元数据，因此 revision 如实为 null，源文件全清单及聚合 SHA-256 提供身份。

标签在真实检索前写定。加载器核对整个仓库的可索引文件清单及哈希、Snippet/行范围、实际 AST 的 Path + Symbol；同文件可标多个必要范围，重复范围拒绝。历史 12 问的 Query、路径、范围、Symbol 和顺序与旧数据逐项一致。

拆分按业务任务族进行，同一业务下的 Symbol、行为、错误/字面量及链路问题留在同一 split；所有负对照改写同组。重复原问题不能跨 split。开发集包含 32 个正例、八个负对照；保留集含五个独立业务族、20 问。本任务只验证保留集的标签，**没有检索保留问题**，未基于其结果调参。保留源码仍是完整检索语料的一部分。

独立人类复核待完成，annotator 明确记录为开发助手。自动校验保证结构与源代码一致，分组还需从实际语义判断相似问题；当前分组已按业务族保守聚合。

## 指标定义

| 指标 | 计算单位与规则 |
| --- | --- |
| 文件 Hit@K / Recall@K | 先截 K 个 Chunk，再对路径去重；命中任一期望文件 / 命中文件比例 |
| Symbol Hit@K / Recall@K | 精确 `(path, symbol)`；无命名 Symbol 标签的案例为 N/A |
| Evidence Range Recall@K | 同路径多个 Chunk 的行范围联合后，完整覆盖多少个标注范围 |
| Evidence Line Coverage@K | 覆盖多少个唯一 `(path, line)`；重叠标签和重复 Chunk 不重复计数 |
| Evidence MRR@K | 第一个与任一所需范围有行交集的 Chunk 排名倒数；K 内无交集为 0 |
| All-evidence Coverage | 全部必要范围均完整覆盖的案例比例 |
| Context Coverage | 使用同一次实际 ContextBuilder 输出的真实行范围重算范围/逐行/全部证据覆盖 |
| p50 / p95 | 实测服务检索耗时，排序后在 `(n-1)*p` 位置线性插值；包含 Query Embedding 与 Qdrant |

各正例指标采用适用案例的 Macro 平均。MRR 的相关性按必要证据行定义：仅命中同文件 imports 不算相关；MRR=1 也不代表完整覆盖。范围与逐行是不同权重，Macro Line Coverage 不必大于 Macro Range Recall。

负对照的 Recall、MRR、Context 指标为 N/A，另外报告空结果率、返回 Chunk 数和外仓库结果数。Dense 没有弃答阈值，面对不存在的 Symbol 仍可能返回相似代码，这种输出保留为负对照失败。

Context 来自真实 Builder，含边界、元数据、按行去重和截断。本任务止于检索和 Context，没有调用生成模型，因此不宣称这些 Sources 已发送给模型，也不评回答正确性。

## 真实运行结果

本次采用原 Embedding `qwen3-embedding:0.6b`、1024 维 Cosine、AST/fallback、TopK 5、Context 5 Sources / 16000 字符。临时 Collection 共 129 个 Point；历史 17 个 Payload/Vector 从生产索引原样复制，新夹具只在临时 Collection 内索引。

| 指标 @5 | 历史 12 问 | 开发集 32 正例 |
| --- | ---: | ---: |
| 文件 Hit | 91.67% | 100.00% |
| 文件 Macro Recall | 84.72% | 92.71% |
| Symbol Macro Recall | 69.05%，七问 | 76.79%，28 问 |
| 完整范围 Macro Recall | 73.61% | 79.69% |
| 逐行 Macro Coverage | 73.38% | 81.27% |
| Evidence MRR | 0.6042 | 0.5661 |
| 全部证据与 Context 完整覆盖 | 8/12 | 22/32 |
| p50 / p95 检索耗时 | 647.67 / 678.76 ms | 645.11 / 676.64 ms，含全部 40 个开发请求 |

开发集八个负对照：两个空仓库返回空结果；其余六问返回无关代码，空结果率 25%；外仓库结果数为 0。负对照不加入上表正例分母。

共 **52 次真实查询、8 次索引 Embedding Provider 调用、52 次查询 Embedding Provider 调用、零次生成调用**。Provider 调用数不冒充 SDK 网络重试数。完整原始检索、实际 Context、每案指标、配置、标签快照与含 Vector 的临时索引快照均保存。临时 Collection 已删除，两个既有 Collection 和生产 17 个 Point 保持一致。

成功案例：`shipping-error` 在 Rank 1 找到 `ShippingService.dispatch` 的异常行，完整范围、逐行、Symbol 与 MRR 均通过。

真实失败：`wallet-symbol` 命中正式与 legacy 两个文件，但只召回正式 `credit` 方法，完整范围 Recall=1/2、逐行 Coverage=7/10；`ledger-chain` 命中了 Controller/Service 文件的类头、imports、构造函数，三个必要方法的证据覆盖却为 0；`missing-symbol-a` 返回五个无关 Chunk。文件 Hit 不能代替实现证据命中。

## 运行与离线重算

从项目根目录执行，已有 Qdrant 与 Embedding 服务即可：

```powershell
.\infra\Qdrant.ps1 up
# 默认为 regression + dev，输出新时间戳文件
& '.\services\api\.venv\Scripts\python.exe' evaluation/stage4/evaluate.py

# 完全离线重算本次 52 个原始案例
& '.\services\api\.venv\Scripts\python.exe' evaluation/stage4/evaluate.py `
  --verify-only evaluation/reports/stage4_retrieval_20261013_task2.json

& '.\services\api\.venv\Scripts\python.exe' -m pytest evaluation/tests -q
```

`--dataset` 与 `--output` 接收文件路径。已有报告及同名部分收据目录拒绝覆盖；失败保留已完成收据和错误类型，不发布删掉失败案例后的成功汇总。清理不成功或既有索引变化会令整个运行失败。

未来运行 `--split holdout` 需提供之前固定的 `--frozen-config` JSON，至少包含匹配的 `dataset_sha256` 和 `configuration`（报告中的配置）。没有该文件、数据集/配置不匹配都会拒绝。保留集的启用留到配置确定后的消融阶段。

代码接口：`dataset.load_dataset` 负责身份/分组/源标签验证；`metrics.case_metrics` 与 `metrics.summarize` 是纯指标函数；`evaluate.replay` 不访问模型或 Qdrant，逐案核对索引身份、原收据、Context/Citation、定义与汇总。生产 API 无新增字段。

交付：[数据集](../evaluation/datasets/stage4_retrieval.json)、[真实报告](../evaluation/reports/stage4_retrieval_20261013_task2.json)、其 `_artifacts` 目录与 [机器验收](stage-4-task-2-verification.json)。26 个新测试覆盖范围/逐行区别、去重、MRR、分母、Context 截断、延迟、标签错误、跨 split 泄漏及保留集配置约束，评测套件共 107 项通过。

## 概念与下一任务

- 文件、Symbol、完整范围、逐行覆盖和 MRR 回答不同问题，不能互相代替。
- 小保留集用于学习阶段配置决策；它没有在 Task 2 被提前检索，人工夹具与业务族分组也不能冒充生产泛化证据。
- 只在临时索引添加学习代码，可以同时验证跨仓库隔离并保持已冻结的生产 Dense 基线。
- 下一任务为 **Stage 4 / Task 3 内部候选层**，依赖本次固定 Dataset、指标、真实失败和 Task 1 的 Dense 兼容基线。本次未开始 Task 3。
