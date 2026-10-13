# Stage 3 / Task 2：Tree-sitter 最小 Parser Spike

日期：2026-10-04，Asia/Shanghai。Task 2 已完成；未进入 Task 3。

## 本次完成

独立 Parser Layer 已能解析真实 `demo-repository` 的 Java 源码，返回类、构造器、方法的真实节点类型、词法 Symbol、父 Symbol、源码行范围和字节位置。没有接入 Indexing、修改 Simple Chunker、升级 Qdrant Payload 或执行重新索引。

新增依赖并锁定本次实际安装版本：

| 依赖 | 声明范围 | 本次锁定/安装 |
| --- | --- | --- |
| `tree-sitter` | `>=0.26.0,<0.27.0` | **0.26.0** |
| `tree-sitter-java` | `>=0.23.5,<0.24.0` | **0.23.5** |

使用现有 `python -m uv add` 安装两个官方 Python Wheel，不需要安装 Node Tree-sitter CLI、Java 编译器、其他语言 Grammar 或 LangChain。Java Grammar 实测 ABI 为 14，运行库支持 13–15；本次组合可正常解析。移除两个新增依赖条目和包记录后，依赖声明与锁文件的原有内容指纹完全一致，确认没有升级现有依赖。`uv lock --check --offline` 通过。

### 验证结果

| 检查 | 实际结果 |
| --- | --- |
| 新增 Parser Tests | **32 passed** |
| 完整 Backend Tests | **503 collected；498 passed、5 skipped、2 warnings**；退出码 0 |
| Stage 2 Evaluation Unit Tests | **32 passed**；退出码 0 |
| Frontend Build | `vue-tsc -b && vite build` 通过；1615 个模块 |
| 真实退款文件 | 类、构造器、方法均识别，行号与源码一致 |
| 真实钱包文件 | 类、`credit`、`balanceOf` 均识别，行号与源码一致 |
| Vue CLI 请求 | 受控提示未注册 Parser，退出码 1；没有虚构 AST 结果 |
| 数据保留 | Scanner、Chunker、Indexer、Retrieval、前端、样例、数据集、旧报告和 `.env` 未改变 |
| Qdrant 只读核验 | 8 个代码 Point、向量、Payload 和集合配置与 Task 1 快照一致 |

5 个跳过项与 Task 1 相同：四个外部服务集成测试和 Windows 文件符号链接权限测试。为遵守本次不写 Qdrant 的范围，没有启用那些会创建/删除测试集合的集成测试。2 个原有依赖弃用警告保持。本次没有重新运行 12 条真实检索题，因为 Parser 尚未接入查询/索引；没有把 Task 1 的评测当成本次新运行。

首轮完整测试出现 **2 failed、495 passed、5 skipped**，两个失败都来自仅 CR 换行的注释边界，实际问题与修复见 Bad Case。修复后全套通过；随后补充了枚举常量匿名类体的遗漏保护和回归用例，最终结果为上表的 498 passed。首轮失败日志保留在 `output/stage3-task2/backend-tests.log`，最终日志为 `backend-tests-final.log`。

## 修改文件

| 文件 | 职责 |
| --- | --- |
| [pyproject.toml](../services/api/pyproject.toml)、[uv.lock](../services/api/uv.lock) | 增加并锁定 Tree-sitter 核心/Java Grammar；保留其他依赖 |
| [parsing/base.py](../services/api/app/parsing/base.py) | `CodeParser` Protocol、`ParsedSource`/`SyntaxSymbol`，以及明确的解析/不支持语言/语法错误类型 |
| [parsing/registry.py](../services/api/app/parsing/registry.py) | 语言识别、元数据一致性检查、选择 Parser；本 Task 仅注册 Java |
| [parsing/tree_sitter_parser.py](../services/api/app/parsing/tree_sitter_parser.py) | Tree-sitter 解析、命名声明遍历、父作用域、字节位置到原文行号转换 |
| [scripts/parser_spike.py](../services/api/scripts/parser_spike.py) | 可复跑的只读 CLI，用现有 Scanner 选择真实文件；输出 JSON，无网络模型调用或索引写入 |
| [tests/test_parsing.py](../services/api/tests/test_parsing.py) | Registry、真实文件、UTF-8/换行、嵌套/重载、匿名体和失败边界 |
| [stage-3-task-2-verification.json](stage-3-task-2-verification.json) | 实际版本、真实节点、边界实验、测试统计和保留证据 |
| 本文 | Task 2 数据流、学习说明、验收和后续边界 |

既有 141 个受保护文件中，只改动了依赖声明和锁文件；Parser、CLI、测试与本次文档为新增文件。工作区仍没有 Git 元数据，不能声称这些变更已提交；本次使用文件指纹核实修改范围，没有初始化 Git。

## 调用链 / 数据流

```text
parser_spike.py --repository demo-repository --path ...java
→ 现有 RepositoryScanner：安全读取、UTF-8 解码、忽略规则
→ 已存在的 RepositoryFile（repository/path/language/content）
→ ParserRegistry：扩展名与 language 一致性 → 选择 Java Parser
→ UTF-8 编码 → 解析缓冲区准备
→ tree-sitter Parser + Java Language → program 语法树
→ root.has_error 检查 → 按命名节点遍历
→ class/constructor/method 等声明 → name 字段 → 词法父作用域
→ 原文 UTF-8 字节范围 → RepoPilot 行号
→ ParsedSource + SyntaxSymbol → CLI JSON
```

Parser 接收已经读取的 `RepositoryFile`，自身不打开文件、不调用模型、Embedding 或 Qdrant，也不生成 CodeChunk。每次 `parse()` 新建 native Parser，避免不同请求共享可变解析状态；Registry 复用已加载的 Language 配置。

不支持语言抛出 `UnsupportedLanguageError`。Tree-sitter 能在语法错误时恢复部分树，所以不能把“返回了 Tree”视为解析成功：本实现发现 `root.has_error` 就抛出 `SourceSyntaxError`，不给调用方部分 Symbol。错误信息不包含完整源码。Task 3 再由 Chunker 捕获可降级错误并选择 Simple Fallback，本 Task 未实现 Fallback。

## 我必须理解的知识

| 知识 | 它是什么、为什么 RepoPilot 需要 | 当前文件和输入 → 输出 | 位置 |
| --- | --- | --- | --- |
| Tree-sitter / Grammar / Parser | Tree-sitter 是解析运行库，Java Grammar 描述 Java 语法，Parser 用它读源码；不需要让 LLM 猜方法边界 | `tree_sitter_parser.py`：UTF-8 bytes + Java Language → Tree | 未来索引时，切块之前 |
| AST / Syntax Node | 将平面的源码表示为语法层级。本实现使用 Tree-sitter 语法树的命名声明节点，作为 AST-aware Chunking 的基础 | `tree_sitter_parser.py`：program → class_declaration → method_declaration 等节点 | Parser 内部遍历 |
| Symbol / parent_symbol | 把声明名称与词法父作用域组合起来，知道 `refund` 属于 `RefundService`；不是编译器完成的名称/类型解析 | `base.py`、`tree_sitter_parser.py`：name 字段 + 父作用域 → `RefundService.refund` / `RefundService` | 从节点投影为元数据 |
| 字节位置 / 行号 | UTF-8 字节数不同于字符数；节点结束字节不包含末尾，用户行号包含两端。正确转换才能用于后续引用 | `tree_sitter_parser.py`：`[start_byte,end_byte)` → 原文的 1-based 闭区间 | 构造 SyntaxSymbol 时 |
| Parser Registry / 降级边界 | 先识别语言，再选择已注册 Grammar；未支持或解析失败要让上层明确知道，而不能伪造结果 | `registry.py`：RepositoryFile → Java Parser 或受控错误 | 语言识别与解析之间；Fallback 是下一 Task |

Tree-sitter 的 Tree 并不是完整的编译器语义模型。能找到一个方法声明，并不意味着已解析其调用目标、类型、继承关系或整条业务调用链。Java 的“函数”在本次实验中表现为方法声明；本次没有 Python 顶层函数解析。

### 行号约定

- `start_byte/end_byte`：原始解码文本 UTF-8 编码后的字节范围，0-based、右端不包含。
- `start_line/end_line`：原始文本的行号，1-based、包含两端，与 Stage 2 `splitlines()` 约定一致。
- `start_point/end_point`：Tree-sitter 解析缓冲区中的 0-based 行/字节列，保留给调试查看，不直接充当 Citation 行号。
- CRLF 原样送入 Parser。孤立 CR 在解析缓冲区中被等长替换成 LF，原始源码、字符内容和字节偏移不变；行号映射仍来自原文。

结束行按 `end_byte - 1` 所在原文行计算，避免节点恰好在下一行开头结束时多算一行。未来 AST Chunker 还要从原文按整行取内容；本 Task 的 SyntaxSymbol 不是 CodeChunk，不能把 native node text 与整行切片混为一谈。

## 真实数据示例

实际解析文件：[RefundService.java](../workspaces/demo-repository/backend/service/RefundService.java)。根节点为 `program`，得到三个命名声明：

| Node Type | Symbol | Parent Symbol | 原文行范围 | UTF-8 字节范围 |
| --- | --- | --- | --- | --- |
| `class_declaration` | `RefundService` | null | **5–19** | `[53,553)` |
| `constructor_declaration` | `RefundService.RefundService` | `RefundService` | **8–10** | `[134,217)` |
| `method_declaration` | `RefundService.refund` | `RefundService` | **12–18** | `[223,551)` |

实际方法节点：

```json
{
  "node_type": "method_declaration",
  "name": "refund",
  "symbol": "RefundService.refund",
  "parent_symbol": "RefundService",
  "start_line": 12,
  "end_line": 18,
  "start_byte": 223,
  "end_byte": 551,
  "start_point": [11, 4],
  "end_point": [17, 5]
}
```

第二个真实文件 [WalletService.java](../workspaces/demo-repository/backend/service/WalletService.java) 得到 `WalletService`（7–23）、`WalletService.credit`（10–18）、`WalletService.balanceOf`（20–22）。两份 JSON CLI 输出与当前 Parser 重新运行结果完全一致，源码指纹也与原冻结样例一致。

目前 Qdrant 的退款块仍是旧 Simple Chunk 的 **1–19 行**，不是新方法节点的 12–18 行。没有把新 Parser 元数据伪装成已写入向量库的结果；没有 AST Chunk、Context、Source ID 或 Citation 的运行结果。

## 验收方法

本机依赖已经安装。Parser Spike 不需要启动 FastAPI、Ollama 或 Qdrant，不新增 API。

仓库根目录执行：

```powershell
$env:PYTHONIOENCODING = 'utf-8'
& .\services\api\.venv\Scripts\python.exe services/api/scripts/parser_spike.py
& .\services\api\.venv\Scripts\python.exe services/api/scripts/parser_spike.py `
  --path backend/service/WalletService.java
```

观察 `root_node_type=program`、真实节点/Symbol/父 Symbol、上述行号/字节范围和安装版本。可以直接对照 Java 文件。

确认 Vue 暂不支持：

```powershell
& .\services\api\.venv\Scripts\python.exe services/api/scripts/parser_spike.py `
  --path frontend/src/RefundPanel.vue
```

应安全提示 `No AST parser is registered for this language.`，退出码 1，不生成假的 Symbol。

在 `services/api` 执行离线测试：

```powershell
& .\.venv\Scripts\python.exe -m pytest tests/test_parsing.py -q
# 新终端中保持四个 REPOPILOT_*_INTEGRATION 标志未启用
& .\.venv\Scripts\python.exe -m pytest -rs
python -m uv lock --check --offline
```

其他检查：根目录运行 `& .\services\api\.venv\Scripts\python.exe -m pytest evaluation/tests -q`；`apps/web` 目录运行 `npm.cmd run build`。

其他机器需要安装更新后的锁文件时，在 `services/api` 使用 `python -m uv sync --locked --dev`。这是环境准备命令，本机已经完成，不需再次安装或升级依赖。

## 当前未实现

只有 Java Grammar。Python、JavaScript、TypeScript、Vue 的 Parser 尚未注册。当前 Registry 可选择/拒绝语言，但没有为了语言数量增加未验证 Grammar。

没有 AST-aware Chunking、Fallback Chunking、Chunk 大小控制、Chunking Version、索引解析统计或 AST Re-index；原 CodeChunk 数据模型完全保持。没有 RAG、Context Builder、Citation Validator、Issue Analyzer、Hybrid Search、Reranker 或仓库 Agent。

没有 Symbol 的类型解析、重载签名标识、跨文件引用解析或调用图。匿名对象/枚举常量类体和 lambda 体明确跳过，避免把其中的方法错误标成外层类的方法。

## Bad Case / 风险

1. **已修复的真实 Grammar 边界观察**：边界实验输入 `// comment\rclass Wallet {\r  void credit() {}\r}\r`，直接传给 Java Grammar 的 Tree 是 `(program (line_comment))`，且 `has_error=false`，声明被注释吞掉。该输入是明确标注的人工边界样例，不冒充真实仓库文件。先前两项 CR 测试因此失败。现在孤立 CR 在解析缓冲区等长转成 LF，得到 `Wallet` 的 2–4 行和 `Wallet.credit` 的第 3 行，原字节切片仍是 `void credit() {}`。LF、CRLF、CR、中文和末尾换行组合均通过测试。
2. **受控限制**：把 U+2028/U+2029 用作 Java 空白分隔符时，此 Grammar 返回错误节点。当前拒绝部分 AST，抛出安全 `SourceSyntaxError`；Task 3 应触发文件级 Simple Fallback。没有通过偷偷修改源内容掩盖语法错误。
3. **同名不等于唯一身份**：重载方法可以同为 `Wallet.credit`；一行内有多个方法时也可共享行范围。测试确认其字节范围不同。Task 3 不能只按 Symbol 或 Symbol+行号去重/生成 ID，需要保留源码范围、内容和版本。
4. **嵌套与匿名范围**：局部命名类使用词法路径，例如 `Outer.run.Local.work`；匿名类/lambda 不产生虚构名字。本次是有限的 Java 命名声明提取，不能声称已覆盖所有语言和语义场景。
5. **解析成功不等于代码可编译或逻辑正确**：Syntax Tree 只提供语法结构；缺依赖、类型错误、并发问题或业务缺陷需要之后独立分析。
6. **生产接入尚未发生**：CLI 先扫描仓库只是本次实验的读取路径。正常查询不会运行该脚本；Task 3/4 才会明确索引期的 Parser/Fallback/资源预算。本 Task 不改变仓库过滤或敏感文件规则，Task 1 的已有风险仍保留。

## 下一任务

**Task 3：AST-aware Chunker。** 在当前 Parser 基础上将命名声明转为可索引 CodeChunk，设计大类/大方法的切片、准确整行内容、稳定 ID 和 Symbol 元数据；实现解析失败/不支持语言的 Simple Fallback，按计划实测并加入核心语言支持。

Task 3 仍先验证 Chunk，不提前接 Qdrant Re-index（属于 Task 4）。

**Task 2 到此停止，等待下一条指令。**

参考：[官方 Python API](https://tree-sitter.github.io/py-tree-sitter/)、[Node API](https://tree-sitter.github.io/py-tree-sitter/classes/tree_sitter.Node.html)、[Java Grammar](https://github.com/tree-sitter/tree-sitter-java)、[Tree-sitter PyPI](https://pypi.org/project/tree-sitter/)、[Java Grammar PyPI](https://pypi.org/project/tree-sitter-java/)。实际版本、节点与边界结果以本项目验证记录为准。
