# Stage 4 / Task 1：冻结 Dense 检索基线

Task 1 将紧邻 Stage 3 / Task 13 的真实评测冻结为后续高级检索的对照。输入是新检索报告、同次源代码/索引状态下的 Stage 3 原始回复与显式审阅、以及已完成的 Task 13 功能验收；输出是独立 Baseline JSON 与相邻快照目录。

生产检索仍使用既有 Dense Retrieval。Task 1 不改变模型、Prompt、切块、索引或 API；不新增生产请求参数。完成边界是基线留存与诊断，下一任务是 Stage 4 / Task 2 数据集和指标建设。

## 冻结的身份与配置

| 项目 | 当前冻结值 |
| --- | --- |
| Repository | `demo-repository`，八个有指纹的可索引源文件 |
| Dense / Collection | `qwen3-embedding:0.6b` / `repopilot_code` |
| 索引 | 17 Chunk，1024 维，Cosine |
| Chunker | `ast-v1`；文本 fallback 为 `ast-fallback-line-v1` |
| 行窗口 / 重叠 / 字符限制 | 60 / 10 / 8192；AST Chunk 的实际重叠元数据另保存在原 Payload |
| 官方 TopK | 5；指标取同一结果的 K=1/3/5 前缀 |
| 默认 Context | 最多 5 Sources，16000 Unicode 字符 |
| 生成对照 | 复用 Task 13 的 `qwen2.5:0.5b` 回复，Task 1 零生成调用 |
| 补充诊断 | 对六个 Top5 完整证据不足案例单独查询 Top20，每案一次 Embedding Provider 调用 |

当前目录没有 `.git`，索引也没有原生修订号。`git_revision`、`native_index_revision` 如实记录为 null；`source_snapshot_sha256` 和 `index_snapshot_sha256` 提供内容身份。配置快照使用显式字段白名单，端点仅保存 SHA-256，不保存密钥或端点原文。

完整 Qdrant 快照包括全部既有 Collection 的稳定配置、Metadata、Payload 和 Vector。保存快照用于核对与重放，CLI 不自动恢复或修改索引。`repopilot_infra_smoke` 空集合也保留原状。

## 数据流、接口与运行

```text
Task 13 功能验收（绑定新报告指纹）
  → 核对源文件、Dataset、配置、运行时和完整索引
  → 重放 12 条 HTTP Top5 + 16 条同请求 Retrieval / Context / Citation
  → 核对新审阅与逐案收据
  → 六次独立 Top20 检索诊断
  → 再次核对源文件、配置、报告及索引
  → 发布 Baseline JSON、快照、Chunk 清单与 Bad Case
```

从项目根目录执行；需要已有 Qdrant 与 Embedding 服务，生成服务不会被 Task 1 调用：

```powershell
& '.\services\api\.venv\Scripts\python.exe' evaluation/stage4/freeze_baseline.py `
  --retrieval-report evaluation/reports/stage3_retrieval_20261013_task13.json `
  --grounding-report evaluation/reports/stage3_grounding_20261013_task13_raw.json `
  --review-report evaluation/reports/stage3_grounding_20261013_task13_review.json `
  --acceptance docs/stage-3-task-13-verification.json `
  --output evaluation/reports/my-new-dense-baseline.json
```

本次实际输出为 [stage4_dense_20261013_task1.json](../evaluation/reports/stage4_dense_20261013_task1.json)。原始两批 Top5 都保留：`http_cases` 的 Context 标为 `rebuilt_context`，是纯 Builder 重建；`grounding_cases` 中的 Context 来自实际发送给模型的同一次请求。两者分别统计，不把重建 Context 冒充已发送的证据。

相邻 `stage4_dense_20261013_task1_artifacts/` 包含：

- `index-snapshot.json`：全部原配置、Metadata、完整 Payload/Vector。
- `configuration.json`：允许输出的设置、依赖版本、锁文件与关键实现指纹。
- `chunks.json`：有原内容、Path、Symbol、行范围及内容身份的 17 个 Chunk。
- `top20/*.json`：每案请求、真实响应、原 Top5 身份、前缀是否一致、独立耗时和调用数。
- `bad-cases.json`：绑定具体证据范围、Chunk IDs、Source IDs 或回复指纹的错误分类。

任一已有输出或同名部分诊断目录都拒绝覆盖。失败留下已经完成的诊断收据，不发布完整 Baseline；修复后使用新输出名。源文件、报告、配置、Chunker 身份或索引漂移也会中止，不自动重建索引。

完全离线验证本次留存，不访问模型或 Qdrant：

```powershell
& '.\services\api\.venv\Scripts\python.exe' evaluation/stage4/freeze_baseline.py `
  --verify-only --output evaluation/reports/stage4_dense_20261013_task1.json
& '.\services\api\.venv\Scripts\python.exe' -m pytest evaluation/tests -q
```

离线验证读取当前相同源文件，核对输入/快照指纹，重建 Context/Citation 并重新计算全部指标和分类。源文件已改变时应使用对应冻结代码副本进行重放，不能把新代码视为旧坐标。

## 基线结果与 Bad Case

| 标准 12 问指标 | K=1 | K=3 | K=5 |
| --- | ---: | ---: | ---: |
| 文件 Hit | 8/12 | 10/12 | 11/12 |
| 文件 Macro Recall | 66.67% | 77.78% | 84.72% |
| 完整标注范围 Macro Recall | 50.00% | 58.33% | 73.61% |
| Symbol Macro Recall，七问 | 42.86% | 42.86% | 69.05% |

实际 Context 覆盖全部证据为 8/12；答案支持为 5/12，完整实质回答为 3/12。标准 12 问与四个补充案例分开保存；空仓库的 Symbol、范围及 Citation 准确率为 N/A。补充 Top20 不修改这些分母、Top5、Context 或回答判定。

Bad Case 允许同时属于多个类别：

| 类别 | 判定证据与实际案例 |
| --- | --- |
| 索引或召回缺失 | 核对完整索引是否覆盖目标范围，再检查诊断 Top20；区分未索引与诊断预算内未召回，不把无法定位的原因说成已确认的排序问题 |
| 排序靠后 | 原 Top5 缺少所需范围，而索引与独立 Top20 中存在；重点是 `refund-controller`、`wallets-schema` 及多文件链路 |
| Context 缺失 | 原 Top5 完整覆盖某标注范围，而真实 Context 不完整；700 字符案例的 refund 12–18 行变为 12–15 行 |
| 生成错误 | 显式审阅指出错答、复述、受控生成失败，或在完整证据可见时遗漏要求；包括返回零、SQL 位置、类型字段和 Issue 分析 |

成功案例是准确定位 `RefundService.refund`；需要关注的失败是 Controller 文件命中但入口方法不在 Top5。同文件 imports/类字段占多个位置的现象保留在原排名中，Task 1 不去重排名或调整选择策略。

原问题的独立 Top20 与原 Top5 前缀可能不同；每案保存该标志和实际排名。它提供诊断观察，不回写原结果，也不证明某个生成错误只有单一原因。逐案详情与真实排名以本次报告为准。

本次六个诊断均返回同一仓库的全部 17 个 Chunk，前五个 Chunk 身份与原结果一致。目标实现已经索引，真实后续排名为：

| 案例 | Top20 中原 Top5 缺失的必要证据 |
| --- | --- |
| `refund-controller` | `OrderController.refundOrder` 第 9 位 |
| `wallets-schema` | `schema.sql` 第 6 位 |
| `refund-validation-files` | `WalletService.credit` 第 17 位 |
| `refund-call-chain` | `OrderController.refundOrder` 第 7 位，`WalletService.credit` 第 15 位 |
| `issue-refund` | `OrderController.refundOrder` 第 8 位，`WalletService.credit` 第 14 位 |
| `issue-direct` | `OrderController.refundOrder` 第 6 位，`WalletService.credit` 第 12 位 |

分类表包含 12 个有失败观察的案例：9 条标注范围排序观察、1 条 Context 缺失观察、11 条生成问题观察；类别可以重叠。这次未观察到索引缺失，记录零例，不构造失败补齐分类。六次额外 Embedding 调用、零次生成调用均独立计数，完整源文件、配置与索引核对通过。

延迟保留每问的原 HTTP 总耗时、服务检索耗时，以及每次诊断的独立检索耗时。Task 1 的额外调用只包含诊断 Embedding；不将新生成耗时或并行测试耗时混入原检索指标。

## 验收、概念与下一任务

20 个新测试覆盖报告关联、未完成验收、源文件/配置/索引漂移、旧文件覆盖拒绝、路径限定的 Symbol 分母、完整范围联合覆盖、Context 截断的真实坐标，以及诊断不能替换 Top5。最终评测套件 81 项通过；真实基线及离线重放另由 [机器记录](stage-4-task-1-verification.json) 留证。

Task 1 必须理解：

1. **文件、Symbol、完整范围是三个不同单位。** K 先截取 Chunk，再计算相应唯一目标；本任务的范围召回要求全部标注行被联合覆盖，逐行覆盖率留给 Task 2。
2. **候选数量、TopK 和 Context 预算分别起作用。** Top20 是诊断请求，当前没有新增内部候选层；生产默认仍为 Top5，实际 Context 还可能更少。
3. **身份指纹约束比较条件。** 同样的模型名不足以确认同样的索引；源代码、模型端点身份、参数、Payload/Vector 和实现也需固定。
4. **证据与答案分层审阅。** 完整 Source 不保证模型读对；正确 Citation 坐标也不保证语义支持。助手审阅身份明确，不作为独立人类认可。
5. **冻结对照不要求推理逐字相同。** 新回答逐案审阅，分数/排名漂移记录；不调标签或选择性重试来复制历史结果。

下一任务依赖本次 Baseline、旧 12 问回归集、Bad Case 与固定代码/索引身份。Stage 4 / Task 2 再建立新的开发/保留集与指标；BM25、Exact、Hybrid、Rerank、Expansion 和产品接入尚未开始。
