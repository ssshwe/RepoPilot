# Stage 3 / Task 3：AST-aware Chunker

日期：2026-10-06，Asia/Shanghai。Task 3 已完成；停在独立 Chunker 验收，没有进入 Task 4。

## 本次完成

已实现独立的 `ASTCodeChunker`，把 Task 2 的 `ParsedSource / SyntaxSymbol` 转成带真实 Symbol、父 Symbol、节点类型、行号、策略和版本的 `CodeChunk`。补齐 Java、Python、JavaScript、TypeScript 四种核心语言。Vue、Markdown、YAML、JSON、SQL 使用现有 Simple Chunker 降级。

真实 demo 仓库的离线运行结果为 **8 个支持文件、4 个 AST 文件、4 个 fallback 文件、0 个失败文件、17 个 Chunk**。这 17 个是本次预览生成的 Chunk，没有 Embedding，也没有成为 Qdrant 中的新 Vector。

| 验证 | 实际结果 |
| --- | --- |
| 完整后端测试 | **594 collected；589 passed、5 skipped、2 warnings** |
| Parser / AST Chunker / CLI 用例 | **123 个**，包含保留的 Java Parser 用例和本 Task 新增的 94 个用例 |
| Stage 2 评测单测 | **32 passed** |
| 前端构建 | `vue-tsc -b && vite build` 通过，1615 个模块 |
| 依赖锁检查 | `python -m uv lock --check --offline` 通过 |
| 原有文件指纹 | 检查 149 个原有文件，只有下表 6 个既有文件改变 |
| 外部模型调用 / 持久化索引写入 | **0 / 0** |
| Qdrant 只读检查 | 连接失败，`URLError`；本次无法重新核验在线集合状态 |

5 个跳过项仍为原有外部服务集成测试及 Windows 文件符号链接权限测试；没有启用会创建/删除持久化测试集合的集成开关。原有两个依赖弃用警告保持。没有重跑 12 条真实检索题，也没有把 Task 1 的检索指标当作本次的新结果。

依赖仅增加三个官方 grammar package，其他锁定版本和包记录保持：

| Grammar | 声明范围 | 本次实际安装 / ABI |
| --- | --- | --- |
| Java，Task 2 已有 | `>=0.23.5,<0.24.0` | 0.23.5 / 14 |
| Python | `>=0.25.0,<0.26.0` | 0.25.0 / 15 |
| JavaScript | `>=0.25.0,<0.26.0` | 0.25.0 / 15 |
| TypeScript | `>=0.23.2,<0.24.0` | 0.23.2 / 14 |

Tree-sitter core 继续使用已锁定的 0.26.0。实际 grammar 来源可查 [Python package](https://pypi.org/project/tree-sitter-python/)、[JavaScript package](https://pypi.org/project/tree-sitter-javascript/)、[TypeScript package](https://pypi.org/project/tree-sitter-typescript/)。版本与 ABI 同时通过本地安装包和真实解析核验，没有升级已有依赖。

## 修改文件

| 既有文件修改 | 职责 |
| --- | --- |
| [indexing/models.py](../services/api/app/indexing/models.py) | 给 `CodeChunk` 追加五个有兼容默认值的字段；原来的七个参数和 Simple ID 保留 |
| [parsing/registry.py](../services/api/app/parsing/registry.py) | 注册四种 grammar 和对应声明节点类型 |
| [parsing/tree_sitter_parser.py](../services/api/app/parsing/tree_sitter_parser.py) | 提取 Python 装饰器/异步声明、JS/TS 静态具名绑定及父作用域；保留 Java 回归行为 |
| [pyproject.toml](../services/api/pyproject.toml)、[uv.lock](../services/api/uv.lock) | 增加并锁定三个 grammar；原有依赖不变 |
| [test_parsing.py](../services/api/tests/test_parsing.py) | Registry 从 Java 扩展到四语言；原 Java 边界回归仍运行 |

| 新文件 | 职责 |
| --- | --- |
| [indexing/ast_chunker.py](../services/api/app/indexing/ast_chunker.py) | AST 声明与间隙分段、大小控制、受控 fallback、元数据验证、稳定 ID |
| [scripts/ast_chunk_spike.py](../services/api/scripts/ast_chunk_spike.py) | 通过现有 Scanner 离线预览，统计 AST/fallback/failed，限制全局预览数量 |
| [test_parsing_languages.py](../services/api/tests/test_parsing_languages.py) | 新语言、装饰器、绑定、匿名体、namespace、Unicode/换行和语法错误 |
| [test_ast_chunker.py](../services/api/tests/test_ast_chunker.py) | 真实文件、大小/数量预算、嵌套函数、同一行边界、降级、ID 和离线边界 |
| [test_ast_chunk_spike.py](../services/api/tests/test_ast_chunk_spike.py) | CLI 三种文件统计、继续处理后续文件、预览不掩盖失败、Scanner 审批范围 |
| [fixtures/parsing/refund.js](../services/api/tests/fixtures/parsing/refund.js) | 明确标注的手写 JavaScript 验收 fixture，不冒充生产仓库 |
| [stage-3-task-3-verification.json](stage-3-task-3-verification.json) | 本次真实版本、数据、测试和保留证据 |
| 本文 | 调用链、策略、学习说明和可重复验收 |

Parser 的基础接口、Scanner、`SimpleCodeChunker`、Indexer、Retrieval、前端、demo 源码、评测数据集、旧报告及 `.env` 的原有指纹保持。当前工作区没有 Git 元数据，使用文件指纹核对范围，未初始化仓库或创建提交。

## 调用链 / 数据流

```text
ast_chunk_spike.py
→ 现有 RepositoryScanner：安全读取、忽略规则、UTF-8 解码
→ RepositoryFile
→ ASTCodeChunker：先检查实际 UTF-8 文件大小
→ ParserRegistry：扩展名与 language 一致性 → 四语言之一
→ Tree-sitter → root.has_error 检查 → ParsedSource / SyntaxSymbol
→ 验证字节/行号/源码身份 → 声明包含层级
→ 方法/函数分段 + 父类/模块剩余片段
→ 行数与字符预算内的原文完整行切片
→ 元数据 + 内容哈希 + UUID5 → CodeChunk
→ FileChunkingResult → CLI JSON / 文件统计
```

受控分支为：

```text
未注册语言 / 语法错误 / 受控 Parser 故障 / 同一行相邻声明
→ SimpleCodeChunker，内容和行范围沿用 Stage 2
→ symbol / node_type / parent_symbol = null
→ chunking_strategy = fallback
→ chunking_version = ast-fallback-line-v1
```

`fallback_reason` 只保留有限枚举：`unsupported_language`、`syntax_error`、`parser_error`、`shared_line_boundaries`。解析异常的消息和源码不写入日志。未知的程序错误不会被宽泛捕获后伪装成成功降级。

文件字节、单行字符、Chunk 数量超限属于资源错误，直接抛出 `ChunkingLimitError`。CLI 将文件记作 failed，并继续处理其他文件，最终返回非零退出码。单文件所有 Chunk 验证完成后才向调用方返回/产出，不暴露一个前半段成功、后半段失败的文件结果。

目前 `/api/chunks/debug` 和 Indexing factory **仍返回 `SimpleCodeChunker`**，没有新建 HTTP API。`CodeChunk` 的新增默认字段为 `null/null/null/simple/line-window-v1`；Indexer 原有 `asdict()` 若以后被调用会序列化这些默认字段。本 Task 没有修改或运行索引流程；完整 AST 接线、集合版本身份与 payload 验收属于 Task 4。

## 我必须理解的知识

### 1. AST 与 Parser：先识别声明，再决定切片

AST 是语法结构，Parser 是产生结构和可用节点元数据的组件。输入为 `RepositoryFile.content`，由 [registry.py](../services/api/app/parsing/registry.py) 选择 grammar，[tree_sitter_parser.py](../services/api/app/parsing/tree_sitter_parser.py) 输出 `ParsedSource`。Parser 本身不构造向量或 Chunk。

RepoPilot 行号是一基、闭区间；字节是一段原文 UTF-8 的左闭右开区间。Native Point 仅用于调试。本次继续保留 CR/LF/CRLF 的字节映射，Python 装饰器和 export 包装也来自真实原文，后续行号引用不需要模型猜测。

### 2. Symbol：片段属于哪个具名声明

例如 `RefundService.refund` 是方法名和最近具名父作用域组成的词法路径。Parser 从 name 字段或直接变量绑定产生它，`CodeChunk.symbol / parent_symbol` 继承它。JS/TS 的 `const canRefund = (...) => ...` 采用静态变量名；对象方法可形成 `api.refund`，TypeScript namespace 方法可形成 `Wallet.credit`。

这不是编译器的类型解析或调用图。同名重载/getter/setter 可共享 Symbol 字符串，真实字节和片段位置参与 ID，避免把它们当成同一个 Chunk。未绑定匿名 callback、动态计算属性不会被编造为外部类的方法，内容仍留在父片段或模块片段。

### 3. AST-aware Chunking：声明边界加大小控制

[ast_chunker.py](../services/api/app/indexing/ast_chunker.py) 输入声明范围及源码，输出 `CodeChunk`。正常方法整体保留；含方法的类仅保留类头/字段等间隙，方法各自切片，避免把完整父类再重复嵌入。嵌套函数拥有自己的行，外层函数保留其前后代码。短 getter/setter 各自保留，不合并成虚构源码。

默认文件上限 **1 MiB**，AST 单片段最多 **60 行、8192 字符**，单文件最多 **10000 个 Chunk**。超长方法在其行范围内分片，所有片段保持相同 Symbol 和父 Symbol，AST 片段不重叠。字符计算包括缩进与换行；它是现有 Embedding 文本限制，不是假装精确的 Token 计算。单行超过上限明确失败，绝不静默截断。Task 4 还要保留现有整个 Repository 的总预算。

类/模块间隙中仅有空白、闭合括号和分隔符的片段不单独向量化；有实际内容的片段保持完整原文行，不 prepend 类头或合成注释。每个 Chunk 均可用 `source.splitlines(keepends=True)[start_line-1:end_line]` 直接复原。

### 4. Fallback：Parser 失败可以降级，资源超限必须可见

降级使一个有语法错误的文件不阻止其他文件产出 Chunk。AST Chunker 捕获明确的 Parser 异常，把原文件交给现有 [chunker.py](../services/api/app/indexing/chunker.py)，再补充 fallback 元数据和新的版本 ID。默认 fallback 仍为 **60 行、10 行重叠**，没有额外的语义 Symbol。

Fallback 的字符超限行为也沿用原 Simple 算法：一个窗口超限会报错，不会借降级绕过预算。CLI 的 `ast_parsed_files` 指最终采用 AST 策略的文件数；同一行声明即使 native tree 成功，也计入 fallback 文件。文件日志及最终 `chunk_summary` 明确展示 AST / fallback / failed。

### 5. Stable Chunk ID 与 Chunking Version：知道片段由哪套规则产生

现有 Simple ID 的 `line-window-v1` 和 namespace 保持。本次 AST 使用 **`ast-v1`**，fallback 使用 **`ast-fallback-line-v1`**。[ast_chunker.py](../services/api/app/indexing/ast_chunker.py) 将 repository、path、language、Symbol、声明字节、切片行号、策略/版本、实际切片预算和内容 SHA-256 组成有序身份，再通过 UUID5 生成 ID。

相同输入/规则重复运行，ID 相同；不同源码、位置或版本会变化。ID 不随机，便于 Task 4 管理重新索引的数据。但稳定 ID 本身不会删除旧 Vector；旧数据替换仍需要 Task 4 的显式重新索引。删除纯 whitespace/brace gap、匿名体策略等算法行为变更以后应升级版本。

## 真实数据示例

数据来自本次 [demo-chunks.json](../output/stage3-task3/demo-chunks.json) 的实际运行：

```json
{
  "chunk_id": "66b8fac4-539e-5bd2-802f-2f88b7965a2a",
  "repository": "demo-repository",
  "path": "backend/service/RefundService.java",
  "language": "java",
  "start_line": 12,
  "end_line": 18,
  "content": "    public void refund(String orderId, String userId, BigDecimal amount) {\n        if (orderId == null || orderId.isBlank()) {\n            throw new IllegalArgumentException(\"Order ID is required\");\n        }\n        // Fixture workflow: credit the refunded amount to the user's wallet.\n        wallets.credit(userId, amount);\n    }\n",
  "symbol": "RefundService.refund",
  "node_type": "method_declaration",
  "parent_symbol": "RefundService",
  "chunking_strategy": "ast",
  "chunking_version": "ast-v1"
}
```

本次真实 Chunk 边界：

| 文件 | Symbol | 行号 | 策略 / 数量 |
| --- | --- | --- | --- |
| `RefundService.java` | 模块 / `RefundService` / 构造器 / `RefundService.refund` | 1–4 / 5–7 / 8–10 / 12–18 | AST / 4 |
| `WalletService.java` | 模块 / `WalletService` / `WalletService.credit` / `WalletService.balanceOf` | 1–6 / 7–9 / 10–18 / 20–22 | AST / 4 |
| `frontend/src/types.ts` | `RefundRequest` | 1–5 | AST / 1 |
| `frontend/src/RefundPanel.vue` | null | 1–22 | fallback / 1 |

Java 文件末尾单独的 `}` 没有形成一个额外 Vector 片段；方法自己的闭合行保持在方法内。另一个 Java Controller 产生 4 个，README/JSON/SQL 各产生 1 个，因此总数为 17。

[source-probes.json](../output/stage3-task3/source-probes.json) 另记录了真实项目文件：

- 既有 `services/api/app/repository/language.py`：2 个 AST Chunk，`detect_language` 为第 11–13 行，ID 为 `3fcf4332-3ec5-5c3d-bd96-f39855abaf40`。
- 本次新增 `ast_chunker.py`：13 个 AST Chunk；`ASTCodeChunker._ast_chunks` 实际较长，按 60 行上限拆成 **117–176 / 177–203**，两个片段的 Symbol 和父 Symbol保持。
- 手写测试 fixture `refund.js`：3 个 AST Chunk；`refund` 为 2–7 行、`canRefund` 为 9–11 行。它是测试 fixture，demo 仓库没有 JavaScript/Python 源码，不能把该 fixture 冒充生产数据。

这里还没有 Context、Source ID 或 Citation，它们属于后续 RAG Task。

## 验收方法

在项目根目录的 PowerShell 中运行，无需启动 FastAPI、Qdrant 或模型服务：

```powershell
.\services\api\.venv\Scripts\python.exe services/api/scripts/ast_chunk_spike.py
```

应看到 `ast_parsed_files=4 fallback_files=4 failed_files=0 chunks=17`，JSON 中对应的 Chunk 带五个新增字段。文件日志输出到 stderr，预览 JSON 输出到 stdout。

仅查看真实退款文件：

```powershell
.\services\api\.venv\Scripts\python.exe services/api/scripts/ast_chunk_spike.py --path backend/service/RefundService.java
```

应该返回 4 个 AST Chunk，其中 `RefundService.refund` 为第 12–18 行；对比原文件可验证内容与范围。查看 `.vue` 应返回 `strategy=fallback / fallback_reason=unsupported_language`，不编造 Symbol。`--preview-limit` 限制整个输出的 Chunk 预览数，所有文件仍完成验证，后面的失败不会因预览截断而漏掉。

在 `services/api` 目录运行测试：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_parsing.py tests/test_parsing_languages.py tests/test_ast_chunker.py tests/test_ast_chunk_spike.py -q
.\.venv\Scripts\python.exe -m pytest -q
python -m uv lock --check --offline
```

本次完整测试日志保留在 [backend-tests.log](../output/stage3-task3/backend-tests.log)。日志采集首次读取 Windows CP936 字节时产生了 UTF-8 解码错误；pytest 已完成且全套通过，随后按正确编码保留了 UTF-8 日志，没有把这个日志读取问题当成后端测试失败。评测和构建日志同在 `output/stage3-task3/`。

## 当前未实现

没有把 AST Chunker 接到 Indexer、Embedding 或 Qdrant，没有重新索引；Qdrant 里的旧 Vector 不会因 Parser/Chunker 新增就变成 AST Vector。新的 HTTP Chunk Preview、检索 metadata 展示、Context Builder、Code Chat、Citation Validator 和 Issue Analyzer 尚未实现。

没有 Hybrid Search、Reranker、Query Expansion 或 Agent 循环，也不会让模型自动修改仓库。现有检索仍是 Stage 2 Dense Retrieval。Vue 暂未引入 script/template 的混合 AST，现有 Scanner 不支持 `.jsx/.tsx/.mjs`，本次没有扩张文件类型范围。

## Bad Case / 风险

- **同一行相邻方法/函数**：byte 范围可以区分，但完整行引用会把邻居内容混在一起。本次保守地整文件 fallback，Symbol 留空；已有 Java 重载、JS 双函数、TS 双 alias 用例。
- **一个 Node 过大**：AST 保留 Symbol 的同时按完整行切片；分片未必独立编译，检索命中一个片段也不能证明整个方法行为。后续 Context Builder 应明确片段范围。
- **匿名或动态结构**：文本不会丢弃，但无法安全建立具名 owner 时不创造 Symbol。词法作用域不是类型分析，仍不适合据此声称跨文件调用图完整。
- **降级后资源超限**：fallback 不是无限容错；超过字符/文件/数量预算时 failed 可见，CLI 非零退出。任务 4 的整个仓库预算和写入前准备仍须保留。
- **敏感命名过滤已知缺口**：Task 1 发现 credentials/secrets 命名规则仍需最小修复，安排在 Task 4 扩大索引前处理。没有因为 AST 支持而重新纳入被 Scanner 忽略的文件。
- **在线持久化核验受限**：本次 Qdrant 未能连接，只读检查记录 `URLError`；因此没有声称当前在线集合再次与 Task 2 指纹一致。本任务没有启动服务或执行持久化写入。

## 下一任务

**Task 4：AST Indexing 与 Re-index**。先补齐已发现的敏感文件过滤，再接通 Scanner → AST Chunker → Embedding → Qdrant，升级 Payload 和集合 chunking version 身份，显式重新索引真实 demo 仓库，验证旧数据替换、稳定数量与真实统计。Task 3 到此停止，等待“继续”。
