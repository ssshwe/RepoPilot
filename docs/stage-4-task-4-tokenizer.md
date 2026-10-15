# Stage 4 / Task 4：代码专用 Tokenizer

已实现 [`CodeTokenizer`](../services/api/app/retrieval/tokenizer.py)，版本为 `code-lexical-v1`。索引文本与查询共用同一套纯函数规则，不访问模型、Qdrant 或文件系统。本任务只交付词法表示，不启用 BM25 或修改生产 Dense 排名。

## 输入、输出与规则

`tokenize(text)` 返回有序 `list[str]`；`tokenize_query(query)` 先复用现有查询的非空、类型和 8192 字符验证，再调用同一核心；`tokenize_source(SourceMetadata)` 将已验证的 `content`、`path`、可选 `symbol` 以换行拼接后调用核心。源码来自冻结索引，不读取或重新切分当前文件。

规则按以下顺序运行：

1. 先做 Unicode NFKC 规范化；保留大小写到命名拆分结束，最后 casefold。报告记录 Python Unicode 数据版本 `15.0.0`，运行环境变化会阻止旧报告复算。
2. 保留完整标识符，再拆分 camelCase、PascalCase、snake_case、字母/数字边界和缩写边界。连续大写部分保持为一段；没有词典去猜测 `XMLHTTP` 中的两个缩写。
3. 路径统一反斜杠为 `/`，保留完整路径、带后缀的文件名、目录/文件词及命名组成部分。Windows 盘符、UNC、绝对/相对路径和目录尾斜杠均作为词法文本处理，不解析或访问它们。文件后缀按 ASCII 字符识别，可停止在紧邻的中文问句前；空格或标点分开的路径不猜测拼接。
4. 每段连续汉字产生重叠的字符 bigram；只有一个汉字时保留 unigram。bigram 不跨越标点、Emoji、英文或数字。混合标识符保留整体，并分别处理其中的汉字段和其他字段。
5. 保留错误字符串中的词，不做停用词过滤、词干化、翻译或大小写敏感的 Exact 匹配。

| 输入 | 输出示例 |
| --- | --- |
| `RefundService` | `refundservice`, `refund`, `service` |
| `refund_order` | `refund_order`, `refund`, `order` |
| `HTTPServer` | `httpserver`, `http`, `server` |
| `getHTTPResponse2` | `gethttpresponse2`, `get`, `http`, `response`, `2` |
| `XMLHTTPRequest` | `xmlhttprequest`, `xmlhttp`, `request` |
| `refund订单Id` | `refund订单id`, `refund`, `订单`, `id` |
| `退款订单` | `退款`, `款订`, `订单` |
| `退款退款` | `退款`, `款退`, `退款`，保留两次词频 |
| `types.d.ts` | `types.d.ts`, `types`, `d`, `ts` |
| `Amount must be positive` | `amount`, `must`, `be`, `positive` |

`backend/service/RefundService.java` 输出完整路径 `backend/service/refundservice.java`、文件名 `refundservice.java`，以及 `backend`, `service`, `refundservice`, `refund`, `service`, `java`。两次 `service` 来自目录及标识符中的两个位置，都会保留。纯单词文件名已由组成词保留，不再额外重复一次。

同一标识符出现一次时，整体与派生词相同的内容只计一次；文本中重复出现的标识符仍重复计数。例如 `refund_refund` 为两个词 `refund_refund`, `refund`，而 `refund refund` 保留两次 `refund`。中文重叠 bigram 的重复也保留。这让未来 BM25 能使用词频，而不是只有一个全局去重集合。

默认输入上限 2,000,000 字符、输出上限 200,000 Tokens，NFKC 扩展后再次检查字符预算；超限抛出不包含源码的 `TokenizationLimitError`，不返回部分 Token。预算配置、来源字段及 Unicode 版本与分词版本一同进入 `specification()`。长串标点、连字符和目录路径有专项测试，避免失败的路径前缀被逐词重复扫描。

## 使用与复现

```python
from app.retrieval.tokenizer import CodeTokenizer

tokenizer = CodeTokenizer()
query_tokens = tokenizer.tokenize_query("RefundService 如何处理退款？")
document_tokens = tokenizer.tokenize_source(candidate.source_metadata)
versioned_rules = tokenizer.specification()
```

离线工具 [`verify_tokenizer.py`](../evaluation/stage4/verify_tokenizer.py) 使用 Task 3 保存的完整索引，验证其关联指纹及 Task 2 源标签，处理全部 129 个 Chunk、12 个历史与 40 个开发问题，并保存 21 个明确的输入/输出示例、完整 Token 序列、词频及来源指纹。20 个保留问题未处理；保留族的代码仍属于此前冻结的完整索引语料。

```powershell
# 输出新的时间戳文件；无需启动模型、API 或 Qdrant
& '.\services\api\.venv\Scripts\python.exe' evaluation/stage4/verify_tokenizer.py

# 离线核对本次最终报告
& '.\services\api\.venv\Scripts\python.exe' evaluation/stage4/verify_tokenizer.py `
  --verify-only evaluation/reports/stage4_tokenizer_20261015_task4_final.json

Push-Location services/api
& '.\.venv\Scripts\python.exe' -m pytest -q
Pop-Location
& '.\services\api\.venv\Scripts\python.exe' -m pytest evaluation/tests -q
```

支持 `--reference-report`、`--output`。已有输出拒绝覆盖；报告关联错误、实现文件或 Tokenizer/Unicode 版本漂移、Token/词频变更、漏掉查询都会被拒绝。最终报告为 [`stage4_tokenizer_20261015_task4_final.json`](../evaluation/reports/stage4_tokenizer_20261015_task4_final.json)，验收为 [机器核对文件](stage-4-task-4-verification.json)。开发中间收据归档在 `output/stage4-task4`，不作为最终报告。

## 2026-10-15 验证结果

- 新增 **72 项 Tokenizer 测试全部通过**；默认后端全套 **1128 通过、6 跳过**。跳过项包含五个需要显式启用的真实服务集成用例；本次纯词法模块没有重跑它们。两项既有依赖弃用警告仍存在。
- 新增 **13 项报告完整性测试**，评测套件共 **130 项通过**；覆盖输入/报告关联漂移、版本、Unicode 数据、词频、漏案与旧文件覆盖拒绝。
- 最终收据包含 **129 个 Chunk、52 个问题、21 个示例**，3870 个文档 Token、975 个查询 Token；文档/查询词表大小分别为 486 / 446，没有空 Token 文档或查询。
- 该次分词过程耗时保存在报告的 `tokenization_latency_ms`，属于一次离线样例运行，不作为服务延迟基准。模型、生成、Qdrant 调用均为零；独立保护核对仅只读访问 Qdrant，确认既有索引未变。
- 公共 API、前端、依赖锁、实际 `.env`、历史数据集和报告保持原样；没有重建任何索引。Task 1/2/3 的既有收据继续离线复算。

成功例：包含英文异常信息 `Cart must contain an item` 的代码与问题都保留 `cart`, `must`, `contain`, `an`, `item`，无需词形或翻译猜测；查询 `backend/service/A.java在哪里` 保留真实路径 Token，并将“在哪里”拆成 `在哪`, `哪里`。

边界例：“退款”和 `RefundService` 没有共同词，纯词法规则不提供跨语言语义；`XMLHTTPRequest` 只确定 `xmlhttp` 与 `request` 的边界，无法凭字符判断 `XML` / `HTTP`。仅有标点或 Emoji 的合法非空查询可能得到空词表，后续词法通道需要明确处理。已有 Dense 的 legacy 方法排序、调用链缺失等质量问题不由本任务改变。

## 下一任务

Task 5 将从已有 Chunk 创建共享词法索引快照，复用 `chunk_id`、内容、行号和本次版本化规则，管理持久化、仓库隔离及重建一致性。它还需要明确 ready / rebuilding / incomplete 状态。BM25、Exact、Hybrid 及页面接入仍留在后续任务。
