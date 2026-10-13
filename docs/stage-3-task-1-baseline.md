# Stage 3 / Task 1：Stage 2 基线审查

审查日期：2026-10-04，Asia/Shanghai。只完成 Task 1；未进入 Task 2。

## 本次完成

结论：现有 Stage 2 Dense Retrieval 基线可直接作为 Stage 3 的起点。没有发现让当前样例测试或检索失效的阻塞故障。Stage 3 应增量添加 Parser、AST Chunker、Context Builder 和 Citation Validator，复用现有 Embedding、Qdrant、仓库过滤、索引替换和 LLM Provider。

已阅读 README、Stage 1 文档、Stage 2 开发与验收文档、评测说明及相关实现。使用已安装的 `.venv`、`node_modules`、Ollama 模型和 Qdrant 镜像完成验证；没有安装、更新或下载依赖。

### 工作区与 Git

实际目录是 `D:\aaaagent开发文件\RepoPilot`。根目录和工作区内未发现 `AGENTS.md`；检查的上级 `D:\aaaagent开发文件`、`D:\` 也没有该文件。

`git status --short` 实际退出码为 **128**：

```text
fatal: not a git repository (or any of the parent directories): .git
```

根目录没有 `.git` 文件或目录，因此不能声称工作区干净、判断哪些文件已提交，或生成可信的 Git diff。本次没有初始化 Git。为核实本次审查没有改动已有文件，执行前保存 138 个已有文件的 SHA-256，执行后逐一比较：**0 个改变**，包括后端/前端源码、测试、依赖声明与锁文件、旧文档、样例仓库、评测数据、历史报告和 `.env`。构建产物、缓存、新日志和新报告另行生成；这项比较不能替代 Git 历史。

```text
RepoPilot/
├── apps/web/                  Vue 3 + Pinia + Element Plus；LLM / Retrieval 两个工作区
├── services/api/app/
│   ├── api/ + schemas/        FastAPI 路由和请求/响应契约
│   ├── llm/                  Stage 1 通用、流式、结构化、工具调用接口
│   ├── agent/ + tools/       Stage 1 固定信息工具演示
│   ├── repository/           安全扫描、语言识别、忽略规则
│   ├── indexing/             CodeChunk、SimpleCodeChunker、RepositoryIndexer
│   ├── embedding/            独立 Embedding Provider、校验、身份指纹
│   └── retrieval/            Dense Retrieval 与 Qdrant 适配
├── services/api/tests/        后端回归和显式启用的外部服务集成测试
├── evaluation/               冻结的 12 条检索题、运行器、单元测试、报告
├── infra/                    Qdrant Compose / Windows-WSL 启动脚本
├── workspaces/demo-repository/ 手写的真实本地样例文件
├── docs/                     Stage 1 / Stage 2 文档与历史验证记录
└── output/stage3-task1/        本次日志与只读审查快照
```

### 实际运行结果

| 检查 | 本次结果 |
| --- | --- |
| Backend Tests | 471 collected；**466 passed、5 skipped、2 warnings**；8.57 秒；退出码 0 |
| Frontend Build | **通过** `vue-tsc -b && vite build`；1615 个模块；退出码 0 |
| Frontend Contracts | **7 passed**；退出码 0 |
| Evaluation Unit Tests | **32 passed**；退出码 0 |
| Stage 2 Retrieval Evaluation | **12/12** 请求完成，每次返回 5 个真实 Chunk；退出码 0 |
| `/health`、`/health/qdrant`、集合信息 | 全部 HTTP 200；代码集合存在，状态 green |
| Metadata 审查 | **8/8** Point 与对应本地源码切片和重新计算的 Simple Chunk 一致 |
| 数据保留 | 集合名称、向量、Payload、集合配置/模型身份运行前后相同；无 Qdrant 写操作 |

5 个跳过项包括 4 个外部服务集成测试和 1 个 Windows 文件符号链接权限测试（WinError 1314）。本次显式禁用外部服务集成测试，因为其中部分会创建、删除测试集合，而本 Task 要求不修改 Qdrant 数据。真实检索由现有 API 和评测运行器验证。2 个警告分别涉及 Starlette TestClient/httpx 和 anyio BlockingPortal 弃用；没有为消除警告升级依赖。本次未执行新的浏览器视觉验收，不将历史截图当成本次浏览器结果。

启动检查时 API 与 Qdrant 不可连接；随后通过已存在的启动脚本恢复服务。先确认已缓存的 Qdrant v1.19.1 镜像和既有容器，再启动；没有创建新集合、重新索引或修改样例源文件。API、Web、Qdrant 和 Ollama 保持运行，便于继续手工检查。

| K | Hit@K | Macro Recall@K |
| ---: | ---: | ---: |
| 1 | 6/12 = **50.00%** | **50.00%** |
| 3 | 10/12 = **83.33%** | **77.78%** |
| 5 | 12/12 = **100.00%** | **93.06%** |

这组指标及每条题的返回路径顺序与 Stage 2 Task 10 报告相同。逐条从排名路径独立重算 Hit/Recall 和汇总值，结果一致；没有改标签、Query、TopK、模型或排序。每题取前 K 个 **Chunk 后**才对文件路径去重。12 条题来自仅 8 个向量的手写样例，并非生产仓库性能结论。

真实新报告：[Stage 2 本次评测](../evaluation/reports/stage2_retrieval_20261004T145400661913Z.json)。完整证据：[Task 1 验证记录](stage-3-task-1-verification.json)。

## 修改文件

**没有修改现有产品代码、README、依赖或配置。** 本次仅新增审查和验证产物：

| 文件 | 职责 |
| --- | --- |
| 本文件 | 基线结论、复用点、修改面、概念、风险和后续计划 |
| [stage-3-task-1-verification.json](stage-3-task-1-verification.json) | 实际测试结果、集合结构、逐 Point 校验、保留检查与真实 Bad Case |
| [新检索评测报告](../evaluation/reports/stage2_retrieval_20261004T145400661913Z.json) | 保留本次 12 条真实请求的排名、分数、延迟和指标 |
| `output/stage3-task1/` | 测试/构建/评测日志，源码比较清单和 Qdrant 前后快照；不复制完整向量或凭证 |

### 可直接复用的模块

| 模块 | 复用方式 |
| --- | --- |
| [repository/scanner.py](../services/api/app/repository/scanner.py)、[paths.py](../services/api/app/repository/paths.py) | 扫描、资源预算、工作区边界、符号链接/重解析点检查保持；不每次问答扫描全仓库 |
| [repository/language.py](../services/api/app/repository/language.py) | 现有扩展名识别接 Parser Registry；未知/文本语言进入 Fallback |
| [embedding/](../services/api/app/embedding/) | 独立模型配置、批量请求、返回顺序、有限/非零向量与维度校验保持 |
| [indexing/chunker.py](../services/api/app/indexing/chunker.py) | 保留 Simple Chunker，作为 Stage 2 基线和 Parser 降级实现 |
| [indexing/service.py](../services/api/app/indexing/service.py) | 复用先准备/校验再替换单仓库、批量写入、最终计数和进程锁 |
| [retrieval/vector_store.py](../services/api/app/retrieval/vector_store.py) | 保持 Cosine、必须的 repository Filter、TopK 限制、身份校验和只读搜索 |
| [retrieval/service.py](../services/api/app/retrieval/service.py) | 保持查询向量化、排名和拒绝非法/跨仓库结果；只扩展元数据投影 |
| [llm/base.py](../services/api/app/llm/base.py)、[compatible_provider.py](../services/api/app/llm/compatible_provider.py) | RAG/Issue 使用现有 `complete_structured()` 做单次生成；继续本地 Pydantic 验证 |
| [RetrievalDebug.vue](../apps/web/src/components/RetrievalDebug.vue) 及 retrieval API/store/types | 保持原界面、请求状态和转义代码预览，增加 Symbol 等字段 |
| [Stage 2 Dataset](../evaluation/datasets/stage2_retrieval.json)、[evaluate.py](../evaluation/retrieval/evaluate.py) | 固定标签和指标不变，用相同题比较 Simple/AST；另加 Stage 3 答案和引用评测 |

### 引入 Tree-sitter 后的最小修改面（尚未执行）

1. 新建小型 `app/parsing/`，负责 Registry、Parser 选择、节点类型、Symbol 和源码位置；先用 Java 实测，再扩展 Python/JavaScript/TypeScript。样例目前含 Java、TypeScript、Vue，未包含 Python/JavaScript 文件，后两者需要独立语言样例验证。
2. 新建 `indexing/ast_chunker.py`，不改写 Simple Chunker。扩展 [CodeChunk](../services/api/app/indexing/models.py)，追加 `symbol`、`node_type`、`parent_symbol`、`chunking_strategy`、`chunking_version`。超大类拆声明/方法；超大方法在方法内部继续切片，保留 Symbol；解析失败/不支持语言逐文件降级并统计原因。
3. 增量调整 Chunker factory、[prepare_repository](../services/api/app/indexing/service.py) 与 [api/indexing.py](../services/api/app/api/indexing.py) 的类型/选用方式、预算和统计。`asdict(chunk)` 已能将新增数据写入 Payload；仍需显式检查索引响应和调试 Schema。
4. 扩展 [Qdrant 查询字段白名单](../services/api/app/retrieval/vector_store.py)、[retrieval/service.py](../services/api/app/retrieval/service.py)、[schemas/retrieval.py](../services/api/app/schemas/retrieval.py)。只在 Payload 追加 Symbol 不够：目前 `with_payload=[...]` 会把新字段过滤掉。
5. 同步扩展前端 [types/retrieval.ts](../apps/web/src/types/retrieval.ts)、[retrieval-contract.ts](../apps/web/src/api/retrieval-contract.ts) 和 Retrieval Debug，避免接口校验/字段投影丢弃新增字段。
6. 新增 `app/rag/`：Context Builder、独立 Prompt、固定单次生成服务、Citation Validator；新增 Code Chat/Issue 的 Router/Schema/前端。现有通用聊天、流式和 Stage 1 工具演示保持独立。
7. 在 [config.py](../services/api/app/core/config.py)/`.env.example` 后续增加 Parser/Chunking 和 Context 预算设置；补充对应离线测试、Stage 3 评测和文档，不引入 LangChain 或检索框架重写。

版本设计需要区分三件事：现有 Chunk ID 输入版本 **`line-window-v1`**，现有嵌入身份 Schema **`code-points-v1`**，后续可见的 **`chunking_version`**。现在 Payload 没有可见的 Chunking Version；不应无意义更改 Embedding Schema 后导致现有集合被拒绝。为 Simple 保持原 ID，AST ID 在 UUID v5 输入中增加 Symbol/范围/内容指纹和 AST 版本。AST 上线后必须重新生成 Chunk、Embedding 和 Payload，并通过已有单仓库替换去掉旧块。

后续对比实验建议保留 `repopilot_code` 的 Simple 基线，并在 Task 4 明确选择独立 AST 集合，运行时切换集合设置而不覆盖 `.env`。使用同一源码、Embedding 身份、题集、TopK 和预算；保留两份报告，记录统计与 Bad Case。该建议本次没有执行，也没有创建 AST 集合。

## 调用链 / 数据流

当前索引期：

```text
POST /api/indexing/repositories/{repository}
→ 仓库名校验 → Indexing Guard
→ RepositoryScanner → RepositoryFile
→ SimpleCodeChunker → CodeChunk
→ EmbeddingProvider 批量向量化 → 向量/模型身份校验
→ Qdrant 仅删除该 repository 的旧点 → 批量 Upsert → 最终计数
```

当前查询期：

```text
RetrievalDebug.vue → Pinia → POST /api/retrieval/search
→ 请求校验 → 验证现有 Collection/Embedding 身份
→ Query Embedding → Qdrant Dense Search + Repository Filter + TopK
→ 校验 UUID/仓库/路径/行范围/内容长度 → 排名 Chunk → 代码预览
```

评测调用正常查询 API，逐结果对照本地源码，再计算指标。本次没有调用索引接口。当前检索链路 **没有调用聊天 LLM**。

后续 Stage 3 查询期计划：

```text
Question / Issue → Repository 校验 → 复用 Dense Retrieval
→ ContextBuilder（保持排名、基础去重、预算、后端 Source ID）
→ 独立 System Prompt + 不可信 Repository Context
→ LLMProvider.complete_structured() 一次
→ CitationValidator（仅允许本次实际注入的 Source）
→ 服务端映射真实 Path / Symbol / Lines → Answer / Analysis
```

## 我必须理解的知识

| 知识 | 它是什么、为什么需要 | 当前实现和输入 → 输出 | 调用链位置 |
| --- | --- | --- | --- |
| Chunking | 将文件切成检索单元；影响每个向量保留多少逻辑。当前不认识函数边界 | `indexing/chunker.py`：RepositoryFile → 重叠行窗口 CodeChunk | 索引时，Embedding 之前 |
| Metadata / Provenance | 为结果保留可回到真实源码的出处，后续 Citation 的基础 | `indexing/models.py`、`schemas/retrieval.py`：源码切片与位置 → 路径、语言、1-based 闭区间、内容 | 从 Chunk 经 Payload 到查询响应 |
| Dense Retrieval / Repository Filter | 以查询向量找相近代码，并限定所选仓库，避免不同仓库混入 | `retrieval/service.py`、`vector_store.py`：Query、repository、TopK → 排名 Chunk/score | 查询时；尚不生成答案 |
| Stable ID / Re-index | 同一输入得到相同 UUID；变化后的新块仍需替换旧点，稳定 ID 本身不清理数据 | `chunker.py` 的 UUID v5、`indexing/service.py`：完整新 Chunk/Vector → 该仓库的新索引 | 索引时；替换非事务 |
| Hit@K / Macro Recall@K | Hit 看是否命中任一目标文件；Recall 看找齐多少目标文件。二者不能代表答案正确 | `evaluation/retrieval/evaluate.py`：真实前 K 个 Chunk 路径和人工 expected_paths → 每题指标/汇总报告 | Retrieval 输出之后 |

Stage 2 已完成 Retrieval，Stage 3 还需要 Context Injection 与 Generation。可靠出处不等于模型推断一定成立；未来 Citation Validator 验证引用身份，答案是否被代码支持仍需评测。

## 真实数据示例

直接读取当前 Qdrant 集合得到：

| Collection | 实际 Point 数 | 向量维度 | 距离 | 状态 |
| --- | ---: | ---: | --- | --- |
| `repopilot_code` | **8** | **1024** | Cosine | green |
| `repopilot_infra_smoke` | **0** | **3** | Cosine | green |

代码集合 Metadata 的 `repopilot_index` 保存 `schema_version=code-points-v1`、`embedding_provider=openai_compatible`、`embedding_model=qwen3-embedding:0.6b`、Endpoint SHA-256。Collection 的 `payload_schema={}` 表示当前没有建立 Payload 字段索引，不表示没有 Payload，或没有应用层校验；8 个点实际保存如下字段：

```text
chunk_id, repository, path, language, start_line, end_line, content,
embedding_model, schema_version, chunk_lines, overlap_lines
```

以下是实际退款 Point 的元数据摘录，未添加任何尚不存在的 Symbol 或 Citation：

```json
{
  "chunk_id": "67881397-a785-5ed6-ad27-40ce2d67e8e6",
  "repository": "demo-repository",
  "path": "backend/service/RefundService.java",
  "language": "java",
  "start_line": 1,
  "end_line": 19,
  "embedding_model": "qwen3-embedding:0.6b",
  "schema_version": "code-points-v1",
  "chunk_lines": 60,
  "overlap_lines": 10
}
```

对应真实 [RefundService.java](../workspaces/demo-repository/backend/service/RefundService.java) 第 12–18 行：

```java
    public void refund(String orderId, String userId, BigDecimal amount) {
        if (orderId == null || orderId.isBlank()) {
            throw new IllegalArgumentException("Order ID is required");
        }
        // Fixture workflow: credit the refunded amount to the user's wallet.
        wallets.credit(userId, amount);
    }
```

存储的 `content` 是原文件完整 1–19 行，不仅是上述展示片段。8/8 Point 均确认：合法相对路径、语言与扩展名一致、行号有效、content 与原文精确相同、UUID 与重新切块一致、向量维度符合 1024。通过 Scanner 解码保留缩进和换行，去除 UTF-8 BOM；`language` 只是扩展名分类，不意味着已解析语法。

代码集合完整 Point 数据（含向量）规范化 SHA-256 在本次前后均为：

```text
1a4ab856204e4e19b946e808965227c9c9f1dddb9ac3a6b1bede590880bf254b
```

此处采用的哈希对象/格式与历史 Task 10 的组合哈希不同，不能直接比较两个不同算法的指纹。实际保留验证比较本次同一算法的前后指纹及完整配置，历史报告文件本身也确认未改变。

本次没有 AST Node、RAG Context、Source ID、生成答案或 Citation 的运行结果。

## 验收方法

现有依赖和服务已可用。仓库根目录启动：

```powershell
.\infra\Qdrant.ps1 up
.\Start-RepoPilot.ps1 -NoBrowser
Invoke-RestMethod http://localhost:8000/health/qdrant
Invoke-RestMethod http://localhost:8000/api/vector-store/collection
```

只读验证 Simple Chunk，不需要写入或重新索引：

```powershell
$chunkBody = '{"repository":"demo-repository","preview_limit":20}'
Invoke-RestMethod -Method Post -Uri http://localhost:8000/api/chunks/debug `
  -ContentType 'application/json' -Body $chunkBody | ConvertTo-Json -Depth 8
```

预期创建 8 个 Chunk；退款文件是 `backend/service/RefundService.java`，`java`，1–19 行。也可用 10 行窗口/2 行重叠，退款文件将分为 1–10、9–18、17–19 行；这个调试请求不会改变索引。

在 [Retrieval Debug](http://localhost:5173/#retrieval) 输入仓库 `demo-repository`、Query `退款服务在哪里校验订单编号，然后把退款金额转回钱包？`、TopK 5。比较退款块实际内容和源码；本次该题退款实现排第 2，README 排第 1。页面本次仅检查代码和构建，手工浏览器操作供你验收。

测试/构建使用现有环境，避免运行安装同步：

```powershell
# services/api 下；保持外部集成标志为 0
$env:REPOPILOT_QDRANT_INTEGRATION = '0'
$env:REPOPILOT_INDEXING_INTEGRATION = '0'
$env:REPOPILOT_RETRIEVAL_INTEGRATION = '0'
$env:REPOPILOT_EMBEDDING_INTEGRATION = '0'
& .\.venv\Scripts\python.exe -m pytest -rs

# apps/web 下
npm.cmd test
npm.cmd run build

# 仓库根目录
& .\services\api\.venv\Scripts\python.exe -m pytest evaluation/tests -q
& .\services\api\.venv\Scripts\python.exe evaluation/retrieval/evaluate.py
```

评测自动生成新报告，不覆盖旧报告。观察 12 条请求、模型 1024 维和 Hit/Recall；不要调用重新索引接口来验收本 Task。

## 当前未实现

尚无 Tree-sitter、Parser、AST-aware Chunker、Symbol/parent_symbol/node_type 元数据、可见 Chunking Version、Context Builder、Context Budget、RAG Prompt、Code Chat、Citation Validator 或 Issue Analyzer。

尚无 BM25、Sparse/Hybrid Search、RRF、Reranker、Query Expansion、LangChain、LangGraph、Memory 或仓库 Agent。Stage 1 已存在的固定信息工具循环只是一项演示；Stage 3 计划不使用它。不自动修改仓库代码、运行仓库测试或生成 Git Patch。

## Bad Case / 风险

1. **真实漏召回**：`refund-validation-files` 的题目问订单编号/金额校验分别在哪，Top5 只命中 RefundService，遗漏 WalletService，Recall@5=1/2。`refund-call-chain` 问入口到余额更新，命中 RefundService 和 OrderController，仍遗漏 WalletService，Recall@5=2/3。只记录，不加入 Hybrid Search 或 Rerank。
2. **真实排名问题**：`refund-service` 题中 README 排第 1（0.6155841），实际实现排第 2（0.6145509）。语义相近的说明文本不能替代实现证据。AST 也不能保证修复 Dense Retrieval 的全部漏召回。
3. **敏感文件规则缺口**：直接调用现有忽略判断函数，`.env`、`.env.production`、`private.pem`、`private.key` 均被排除；`credentials.json`、`secrets.yaml` 以及 `credentials`/`secrets` 目录均未被排除，且 JSON/YAML 属于可支持语言。这是文件名规则探针，不读取凭证内容。本次样例未包含这类文件；未改代码。后续在索引更广泛仓库前，需要最小修复 `repository/ignore.py` 并补 Scanner 测试；不是完整 Secret 检测器。
4. **行号兼容风险**：Simple Chunker/Scanner/评测使用 Python `splitlines()`，包括 CR 和 Unicode 行分隔符。Tree-sitter 接入时必须实测字节位置、中文、CRLF、末尾换行及结束边界，保持输出 content 等于原文件整行切片，并统一当前 Schema/评测的行号约定。不能盲目把语法树位置当成现有行号。
5. **Repository 错误区分不足**：当前 Search 只校验名称并应用 Payload Filter；不存在/未索引的仓库可能返回同样的空结果，没有 RAG 所要求的 Not Found / Not Indexed 区分。后续 RAG 入口要增加轻量状态检查，复用路径边界和仓库计数，不每次扫描或解析整仓库。
6. **索引保留限制**：先删除后写入不是事务，进程锁只覆盖单个 API 进程。原文件改动后旧索引出处可能过期，必须显式重建；当前生产 Search 不每次与磁盘逐字符比较，本次完整内容核验由审查/评测完成。
7. **Prompt/预算边界尚无实现**：Context 只来自最终选中且实际注入的 Chunk；Source ID 由服务端分配，引用只能映射该集合。预算优先丢弃完整 Source；若以后截取内容，必须同步精确行号，不能截文本后沿用完整范围。仓库数据不能进入 system 指令，基础 Prompt/Delimiter 防护也不能证明模型绝对安全。
8. **工作区缺少 Git 元数据**：目前无法提交、对照历史或可靠回滚。是否恢复原 Git Checkout 或初始化版本管理，需要独立明确处理；本次文件哈希只是本次无改动证据。

## 下一任务

下一步是 **Task 2：Tree-sitter 最小 Parser Spike**，用当前真实 Java 文件验证类、构造器和方法节点，输出真实 Node Type、Symbol、Start/End Lines，建立小型 Parser 抽象。Task 2 不接 Indexing、不写 Qdrant。

以下是按原 Task 划分的最小增量顺序；仅为计划，本次未执行：

| Task | 最小交付 |
| ---: | --- |
| 2 | Java Parser Spike + 节点/源码位置测试；后续语言兼容性先实测 |
| 3 | 新 AST Chunker + CodeChunk 元数据；保留 Simple Fallback；大小、失败和行号测试 |
| 4 | 接原 Indexer、追加 Payload/版本/解析统计；补敏感名称排除；明确目标集合再重新索引 |
| 5 | 扩展后端投影和前端 Debug；同题同模型比较 Simple/AST，保留独立报告 |
| 6 | 纯 ContextBuilder：基础去重、保持排名、Chunk/字符预算、Source ID；不调用 LLM |
| 7 | 新 Code Chat 后端固定 RAG 链，单次 `complete_structured()`；模型仅输出 Source IDs |
| 8 | CitationValidator 映射本次已注入 Source 的真实元数据；非法/重复/跨仓库引用测试 |
| 9 | 审查独立 Prompt 和不可信数据边界；README/注释 Injection 用例；记录防护限制 |
| 10 | 简单 Code Chat 前端，显示答案与可展开真实来源；复用现有预览模式 |
| 11 | Issue Structured Output + 来源映射；给分析/建议，不改代码或调用工具循环 |
| 12 | Stage 3 检索/答案/引用评测、人工核验、真实 Bad Case；不提前优化高级检索 |
| 13 | 完整 Stage 1/2 回归、构建和两阶段评测；再更新 README、配置模板和 Stage 3 文档 |

Task 7 的模型 Source IDs 只是内部结构化结果；在 Task 8 完成验证前，不能把它们当成已经验证的用户引用。基础 Prompt 边界在首次 Context 注入时就应具备，Task 9 再做专项审查和测试，而不是让 Task 7 暂时信任仓库指令。

**Task 1 到此结束，停止开发，等待下一条指令。**
