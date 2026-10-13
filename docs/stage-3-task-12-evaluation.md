# Stage 3 / Task 12：Evaluation + Bad Cases

## 本次完成

完成 2026-10-12 的真实 Stage 3 小评测，保留 Retrieval、Answer、Citation 三类案例及 9 个失败案例。固定 16 条 Query，实际执行 16 个现有 FastAPI 路由请求、15 次生成模型 HTTP 调用；空检索对照不调用生成模型。检索、Context、原始模型回复和最终 Sources 均来自同一次请求，没有模拟模型答案。

原 12 条 Query 的文件 Hit@5 为 **11/12（91.67%）**，标注行范围 Macro Recall@5 为 **73.61%**。逐案对照所选引用后，**5/12 回答受证据支持，其中 3/12 完整回答问题**。全部案例的 46 个 Citation 坐标和预览有效，模型语义质量仍未通过验收。

这里的逐案审阅由开发助手对照真实回复、Context 和源文件完成，不是独立人类评审，也没有新增自动 LLM Judge。标签与审阅文件均明确记录身份；**独立人工复核待用户进行**。这些是曾在开发中观察过的小型手写演示仓库案例，不是未见过的生产基准。

应用实现、模型配置、原评测数据、依赖和索引保持不变。本任务没有修检索、Chunker、Prompt 或模型，也没有开始 Task 13。

## 修改文件

| 文件 | 职责 |
| --- | --- |
| [stage3_grounding.json](D:/aaaagent开发文件/RepoPilot/evaluation/datasets/stage3_grounding.json) | 运行前固定的 16 案例；原 12 条 Query/标签完全复用，补充两个 Issue、空仓库、700 字符 Context |
| [evaluate.py](D:/aaaagent开发文件/RepoPilot/evaluation/stage3/evaluate.py) | 调用现有 FastAPI 路由，观察真实检索/Context/模型 HTTP，计算文件、行范围和 Symbol 指标，保护索引与源文件 |
| [review.py](D:/aaaagent开发文件/RepoPilot/evaluation/stage3/review.py) | 验证完整审阅与原始回复指纹，分别汇总支持、完整性、坐标与所选引用语义支持 |
| [test_grounding_evaluation.py](D:/aaaagent开发文件/RepoPilot/evaluation/tests/test_grounding_evaluation.py) | 新增 19 个离线边界用例，防止文件命中代替方法命中、遗漏失败分母、复用错误审阅等 |
| [raw.json](D:/aaaagent开发文件/RepoPilot/evaluation/reports/stage3_grounding_20261012_task12_raw.json) | 完整真实运行；旁边 `_raw_cases` 目录保存每个请求的独立收据 |
| [review.json](D:/aaaagent开发文件/RepoPilot/evaluation/reports/stage3_grounding_20261012_task12_review.json)、[summary.json](D:/aaaagent开发文件/RepoPilot/evaluation/reports/stage3_grounding_20261012_task12_summary.json) | 逐案显式判定、理由、支持的 Source IDs，以及可重算汇总 |
| [retrieval-cases.json](D:/aaaagent开发文件/RepoPilot/output/stage3-task12/retrieval-cases.json) | 真实 Score / Path / Symbol / Lines / Strategy、标注与 Context 诊断 |
| [answer-cases.json](D:/aaaagent开发文件/RepoPilot/output/stage3-task12/answer-cases.json) | 实际回答、固定期望、Answer Supported / Complete 及逐案理由 |
| [citation-cases.json](D:/aaaagent开发文件/RepoPilot/output/stage3-task12/citation-cases.json) | 实际 Citation、预览行数、坐标重放和单独的语义支持判定 |
| [bad-cases.json](D:/aaaagent开发文件/RepoPilot/output/stage3-task12/bad-cases.json) | 9 个真实失败的分类、原始输出与证据说明 |
| [verify.py](D:/aaaagent开发文件/RepoPilot/output/stage3-task12/verify.py) | 不调用模型的证据重放、原始收据对比、测试收据及工作区/Qdrant 保护核对 |
| [README.md](D:/aaaagent开发文件/RepoPilot/README.md)、[evaluation/README.md](D:/aaaagent开发文件/RepoPilot/evaluation/README.md) | 评测入口、指标含义、运行方式、审阅身份和失败记录 |

本任务的保护清单包含 569 个旧文件，只修改 README 和 evaluation/README 两个预期文档，另 **567 个文件哈希一致**。包括所有现有应用源文件、旧测试、Stage 1/2/3 既有证明、旧评测数据和报告、演示仓库、环境及锁文件。Qdrant 的全部 Collection、Metadata、完整 Payload 和 Vector 快照保持一致；生产索引仍是 **17 Points / 1024 维 / Cosine**。

## 调用链 / 数据流

```text
固定 Dataset + 源文件指纹
  → 独立评测进程中的现有 FastAPI app
  → /api/code-chat 或 /api/issue-analysis（HTTPX ASGITransport）
  → 原 RetrievalService（真实 Query Embedding + Qdrant）
  → 原 ContextBuilder（观察实际输出，默认 16000 字符）
  → 原 LLMProvider（真实 HTTP，qwen2.5:0.5b，temperature 0 / max_tokens 512）
  → 原 CitationValidator 与输出处理
  → 原始路由 Response + 每案收据
  → 坐标/预览/本地行重放 + 检索指标
  → 显式逐案对照代码审阅（非自动 Judge）
  → 汇总 + Retrieval / Answer / Citation 案例 + Bad Cases
```

API 请求通过进程内 ASGI 执行，不是浏览器请求或外部 API TCP 测试；Embedding、Qdrant、模型均是真实网络调用。观察钩子只存在于评测进程中，并在结束时恢复方法、移除 SDK hook、关闭该进程拥有的模型客户端。正常 8000 API 和 5173 页面进程没有被替换。

原 12 条标准案例统一 TopK 5、默认 Context 上限。只有明确标注的补充 `refund-budget700` 案例在独立评测进程中使用 700 字符 Builder；不修改 `.env`。评测不重新扫描/索引、不新建 Collection、不复用假的向量、不追加模型修复请求。

每个完成请求立即写独立 JSON 收据，HTTP 失败也保留。若结构、身份或源文件校验失败，整个评测中止，已有收据仍可诊断。生成失败和有标注问题上的弃答不能从回答分母中删除；本次实际运行没有 HTTP 失败或非空 Context 弃答。

## 我必须理解的知识

1. **文件、Symbol、行范围是不同检索单位。** 输入 TopK 排序 Chunk 与固定标签，输出三种独立指标。K 先截取 Chunk，再去重文件；同一文件的多个碎片会占多个位置。Symbol 按准确 Path + Symbol 匹配；行覆盖要求标注范围的每行都进入 TopK，可合并相邻范围。`refund-controller` 在 Rank 1 找到 controller 文件，却未找到 `refundOrder` 方法，这不是实现证据命中。

2. **检索失败和生成失败要分别归因。** 输入同一请求的检索/Context/回复，逐案判断是目标方法未召回、预算删除证据，还是模型在已有证据上答错。`wallet-default-balance` 在 Rank 1 获得正确方法仍回答“未找到”，是生成问题；`wallets-schema` 的 SQL 文件没进 Top5，是检索不足并伴随错误回答范围。不能把所有问题都归为模型或检索。

3. **Citation Valid 不等于 Answer Supported。** 输入所选 IDs 与真实 Source，后端验证坐标与切片；审阅另外检查这些片段能否支持回答的实质内容。坐标正确的 SQL Citation 可以伴随“字段在 Java 文件定义”的错误答案。回答支持成立也可能不完整；`orderId: string` 是正确局部事实，却漏掉其余两个字段。多余引用单独判定，不能因为其中一条正确就说整批都有语义支持。

4. **Context 是模型实际证据，不等于全部 TopK。** 输入完整检索与有限预算，ContextBuilder 输出实际行片段。真实 700 字符案例用 698 字符，只保留 `RefundService.refund` 的 12–15 行，17 行钱包调用已不可见。引用必须同步缩至 12–15。检索覆盖完整方法，Context 覆盖却是零，不可用原大范围解释答案。

5. **固定标签、分母和审阅身份决定结果是否可比较。** 标签先验证源文件并固定，再调用模型；报告绑定 Dataset、Source 和每个实际 Response 的指纹。12 条标准 Query 与四条补充案例分开统计，空负对照的引用有效性为 N/A；不能将它算成“完美 Citation”。审阅必须覆盖每案且说明理由，旧评分不能套到新推理。由助手完成的对照检查需如实标注，不能伪称独立人类认可。

## 真实数据示例

标准 12 条 Query 来自原 Stage 2 Dataset，Query、期望路径、行号和字面证据没有改变。Source 指纹对应既有 `demo-repository` 的 8 个可索引文件。Supplementary 案例是之前已知的退款 Issue、另一个精确指向 `RefundService.refund` 的 Issue、一个未索引仓库名和正常退款 Query 的 700 字符预算版本。

标准检索指标：

| K | 文件 Hit | 文件 Macro Recall | 完整标注范围 Hit | 标注范围 Macro Recall |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 8/12，66.67% | 66.67% | 6/12，50.00% | 50.00% |
| 3 | 10/12，83.33% | 77.78% | 7/12，58.33% | 58.33% |
| 5 | 11/12，91.67% | 84.72% | 10/12，83.33% | 73.61% |

七条有命名 Symbol 标签的标准 Query：Symbol Hit@5 为 6/7（85.71%），Macro Recall@5 为 69.05%。五条无命名 Symbol 标签的文档、SQL、JSON、Vue 案例不进入该分母。Context 完整覆盖全部所需标注范围的标准案例为 8/12；两个多文件问题只覆盖其中一部分。相同 12 条 Query 的文件与行范围结果与 Task 5 AST 运行一致，未更换检索策略。

逐案结果如下。“支持”表示回答包含与问题相关的实质内容，且这些内容由所选引用片段支持；它不等同于回答完整。纯复述、错误位置和错误范围不算通过。

| Case | 实际回复摘要 | Answer Supported | Complete | Citation 坐标 |
| --- | --- | --- | --- | --- |
| refund-service | `RefundService.refund` | 是 | 是，满足位置问题 | 有效 |
| wallet-credit | 返回真实 `credit` 方法体 | 是，S4 支持 | 是 | 有效，含多余引用 |
| wallet-default-balance | “未找到用户如何返回零” | 否，与 S1 矛盾 | 否 | 有效 |
| refund-controller | `backend/service/RefundService.java` | 否，入口定位错误 | 否 | 有效 |
| refund-form | 原样复述问题 | 否，无实质回答 | 否 | 有效 |
| refund-request-type | `orderId: string` | 是 | 否，漏两个字段 | 有效 |
| orders-schema | 声称字段在 RefundService.java 定义 | 否 | 否 | 有效 |
| wallets-schema | `WalletService.balanceOf(String userId)` | 否，未回答表定义 | 否 | 有效 |
| fixture-documentation | 只列 RefundService.java，引用 JSON | 否，所选片段不支持 | 否 | 有效 |
| repository-metadata | `config/repository.json` | 是，S1 支持 | 是，满足位置问题 | 有效，含多余引用 |
| refund-validation-files | 把校验指向 types.ts 和 Vue | 否 | 否 | 有效 |
| refund-call-chain | 只写 RefundService.java | 是，仅支持这一局部事实 | 否，漏完整链路 | 有效，含多余引用 |
| issue-refund | 声称代码被截断，给泛化建议 | 否 | 否 | 有效 |
| issue-direct | 候选节点是 README，测试建议是代码修改 | 否 | 否 | 有效，链语义不成立 |
| empty-repository | 固定证据不足，无生成调用 | N/A，正确负对照 | 是，控制行为正确 | N/A，无引用 |
| refund-budget700 | 复述问题，S1 仅 12–15 行 | 否，无实质回答 | 否 | 有效，正确截断 |

标准 12 案例：5 个 supported（41.67%）、5 个 unsupported、2 个 nonresponsive；3 个完整回答（25%）。全部有内容标签的 15 案例：5 个 supported（33.33%）、3 个完整回答（20%），另有一个正确空检索对照。报告中 `complete_responses=4` 包含该控制案例，不能解释为四条完整实质答案。

15 批有引用的响应共 46 个 Citation，全部坐标/预览有效；标准 12 批共 39 个。只有两批所选引用全部直接支持输出的实质内容；这与 5 个答案可被至少必要的引用支持是不同判断。其余情况包括多余引用、错引和无法支持错误内容的真实坐标。

真实错误示例：Query 为“读取用户钱包余额时，未找到用户如何返回零？”，S1 是 [WalletService.java](D:/aaaagent开发文件/RepoPilot/workspaces/demo-repository/backend/service/WalletService.java:20) 第 20–22 行：

```java
    public BigDecimal balanceOf(String userId) {
        return balances.getOrDefault(userId, BigDecimal.ZERO);
    }
```

真实模型却回答“未找到用户如何返回零”，并引用 S1。不是假设失败，也不是夹具结果，完整收据见 [03-wallet-default-balance.json](D:/aaaagent开发文件/RepoPilot/evaluation/reports/stage3_grounding_20261012_task12_raw_cases/03-wallet-default-balance.json)。

另一真实 Query 为“订单表的状态字段和金额字段在哪里定义？”。S1 是 [schema.sql](D:/aaaagent开发文件/RepoPilot/workspaces/demo-repository/database/schema.sql:1) 的实际 DDL，模型却答“订单表的状态字段和金额字段都在 `backend/service/RefundService.java` 文件中定义。”。这直接展示坐标正确与回答错误可以同时发生，见 [07-orders-schema.json](D:/aaaagent开发文件/RepoPilot/evaluation/reports/stage3_grounding_20261012_task12_raw_cases/07-orders-schema.json)。

## 验收方法

从项目根目录运行；已有本地 Ollama、模型和 Qdrant 存储即可，不需要启动另一个 API 端口：

```powershell
.\infra\Qdrant.ps1 up
# 如本地服务尚未运行，可同时恢复正常 API、网页和 Ollama
.\Start-RepoPilot.ps1 -NoBrowser

# 新的真实评测，默认产生新时间戳文件，不覆盖历史报告
.\services\api\.venv\Scripts\python.exe evaluation/stage3/evaluate.py

# 现有 42 + 新增 19 个离线评测用例
.\services\api\.venv\Scripts\python.exe -m pytest evaluation/tests -q

# 保存证据重放，读取工作区/索引，仅更新 Task 12 派生验证文件
.\services\api\.venv\Scripts\python.exe output/stage3-task12/verify.py
```

正常页面仍可在 [Code Chat](http://localhost:5173/#code-chat) 或 [Issue Analyzer](http://localhost:5173/#issue-analysis) 输入同一 Query，分别调用 `POST /api/code-chat` 或 `POST /api/issue-analysis`。新请求是新的推理，必须按新回复重新判断；不要用旧评测结论代替新输出检查。

人工复核时，对每案先看固定期望和实际回复，再打开所选 Source 与完整 Context：

1. 判断文件命中是否包含目标方法和所需行，缺的是检索证据还是实际 Context。
2. 逐条核对回答的事实、位置与范围能否由选中的片段支持；记录 Answer Supported 和 Complete，并说明理由。
3. 分别核对 Citation 坐标、预览、语义支持；不要把正确坐标当作整批支持。Issue 的候选调用节点还需核实是否可调用及有无边证据。
4. 在新的审阅 JSON 中记录审阅者身份、当前报告 SHA、每个 Response 指纹和 Source IDs。必须覆盖全部案例，不能删除失败或只挑成功输出。

审阅契约和现有示例见 [review.json](D:/aaaagent开发文件/RepoPilot/evaluation/reports/stage3_grounding_20261012_task12_review.json)。可对当前这份审阅产生新的汇总，输出必须使用新文件名：

```powershell
.\services\api\.venv\Scripts\python.exe evaluation/stage3/review.py `
  --report evaluation/reports/stage3_grounding_20261012_task12_raw.json `
  --review evaluation/reports/stage3_grounding_20261012_task12_review.json `
  --output evaluation/reports/my_stage3_review_summary.json
```

新的真实运行需要对应的新审阅文件，不能执行留证用的 `freeze_review.py` 来给新报告自动套用旧判定。该脚本的判定明确只适用于保存的 Task 12 原始回复指纹，并禁止覆盖文件。

2026-10-12 验证结果：离线评测测试 **61 passed**；16 个原始收据、15 个真实模型 HTTP 回复、动态 Schema 和 Prompt、完整 Context、Source Metadata、预览与当前文件行均已重放核对；汇总可以独立重算。没有重跑旧后端或前端构建，应用源代码未改变，完整 Stage 3 最终测试留给 Task 13。机器记录见 [stage-3-task-12-verification.json](D:/aaaagent开发文件/RepoPilot/docs/stage-3-task-12-verification.json)。

## 当前未实现

- 独立人类审阅结果、生产仓库或真正未见过的测试集。
- 自动语义 Judge、调用图或调用边证明、缺陷成立的自动判断。
- Hybrid Search、BM25、Sparse Retrieval、RRF、Reranker、Query Expansion。
- 针对本次失败的 Chunker、模型、Prompt 或检索修复。
- 多轮对话、Agent Loop、自动改代码或执行仓库测试。
- Task 13 的完整回归、Stage 3 总结文档和最终验收结论。

## Bad Case / 风险

已保存 9 个真实 Bad Cases，至少覆盖以下问题，所有原始回复保留：

- **Dense Retrieval 漏召回目标 Symbol。** `refund-controller` 命中 controller 文件，Top5 却只有 imports 1–5 行和 class 字段 6–8 行，没有 `refundOrder` 13–15 行。目标方法在另一个 Query 的 Rank 3 出现，证明已索引。小 AST 碎片没有独立完成任务所需的实现上下文；本次没有证明放大 Chunk 就能解决排序。
- **完整调用链 Context 不足。** `refund-call-chain` 只覆盖 1/3 标注方法，钱包方法完全未召回，controller 只召回 imports。回答中的一个真实文件名不能冒充三节点链路。
- **生成错答与错引。** `wallet-default-balance` 否认可见的返回零逻辑；`orders-schema` 在正确 SQL Source 面前指向 Java 文件；`fixture-documentation` 引用不包含文件名的 JSON，而未引用相关 README。
- **候选链与建议不成立。** `issue-direct` 把 README 当调用节点，所谓测试建议只是添加代码检查。`issue-refund` 用未经证实的截断说法解释无法分析，实际上完整退款方法已进入 Context。
- **预算造成证据缺失。** `refund-budget700` 的 12–15 行引用正确，缺少 17 行调用。模型仍只复述问题。坐标保护工作正常，答案有用性没有通过。

小仓库中未观察到真实“超大 AST Chunk”失败，不造一个实验结果补齐清单。检索问题和 Chunk 尺寸问题作为后续 Stage 4 调查材料保留，本次没有扩展检索算法。

数据量小、案例已有开发曝光、只测一个仓库与一个小模型，不能外推生产表现。temperature 0 也不保证不同时间的回复完全一致；本次两个 Issue 的文字比 Task 11 有所变化，语义问题仍存在。输出被合法 Schema 接受不代表模型推理合格，助手的审阅也不能代替用户的独立复核。

## 下一任务

Task 13：Stage 3 最终测试与文档。下一次继续时才运行完整后端、前端构建、Stage 2 检索评测和 Stage 3 评测，更新最终 README、配置示例与 `docs/stage-3.md` 并解释核心概念。本次完成 Task 12 评测留证后停止，**未开始 Task 13，未进入 Stage 4**。
