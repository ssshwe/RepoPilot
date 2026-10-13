# Stage 3 / Task 4：AST Indexing 与 Re-index

验收日期：2026-10-07，Asia/Shanghai。Task 4 已完成；没有进入 Task 5。

## 本次完成

已接通 **Scanner → AST Chunker → 本地 Qwen Embedding → Qdrant**，通过真实运行的 Uvicorn HTTP API 重新索引 `demo-repository`。`repopilot_code` 中该仓库的旧 **8 个 Simple Point 已替换为 17 个新 Point**；再次重新索引仍为 17 个，ID 集合保持，未累积旧数据。

升级了 Point Payload，持久化 `symbol / node_type / parent_symbol / chunking_strategy / chunking_version`；集合 metadata 按 repository 保存实际 chunking profile。修复了 Task 1 已发现的 credentials/secrets 敏感命名过滤缺口。继续使用现有 Dense Retrieval、Embedding Provider、Qdrant collection 和稳定 UUID5，没有加入高级检索机制。

### 真实运行统计

下表来自两次实际 HTTP 响应，而非 mock：

| 指标 | 首次重建 | 重复重建 |
| --- | --- | --- |
| Files Scanned，含被跳过的候选文件 | 10 | 10 |
| Supported Files | 8 | 8 |
| AST Parsed Files | 4 | 4 |
| Fallback Files | 4 | 4 |
| Failed Files | 0 | 0 |
| Chunks Created | 17 | 17 |
| Vectors Indexed | 17 | 17 |
| Previous Vectors | 8 | 17 |
| Embedding Model | `qwen3-embedding:0.6b` | 相同 |
| Embedding Dimension | 1024 | 1024 |
| Collection | `repopilot_code` | 相同 |
| Chunking Version | `ast-v1` | `ast-v1` |

17 个 Point 包含 **13 个 AST Chunk、4 个 fallback Chunk**。文件数与 Chunk 数不同：三个 Java 文件各生成 4 个 Chunk，TypeScript 文件生成 1 个，四个 fallback 文件各生成 1 个。`notes.txt` 因不支持扩展名跳过；`package-lock.json` 因忽略规则跳过。

完整响应与核验：[first-index-response.json](../output/stage3-task4/first-index-response.json)、[repeat-index-response.json](../output/stage3-task4/repeat-index-response.json)、[live-verification.json](../output/stage3-task4/live-verification.json)。验收使用临时 API 端口 8014，完成后已停止该 API 进程；Qdrant 和现有本地 Embedding 服务保留运行。

### 验证结果

| 验证 | 本次实际结果 |
| --- | --- |
| 完整后端回归 | **625 collected；620 passed、5 skipped、2 warnings** |
| 新增覆盖 | 新文件 27 个用例，加忽略目录列表扩展触发的 4 个既有参数化用例 |
| 实际 Embedding + Qdrant 集成测试 | **1 passed**，39.42 秒；临时独立集合验收后删除 |
| 集成测试场景 | 22 个 Point → 重复仍 22 → 删除 README 后 19 → 空仓库 0 |
| Stage 2 评测单测 | **32 passed**；原数据集/报告不变 |
| 前端构建 | 通过，1615 个模块 |
| 实际 Dense Retrieval 冒烟 | `POST /api/retrieval/search` 返回 200、5 个当前仓库的合法结果 |
| 真实向量核验 | 17 个 Point 均为非零、有限数值、1024 维 |
| 真实 Payload 核验 | 所有元数据与 Prepared Chunk 一致，所有内容可按真实源码行范围复原 |
| 持久化数据范围 | `repopilot_infra_smoke` 未变；临时测试集合已清理 |

完整后端的 5 个跳过项仍为既有外部集成开关/Windows 符号链接权限用例。其中 indexing 集成用例随后单独开启并通过，不能把该单独运行伪装成完整套件没有跳过。两个依赖弃用警告仍为原有警告。

Task 4 开始恢复现有 WSL/Docker Qdrant 后，旧 8 个 Point 的完整指纹与 Task 1 / Task 2 保存的指纹一致，补上了 Task 3 服务不可用时未能完成的在线检查。随后才执行本次已授权的替换。没有重跑 12 条检索评测题，Simple/AST 检索对比留给 Task 5。

## 修改文件

| 既有文件修改 | 职责 |
| --- | --- |
| [repository/ignore.py](../services/api/app/repository/ignore.py) | case-insensitive 地忽略 credentials/secrets 文件及同名目录，在打开源码前过滤 |
| [indexing/ast_chunker.py](../services/api/app/indexing/ast_chunker.py) | 新增设置驱动的 AST factory，字符预算取配置与 Embedding 8192 上限的较小值 |
| [api/indexing.py](../services/api/app/api/indexing.py) | 现有重新索引 API 默认使用 AST factory，保留验证、线程池准备和客户端清理 |
| [indexing/service.py](../services/api/app/indexing/service.py) | 仓库准备统计、总预算、实际 chunking profile、Payload、写入后版本提交和日志 |
| [schemas/indexing.py](../services/api/app/schemas/indexing.py) | 响应追加真实 AST/fallback/failed 统计、版本及实际字符预算 |
| [retrieval/vector_store.py](../services/api/app/retrieval/vector_store.py) | 写入并回读确认 repository chunking metadata，保留其他仓库 profile 和其他 metadata |
| [test_indexing.py](../services/api/tests/test_indexing.py) | 更新 factory patch 路径，保留 Stage 2 预算、错误、并发和数据保留测试 |
| [test_indexing_integration.py](../services/api/tests/test_indexing_integration.py) | 真实集成验收改为 AST，验证新 Payload，实际重建/编辑/空仓库清理 |

新增 [test_ast_indexing.py](../services/api/tests/test_ast_indexing.py)、[stage-3-task-4-verification.json](stage-3-task-4-verification.json) 和本文。`output/stage3-task4/` 保存真实 HTTP 响应、完整 Qdrant 前后快照、日志和验收脚本。

检查了 **157 个原有文件**，仅上述 8 个既有文件改变。依赖声明/锁文件、`.env`、前端、demo 源码、评测数据集、旧报告均保持原指纹。没有 Git 元数据，因此未声称有 Git 提交或干净工作树；数据保留依据见 [workspace-preservation.json](../output/stage3-task4/workspace-preservation.json)。

## 调用链 / 数据流

```text
POST /api/indexing/repositories/demo-repository
→ 验证 Repository 名称 → 单进程 indexing_guard
→ RepositoryScanner：路径安全 / 敏感命名过滤 / 解码 / 扫描预算
→ create_ast_code_chunker：选择实际大小配置
→ 每文件 AST / fallback → CodeChunk → AST/fallback 文件统计
→ 仓库总 Chunk 数与文本预算 → PreparedRepository
→ 现有 EmbeddingProviderFactory → 批量 Embedding → 验证所有向量
→ 验证向量总预算、collection 维度、Cosine 和原有 embedding identity
→ count old repository points
→ delete only this repository → 分批 upsert 新 Point
→ exact repository count == 本次 Chunk 数
→ record_repository_chunking → 回读确认 collection metadata
→ RepositoryIndexResponse + 完整统计日志 → 关闭 provider/client
```

Parser 受控失败继续走 fallback，不让该文件的语法错误阻止其他文件。实际资源超限仍为 413，并在准备日志记录失败文件，不创建 Embedding/Qdrant 客户端。只有全部准备和 Embedding 验证成功，才进入旧点删除。

Point 的 `chunk_lines / max_chunk_characters` 来自实际 Chunker，避免只记录配置却与切片行为不一致。AST Payload 的 `overlap_lines=0`；fallback Payload 保留配置的重叠值，本次为 10。API 响应和仓库 profile 中的 `overlap_lines` 表示 fallback 窗口配置。

## 我必须理解的知识

### 1. Re-index：重新生成向量并替换旧点

新增 AST Chunker 不会改变已有 Vector。输入为真实仓库文件，[service.py](../services/api/app/indexing/service.py) 准备新 Chunk、重新 Embedding，再通过 repository filter 删除旧点并 upsert 新点，输出当前仓库的新索引和统计。正常重建从 8 → 17；重复重建从 17 → 17，不是 17 → 34。

稳定 ID 便于追踪，但删除旧点仍是必要步骤：源码变化或切片算法变化后，新 ID 无法自动覆盖所有旧 ID。其他 repository 的点不在当前删除 filter 范围内。

### 2. Payload：向量对应的真实代码证据

Embedding Provider 的输入是 `CodeChunk.content`，输出 1024 个坐标；Symbol、路径、行号不由向量坐标或模型生成。Indexer 将真实 `CodeChunk` 元数据写入同一 Point 的 Payload，后续检索才能把相似度结果还原到真实文件和声明。

本次存储五个新增字段，且逐项与 Prepared Chunk 比较，再用 `source.splitlines(keepends=True)[start_line-1:end_line]` 验证内容。这一步保障以后 Citation 能使用服务器已有证据。坐标本身不适合人工解释，也不能替代 Payload 的正确性核验。

### 3. Collection metadata：模型身份与切片版本各有职责

现有 `repopilot_index` 继续保存 schema、provider、model 和 endpoint hash，供索引/查询兼容检查；不写入密钥或完整 endpoint。新增 `repopilot_chunking[repository]` 保存该仓库的 pipeline 版本、fallback 版本和实际预算。

Qdrant 支持 collection 自定义 metadata，见 [官方文档](https://qdrant.tech/documentation/manage-data/collections/#collection-metadata)。按 repository 保存 profile 是 RepoPilot 的约定：只重建一个仓库，不能宣称整集合的其他仓库都升级了。profile 仅在 Point 写入和最终数量确认后更新，并回读验证。

### 4. 准备阶段与提交阶段：先发现可提前发现的错误

[prepare_repository()](../services/api/app/indexing/service.py) 输入 Scanner/Chunker，输出受预算约束的 `PreparedRepository`。Indexer 验证所有 Embedding 批次、维度和总向量数量后再删旧点。用例已证明后续批次 Embedding 失败时，旧点及旧 profile 都保留。

这减少可避免的数据损失，但没有把 delete/upsert 变成事务。写入未确认、最终数量不符或 metadata 回读失败时，API 返回受控错误并明确可能部分更新，不把不确定状态报为成功。

### 5. 敏感文件过滤：在源码读取之前做最小修复

[ignore.py](../services/api/app/repository/ignore.py) 输入文件/目录 basename，输出是否忽略。现在排除 `credentials`、`credentials.*`、`secrets`、`secrets.*` 及点前缀变体，并剪枝同名目录，大小写不敏感。原 `.env/*.pem/*.key` 规则继续有效。

测试用实际 reader spy 证明这些路径没有进入 `read_bounded_file`，因而不会进入 Chunk/Embedding。`credentials_reader.py` 等不匹配敏感 basename 的代码文件仍可扫描，没有用任意子串规则扩大过滤。

## 真实数据示例

本次写入 Qdrant 的退款 Point，字段摘自 [payload-examples.json](../output/stage3-task4/payload-examples.json)：

```json
{
  "point_id": "66b8fac4-539e-5bd2-802f-2f88b7965a2a",
  "vector_dimension": 1024,
  "vector_preview": [-0.042067032, 0.09202012, -0.005537196, -0.033054087],
  "repository": "demo-repository",
  "path": "backend/service/RefundService.java",
  "symbol": "RefundService.refund",
  "node_type": "method_declaration",
  "parent_symbol": "RefundService",
  "start_line": 12,
  "end_line": 18,
  "chunking_strategy": "ast",
  "chunking_version": "ast-v1",
  "overlap_lines": 0
}
```

`vector_preview` 是真实 1024 维向量的前四个值，不是完整向量。完整 Payload 包含源码 `content`、Embedding 模型、schema、实际行数及字符预算。Vue Point 也已实际索引，其 `symbol/node_type/parent_symbol=null`、`chunking_strategy=fallback`、`chunking_version=ast-fallback-line-v1`，没有虚构 AST Symbol。

当前集合的真实 profile 为：

```json
{
  "repopilot_chunking": {
    "demo-repository": {
      "chunking_version": "ast-v1",
      "fallback_chunking_version": "ast-fallback-line-v1",
      "chunk_lines": 60,
      "overlap_lines": 10,
      "max_chunk_characters": 8192
    }
  }
}
```

完整前后数据已保存为 [qdrant-before.json](../output/stage3-task4/qdrant-before.json) / [qdrant-after.json](../output/stage3-task4/qdrant-after.json)。前者保留原 8 个 Point 的 Payload 和向量，便于 Task 5 对照；旧评测报告也保持。最后数据指纹为 `af643856c5172b9ff6225e64d156c4a646e936adbed81fcf80cfb00d11f43e98`。

## 验收方法

继续使用已经配置的本地 Embedding 服务和模型，不需要 OpenAI Platform key。项目根目录启动现有 Qdrant：

```powershell
.\infra\Qdrant.ps1 -Action up
```

在 `services/api` 目录启动更新后的 API：

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

另一个 PowerShell 调用现有索引接口：

```powershell
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8000/api/indexing/repositories/demo-repository'
```

此操作会重新计算 Embedding 并替换该仓库索引。当前仓库应返回 10 个候选文件、4 AST/4 fallback/0 failed、17 个 Chunk/Vector、1024 维；由于本次已完成重建，下一次 `previous_vectors` 应为 17。日志应有 `Index prepared` 和 `Index complete`，并包含各项计数和版本。

本次为了避免影响已有 API，使用独立端口 8014 做真实 HTTP 验收，并已只停止该次启动的进程。上面的 8000 为常规手动启动端口；临时端口不会继续提供服务。

在 `services/api` 目录执行单测及可选真实集成测试：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_ast_indexing.py tests/test_indexing.py -q
.\.venv\Scripts\python.exe -m pytest -q
$env:REPOPILOT_INDEXING_INTEGRATION='1'
.\.venv\Scripts\python.exe -m pytest tests/test_indexing_integration.py -q
Remove-Item Env:REPOPILOT_INDEXING_INTEGRATION
```

真实集成测试仅使用临时拷贝的源码和唯一测试集合，结束后清理，不编辑 demo 源码。最终再次只读核验，生产集合及其他集合与重复重建完成后的快照完全相同，测试集合没有残留。

检索兼容性仅做了现有接口的冒烟请求：`POST /api/retrieval/search`，输入 `{"repository":"demo-repository","query":"退款逻辑在哪里？","top_k":5}`。本次返回 200、5 个属于当前仓库和本次 ID 集合的结果；这不是检索质量对比或 RAG 回答。响应和日志见 `output/stage3-task4/dense-retrieval-smoke.json`。

## 当前未实现

未进入 Task 5 的 Retrieval Debug metadata 展示和 Simple/AST 对比实验。现有 `/api/chunks/debug` 仍是 Simple 预览入口，它不能代表当前 Qdrant 已写入的 AST Payload；现有检索响应/前端尚未展示新增 Symbol 字段。

没有 Context Builder、Code Chat、Citation Validator 或 Issue Analyzer；没有 Hybrid Search、Reranker、Query Expansion 或 Agent 循环，没有由模型自动修改源码。

## Bad Case / 风险

- **非事务替换**：delete 和分批 upsert 之间存在窗口；提交阶段失败可能留下部分索引。当前返回明确错误并要求完整重建，未宣称提供原子切换。
- **单进程锁**：`indexing_guard` 只能协调一个 API 进程；多 worker 或外部 writer 仍需要协调。按 repository 合并 metadata 也依赖这项约束。
- **版本确认失败**：最终 Point 数量正确也不足以报成功；metadata 更新与回读同样需要确认。测试覆盖了未确认、读回不一致和 malformed metadata 的提前拒绝。
- **降级预算**：语法错误是可降级故障，文件/字符/总 Chunk 预算超限则必须在写入前报错；不能把资源失败伪装成成功 fallback。

## 下一任务

**Task 5：Retrieval Debug 升级 + Simple / AST 对比实验**。展示 Score、Path、Symbol、Node Type、Lines、Chunking Strategy，并使用保留的 Simple 基线和真实 Query 比较检索结果。Dense Retrieval 机制保持，不加入 BM25/Rerank。Task 4 到此停止，等待“继续”。
