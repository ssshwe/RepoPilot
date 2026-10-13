# Stage 3 / Task 5：Retrieval Debug 与 Simple / AST 对比

验收日期：2026-10-07，Asia/Shanghai。仅完成 Task 5；未进入 Task 6。

## 本次完成

已接通 Qdrant Payload → RetrievalResult → 前端响应校验 → Retrieval Debug。结果卡显示 **Score、Path、Symbol、Node Type、Lines、Chunking Strategy**，并显示 Parent Symbol 与 Chunking Version。代码保持原始空白，通过 Vue 文本插值展示；没有把源码作为 HTML 执行。

完整的 Stage 2 旧 Payload 缺少全部五个 chunking 字段时，返回 `simple / line-window-v1`，三个 AST 名称为 null。Stage 3 Payload 必须包含全部五个字段，策略、版本、Node Type、Symbol 与 Parent 必须一致。缺字段、伪造 text Chunk 的 AST 名称、错误所属关系等情况返回受控 502，整个响应失败，禁止返回部分源码。

真实对比使用原 12 条 Query 和标注，在两个 UUID 命名的临时 Collection 中分别重新生成 **8 个 Simple Chunk、17 个 AST/fallback Chunk**。模型仍为本地 `qwen3-embedding:0.6b`，1024 维，Cosine，TopK=5。每条 Query 只调用一次真实 Provider，然后将同一个 Query Vector 交给两个现有 RetrievalService。

**AST 改善了部分方法定位，但没有全面提高召回。** 保留原指标及所有排名，同时补充“标注行范围覆盖”指标，揭示命中文件却拿不到目标方法的情况。没有调整 Query、标注、源码、检索排序或模型配置。

| 文件级指标 | Simple | AST-aware |
| --- | ---: | ---: |
| Hit@1 | 6/12，50.00% | 8/12，66.67% |
| Macro Recall@1 | 50.00% | 66.67% |
| Hit@3 | 10/12，83.33% | 10/12，83.33% |
| Macro Recall@3 | 77.78% | 77.78% |
| Hit@5 | 12/12，100.00% | 11/12，91.67% |
| Macro Recall@5 | 93.06% | 84.72% |

| 补充指标：完整覆盖标注行范围 | Simple | AST-aware |
| --- | ---: | ---: |
| Evidence Hit@1 | 50.00% | 50.00% |
| Evidence Macro Recall@1 | 50.00% | 50.00% |
| Evidence Hit@3 | 83.33% | 58.33% |
| Evidence Macro Recall@3 | 77.78% | 58.33% |
| Evidence Hit@5 | 100.00% | 83.33% |
| Evidence Macro Recall@5 | 93.06% | 73.61% |

真实完整报告：[stage3_chunking_20261007_task5.json](../evaluation/reports/stage3_chunking_20261007_task5.json)。报告保存每条 Query 的五个结果、原始分数、真实元数据、标注、两类指标、共享 Query Vector 的 SHA-256 和索引保护核验；不保存完整源码和向量。历史 Stage 2 报告、原评测数据集保持不变。

## 修改文件

| 文件 | 职责 |
| --- | --- |
| [schemas/retrieval.py](../services/api/app/schemas/retrieval.py) | 扩展检索结果，兼容完整旧 Payload，拒绝不完整或矛盾的新元数据 |
| [retrieval/vector_store.py](../services/api/app/retrieval/vector_store.py) | Dense Query 的 Payload 白名单增加五个字段，过滤和排名保持 |
| [retrieval/service.py](../services/api/app/retrieval/service.py) | 投影五个字段，复用结果校验与安全错误边界 |
| [types/retrieval.ts](../apps/web/src/types/retrieval.ts) | TypeScript 结果类型 |
| [api/retrieval-contract.ts](../apps/web/src/api/retrieval-contract.ts) | 校验完整元数据及所属关系，明确归一化旧响应 |
| [RetrievalDebug.vue](../apps/web/src/components/RetrievalDebug.vue) | 展示新增字段和空 Symbol 的实际含义 |
| [style.css](../apps/web/src/style.css) | 元数据网格、自适应换行 |
| [App.vue](../apps/web/src/App.vue) | 版本说明更新为 Stage 3 AST Retrieval |
| [test_retrieval.py](../services/api/tests/test_retrieval.py) | 真实内存 Qdrant 混合旧/AST/module/fallback、错误元数据、API 502 和只读校验 |
| [test_retrieval_integration.py](../services/api/tests/test_retrieval_integration.py) | 修正 Task 4 factory 改名后的 opt-in patch；实际 Simple/AST 两个参数化场景 |
| [retrieval-contract.test.mjs](../apps/web/tests/retrieval-contract.test.mjs) | 前端元数据保留、旧数据兼容、拒绝不完整或矛盾数据 |
| [compare_chunking.py](../evaluation/retrieval/compare_chunking.py) | 临时双索引、真实共享 Query Embedding、指标、数据保护与清理 |
| [test_chunking_comparison.py](../evaluation/tests/test_chunking_comparison.py) | 行覆盖、Chunk 截断、共享向量、失败时客户端/集合清理 |
| [evaluation/README.md](../evaluation/README.md) | 对比运行方式和指标解释 |

新增本验收文档、[verification.json](stage-3-task-5-verification.json)、实际对比报告及 `output/stage3-task5` 证据。未增加依赖。

## 调用链 / 数据流

生产查询：

```text
Retrieval Debug：Repository / Query / TopK
→ POST /api/retrieval/search
→ 验证 Collection 的模型身份
→ Query Embedding
→ Qdrant Dense Query（Repository Filter 在 TopK 前）
→ 原排名、Score、Payload 白名单
→ RetrievalResult：源码行范围 + 五个 chunking 字段校验
→ JSON
→ parseSearchResponse
→ 结果卡 + Code Preview
```

对比实验：

```text
同一 Scanner / 真实源码 / 固定预算
├→ Simple Chunker → 8 个真实 Embedding → 临时 Simple Collection
└→ AST Chunker    → 17 个真实 Embedding → 临时 AST Collection

每条原 Query → 一次真实 Query Embedding
├→ 相同 RetrievalService → Simple Top5
└→ 相同 RetrievalService → AST Top5
→ 验证返回内容与源码逐行一致
→ 原文件级 Hit / Recall + 补充 Evidence 行覆盖
→ 验证搜索只读 → 删除两个临时集合 → 核验原索引未变 → 保存新报告
```

对比直接调用 Service，未伪装成两次 HTTP 请求。生产接口另以真实 Uvicorn HTTP 请求验证。对比复用已经生成的 Query Vector，不比较端到端延迟。

## 我必须理解的知识

1. **Dense Retrieval 与 Score**：`retrieval/service.py` 输入 Query 和仓库名，输出按 Cosine 相似度排序的 Chunk。Score 来自 Qdrant，前端仅格式化为六位小数；它不是正确率，也不代表代码包含答案。实验不改变检索机制。
2. **Metadata 传递与完整性**：`vector_store.py` 从 Point 取白名单字段，`schemas/retrieval.py` 将其变成受校验的 RetrievalResult，前端 `retrieval-contract.ts` 再校验一次。输入是数据库 Payload，输出是可信格式的来源记录；这不等于已验证回答或 Citation。
3. **Symbol / Node Type / Strategy**：`ast-v1` 的方法 Chunk 可以有 `RefundService.refund / method_declaration`；同文件 imports Chunk 则可以是 `symbol=null / program / ast`。Vue fallback 是 `null / null / fallback`。`RetrievalDebug.vue` 展示真实值，不把空 Symbol 补成类名。新版本上线需要同步更新后端和前端支持的策略/版本组合。
4. **公平对比**：`compare_chunking.py` 输入同一源码和原数据集，输出两套 Top5 与指标。模型、维度、源码、预算、Query Vector 和 TopK 一致；Chunk 数和边界是实验变量。TopK 指 Chunk，不指五个不同文件；同一文件的多个 AST Chunk 会占多个名次。
5. **文件命中与证据覆盖**：既有 `evaluate.py` 在 TopK Chunk 截断后去重路径，计算文件 Hit / Macro Recall。新增 `evidence_metrics` 检查 TopK 中同路径行范围的并集是否完整覆盖原标注范围。它会识别“Controller 文件命中但目标方法未返回”，仍不能证明整个调用链或最终回答正确。

## 真实数据示例

对比报告中 `refund-service` 的 Query：

> 退款服务在哪里校验订单编号，然后把退款金额转回钱包？

| 策略 | 排名 | Score | Path | Symbol | Node Type | Lines |
| --- | ---: | ---: | --- | --- | --- | --- |
| simple | 1 | 0.6155841 | README.md | null | null | 1–21 |
| simple | 2 | 0.61390245 | backend/service/RefundService.java | null | null | 1–19 |
| ast | 1 | 0.6297276 | backend/service/RefundService.java | RefundService.refund | method_declaration | 12–18 |

AST 第 1 名 ID 为 `66b8fac4-539e-5bd2-802f-2f88b7965a2a`，Parent 为 `RefundService`，版本为 `ast-v1`。实际内容由源码 12–18 行复原：

```java
    public void refund(String orderId, String userId, BigDecimal amount) {
        if (orderId == null || orderId.isBlank()) {
            throw new IllegalArgumentException("Order ID is required");
        }
        // Fixture workflow: credit the refunded amount to the user's wallet.
        wallets.credit(userId, amount);
    }
```

生产索引的另一次真实 HTTP 请求，同一 Query、TopK=1 返回上述 ID、Symbol、行范围，Score 为 **0.62815964**，1024 维；原响应见 [ast-http-response.json](../output/stage3-task5/ast-http-response.json)。这是另一轮检索，使用保留的 Task 4 索引；不能拿它的分数混入新建双索引的对比报告。

Vue Query：

> 退款表单如何收集订单编号、用户编号和金额，并触发 refund 事件？

生产 HTTP 第 1 名为 `frontend/src/RefundPanel.vue`，Score **0.6801285**，Lines **1–22**，Symbol/Node Type/Parent 为 null，Strategy **fallback**，Version **ast-fallback-line-v1**。原响应见 [fallback-http-response.json](../output/stage3-task5/fallback-http-response.json)。新建双索引对比中该 Vue 内容相同，两者 Score 同为 **0.6797179**，仅 ID 和切块标记不同。

浏览器使用真实前端、真实 API 和保留的 17 Point 索引，未 mock 网络。488×668 的现有侧栏窗口下，页面及卡片无横向溢出；AST 方法、无 Symbol 的 imports、Vue fallback 均能查看。代码预览中 `<script setup>` 作为文本显示。截图与 DOM 见 `output/stage3-task5/ui-*`；浏览器无 warn/error。

## 验收方法

运行当前配置的服务：

```powershell
.\infra\Qdrant.ps1 up
.\Start-RepoPilot.ps1 -NoBrowser
```

打开前端 `#retrieval`，Repository 输入 `demo-repository`，TopK=5，使用上面退款 Query。预期能看到 `RefundService.refund / method_declaration / Lines 12–18 / ast / ast-v1`。使用 Vue Query，预期有 `fallback`，且没有伪造 Symbol。排名和分数可能随实际 Provider 输出变化；校验结构和来源。

接口仍为 `POST /api/retrieval/search`，请求示例：

```json
{
  "repository": "demo-repository",
  "query": "退款服务在哪里校验订单编号，然后把退款金额转回钱包？",
  "top_k": 5
}
```

重跑对比，不覆盖已保存报告：

```powershell
& '.\services\api\.venv\Scripts\python.exe' '.\evaluation\retrieval\compare_chunking.py'
```

自动创建、使用并删除本次 UUID 临时集合；原 `repopilot_code` 不重建。所有原集合的完整向量、Payload、数量及 metadata 在实验前后核验。并发写入原索引会使保护核验失败，因此验收时不要同时重新索引。

单测与真实集成：

```powershell
Push-Location services/api
& '.\.venv\Scripts\python.exe' -m pytest -q
$env:REPOPILOT_RETRIEVAL_INTEGRATION = '1'
& '.\.venv\Scripts\python.exe' -m pytest tests/test_retrieval_integration.py -q
Remove-Item Env:REPOPILOT_RETRIEVAL_INTEGRATION
Pop-Location
& '.\services\api\.venv\Scripts\python.exe' -m pytest evaluation/tests -q
Push-Location apps/web
npm test
npm run build
Pop-Location
```

| 本次实际验证 | 结果 |
| --- | --- |
| 完整后端 | 649 collected；643 passed，6 skipped，2 warnings |
| 外部真实检索集成 | 2 passed，32.46 秒；Simple/AST，两个仓库、TopK=1/5/20、只读校验、临时集合清理 |
| 原评测单测 | 32 passed |
| 新对比单测 | 10 passed，包括初始化失败关闭客户端、失败时尝试清理两个临时集合 |
| 前端契约 | 9 passed |
| TypeScript + Vite | 构建通过，1615 个模块 |
| 真实双索引对比 | 12 条 Query、24 次 Dense Search、一次真实 Query Embedding/Query |
| 生产 HTTP | AST 与 Vue fallback 各一个 TopK=1 请求，均 200，源码逐行一致 |
| 实际 UI | AST 与 fallback 展示、代码预览、无横向溢出、无浏览器警告/错误 |

完整后端的跳过项包括外部集成开关和既有 Windows 链接权限用例；本次两个检索参数化用例随后单独 opt-in 通过。两个依赖弃用警告仍为既有警告。

160 个既有文件纳入保护核验；仅 12 个授权文件修改。`.env`、依赖/锁文件、Demo 源码、原数据集、历史报告和先前 Task 文档不变。Qdrant 最终只保留原两个集合；`repopilot_code` 仍为 17 个 Point，原始 HTTP 全 Point 指纹仍为 `af643856c5172b9ff6225e64d156c4a646e936adbed81fcf80cfb00d11f43e98`。本次临时 API 8015 与 Vite 5175 在验收后停止，Qdrant/本地模型服务保持。

## 当前未实现

未实现 Context Builder、Source ID、Context Budget/去重、RAG Prompt、Code Chat、Grounded Answer、Citation Validator 和 Issue Analyzer。没有 BM25、Hybrid、Reranker、Query Expansion 或 Agent Loop。没有为对比新增生产 API 的 Collection/Chunker 选择参数。

## Bad Case / 风险

- **wallets-schema**：Simple 在第 4 名返回 `database/schema.sql:1–11`，Score `0.34237885`。AST Top5 没有 SQL；Wallet 的类声明和方法各占一个名次。文件 Recall@5 从 1 降为 0。
- **refund-controller**：AST 第 1 名是 `OrderController.java:1–5` 的 imports，Score `0.6572932`；第 3 名是类声明 6–8 行。目标 `refundOrder:13–15` 未进入 Top5。因此文件命中为真，标注行覆盖为假。不能据此直接生成“已找到处理方法”的回答。
- **refund-call-chain**：AST Top5 找到 Controller 路径及 RefundService，但没有 Controller 目标方法，也没有 WalletService。文件 Recall@5=2/3，Evidence Recall@5=1/3。仍不能证明完整调用链。
- **refund-validation-files**：Simple 和 AST 均漏掉 WalletService，Recall@5 均为 1/2。
- 仅八个文件、十二条手工标注 Query；Simple 通常一文件一个 Chunk，AST 变成十七个候选，Top5 的覆盖率和竞争关系随之改变。实验结论适用于此固定 Demo，不能外推生产仓库。
- 当前 AST 不将父类或 imports 人工拼入方法文本，短声明可能排在业务方法之前。保留这个结果；本次没有通过改标注、合并结果、Rerank 或增加 TopK 修饰指标。
- 调试卡显示的是来源元数据，不是已验证 Citation。新字段格式校验不替代 Context/Citation/回答依据校验。
- 对比需要读取原集合的向量以做完整保护指纹；在大集合上会有时间与内存成本。失败清理若未被 Qdrant 确认，CLI 不保存 completed 报告，并给出仅本次临时集合的名称用于恢复。

## 下一任务

**Task 6：Context Builder**。将 TopK 结果去重、保持排序、执行预算、分配后端 Source ID 并格式化上下文；测试 Source ID、Metadata、仓库隔离和安全截断。暂不调用 LLM。本次到 Task 5 为止。
