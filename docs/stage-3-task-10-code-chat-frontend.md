# Stage 3 / Task 10：Code Chat 前端

## 本次完成

新增第三个工作区 **Code Chat · 仓库问答**，可选择或输入已索引 Repository、设置 TopK、提交 Question。页面展示真实 Answer、Sources、Path、Symbol、Lines 和带原行号的 Code Preview；第一条引用默认展开，长代码在预览内部横向滚动。

完成等待状态、停止等待、清空、按原参数重试、空证据提示、固定错误提示和移动端布局。每次提问独立检索，最多保留 20 条浏览器内存记录。原 LLM 工作区和 Retrieval Debug 仍可切换使用。

后端响应补充原问题回显和已校验引用的代码预览；Citation 仍为原来的六个字段，模型仍只生成 answer/source_ids。真实浏览器使用既有 Ollama 与 Qdrant，没有替换模型、重新索引或新增项目依赖。

完整记录：[机器验收结果](D:/aaaagent开发文件/RepoPilot/docs/stage-3-task-10-verification.json)、[桌面截图](D:/aaaagent开发文件/RepoPilot/output/playwright/task10-desktop.png)、[手机截图](D:/aaaagent开发文件/RepoPilot/output/playwright/task10-mobile.png)、[手机代码预览](D:/aaaagent开发文件/RepoPilot/output/playwright/task10-mobile-preview.png)。本次完成后停在 Task 10。

## 修改文件

| 文件 | 职责 |
| --- | --- |
| [CodeChat.vue](D:/aaaagent开发文件/RepoPilot/apps/web/src/components/CodeChat.vue) | 简单问答布局、引用展开、行号与代码文本展示、输入/等待/错误状态 |
| [code-chat.ts Store](D:/aaaagent开发文件/RepoPilot/apps/web/src/stores/code-chat.ts) | 请求参数快照、20 条内存历史、180 秒超时、取消、清空、重试与旧响应隔离 |
| [code-chat.ts API](D:/aaaagent开发文件/RepoPilot/apps/web/src/api/code-chat.ts) | 一次 POST、AbortSignal、JSON 解码与固定 HTTP 错误映射 |
| [code-chat-contract.ts](D:/aaaagent开发文件/RepoPilot/apps/web/src/api/code-chat-contract.ts) | 校验回显、Repository、IDs、引用/预览顺序、范围和诊断字段；按 Python 换行规则分行 |
| [code-chat.ts Types](D:/aaaagent开发文件/RepoPilot/apps/web/src/types/code-chat.ts) | Request、Response、Citation、Source 和 Turn 的 TypeScript 类型 |
| [App.vue](D:/aaaagent开发文件/RepoPilot/apps/web/src/App.vue) | 第三个工作区、`#code-chat` 入口、当前生成模型标签 |
| [style.css](D:/aaaagent开发文件/RepoPilot/apps/web/src/style.css) | 问答区域与桌面/手机布局；代码内部滚动 |
| [code_chat.py](D:/aaaagent开发文件/RepoPilot/services/api/app/schemas/code_chat.py) | CodeChatSource DTO、question 回显、预览与 Citation 对齐约束 |
| [service.py](D:/aaaagent开发文件/RepoPilot/services/api/app/rag/service.py) | 仅从当前 Context Source Map 取出已引用、已校验片段 |
| [test_code_chat_preview.py](D:/aaaagent开发文件/RepoPilot/services/api/tests/test_code_chat_preview.py) | 新增 11 项预览、截断、空证据与损坏响应检查 |
| [contract tests](D:/aaaagent开发文件/RepoPilot/apps/web/tests/code-chat-contract.test.mjs)、[store tests](D:/aaaagent开发文件/RepoPilot/apps/web/tests/code-chat-store.test.mjs) | 新增 19 项前端契约/状态检查；配套 fixture 和 TS 加载器不调用真实模型 |
| [README.md](D:/aaaagent开发文件/RepoPilot/README.md) | 更新完成进度、入口、响应契约和运行边界 |

另新增本文件、机器记录、[独立复核脚本](D:/aaaagent开发文件/RepoPilot/output/stage3-task10/verify.py) 和 `output/stage3-task10` / `output/playwright/task10-*` 验收证据。

对 443 个原有文件逐个核对哈希：仅 README、App.vue、style.css、RAG Service、Code Chat Schema 五个原有文件发生预期修改；另外 438 个保持原样。包括环境文件、锁文件、原有测试、Context Builder、Citation Validator、Prompt/输出检查、演示源码、评测数据和 Task 1–9 记录。

## 调用链 / 数据流

```text
Repository + Question + TopK
→ 前端参数校验 → 当前 Turn 参数快照
→ 一次 POST /api/code-chat
→ 一次仓库限定 Retrieval → Context Builder
→ Citation Validator 前置校验
→ 空 Context：固定证据不足回答，不调用生成模型
→ 非空 Context：一次结构化生成 answer / source_ids
→ Citation Validator 映射 → 既有有限敏感输出检查
→ 从同一 Context Map 取 cited Source content / language
→ Response：question + answer + citations + sources
→ 前端响应契约校验 → Pinia Turn → 转义文本与行号预览
```

Source Preview 与 Citation 按 source_id 一一对应，保持模型选择顺序。预览取的是 Context 实际发送的片段，发生截断时只显示截断后的行；不会重新检索来寻找“相似代码”，也不会根据模型给出的路径读取文件。当前 API 新增 `question` 与 `sources` 字段，严格限制旧响应字段的外部消费者需要适配。

UI 展示 Answer 原文，使用 Vue 文本插值；不解析 HTML、Markdown 链接或伪造 Source 标记。Citation 的路径/符号/行号作为结构化数据展示，不能从 Answer 或 Code Preview 文本中添加引用。

取消和清空会中止前端 fetch 并解除本次 Controller。回调先检查 Controller 身份：清空或新提问后到达的旧结果不能改变历史、当前等待状态或新回答。停止等待只约束浏览器端，后端已启动的模型生成仍可能继续。

## 我必须理解的知识

1. **代码预览必须沿用当前证据。** `CodeChatService.answer` 输入本次 Request，输出带预览的 Response。它在 Citation Validator 通过后从 Context Map 取内容，因此 Path/Lines 与内容属于同一片段。再次 Retrieval 可能拿到不同片段；直接读磁盘可能与索引版本不一致，破坏本次证据对应关系。

2. **类型声明不能校验网络 JSON。** `parseCodeChatResponse` 输入 unknown 和原 Request，验证 Repository/Question/TopK、唯一 IDs、Citation/Source 顺序、行数及诊断字段，然后输出明确的 Response。发现异仓库或损坏范围会拒绝整个答案。TypeScript 类型在运行时消失，直接强制转换无法发现这些问题。

3. **前端历史不等于多轮模型会话。** Store 输入当前参数，为每个 Turn 保存快照；重试用原参数，最多保存 20 条。每次网络请求仍只有当前问题，不发送历史。取消后的回调必须检查请求身份，否则旧回答可能恢复已清空记录或结束新请求的等待状态。刷新页面会丢失历史。

4. **显示仓库文字不能赋予它执行能力。** CodeChat.vue 输入已校验 DTO，用文本插值输出 Answer 和逐行代码。`<script>`、`<img onerror>`、`read_file`、伪造 S99 都保持文字；不使用 v-html，也不创建模型提供的链接。受控浏览器样例验证没有脚本/图片/链接节点和执行标记。以后加入 Markdown/链接要另行建立渲染边界。

5. **行号与答案质量分别验证。** `sourceLines` 输入字符串，按 Python `splitlines()` 的换行类别分割，保留空行/缩进，以 Citation.start_line 加偏移输出行号。损坏行数会被契约拒绝，截断保留实际范围；这仍无法验证模型解释是否正确。真实回答只给方法名，需要人结合代码核对。

## 真实数据示例

浏览器访问 `http://localhost:5173/#code-chat`，Repository 为 `demo-repository`，TopK=5，Question 为：

> 退款服务在哪里校验订单编号，然后把退款金额转回钱包？

实际模型为 `qwen2.5:0.5b`；Embedding 为 `qwen3-embedding:0.6b`，1024 维、Cosine。实际返回 Answer：**`RefundService.refund`**，引用如下：

```json
{
  "source_id": "S1",
  "repository": "demo-repository",
  "path": "backend/service/RefundService.java",
  "symbol": "RefundService.refund",
  "start_line": 12,
  "end_line": 18
}
```

页面 Code Preview 对应的真实 7 行如下，行号由后端 Citation 的 12 起算；第 17 行调用钱包服务：

```java
    public void refund(String orderId, String userId, BigDecimal amount) {
        if (orderId == null || orderId.isBlank()) {
            throw new IllegalArgumentException("Order ID is required");
        }
        // Fixture workflow: credit the refunded amount to the user's wallet.
        wallets.credit(userId, amount);
    }
```

此次 Retrieval 得到 5 个块，Context 为 5 个 Source、4110 字符，预算 16000；模型只选择 S1，因此 API/页面只返回一条 Citation 与一个 Preview。详情：[首轮真实响应](D:/aaaagent开发文件/RepoPilot/output/stage3-task10/response-refund.json)、[最终页面对应响应](D:/aaaagent开发文件/RepoPilot/output/stage3-task10/response-refund-final.json)、[对应 Context](D:/aaaagent开发文件/RepoPilot/output/stage3-task10/browser/context-004.json)、[实际生成消息和 HTTP 结果](D:/aaaagent开发文件/RepoPilot/output/stage3-task10/browser/generation-003.json)。回答没有改写或补充成更完整的解释。

输入没有索引的 `stage3-task10-not-indexed`，问题“这个仓库的退款函数在哪里？”，真实 API 返回：

```json
{
  "answer": "根据当前索引到的代码，我没有找到足够证据回答这个问题。",
  "source_ids": [],
  "citations": [],
  "sources": [],
  "status": "insufficient_evidence",
  "model": null,
  "generation": { "called": false, "latency_ms": 0.0 }
}
```

这是原始响应的字段节选，完整文件为 [response-empty.json](D:/aaaagent开发文件/RepoPilot/output/stage3-task10/response-empty.json)。Retrieval 得到 0 个块，empty_reason 为 no_results；页面显示证据不足与 Sources 0，不会向 LLM 请求补造代码。见 [空证据截图](D:/aaaagent开发文件/RepoPilot/output/playwright/task10-empty.png)。

本次录制服务共收到 **4 次真实 Code Chat、3 次真实生成 HTTP**：两次退款问题、一次未索引仓库，以及一次浏览器测试脚本首次失败后释放的 `controlled pending question`。最后一条返回“序列化仓库数据”和五个 Citation，内容质量不合格，单独保留在 [response-controlled-pending-real.json](D:/aaaagent开发文件/RepoPilot/output/stage3-task10/response-controlled-pending-real.json)，没有算作正常问题成功案例。见下方 Bad Case。生成请求均只有 answer/source_ids Schema，没有 tools/tool_choice。

502、异仓库响应、恶意 HTML/Source 与延迟响应的 UI 检查使用**明确标记的受控浏览器 fixture**，不作为真实模型推理或防护成功率。记录：[browser-controls.json](D:/aaaagent开发文件/RepoPilot/output/stage3-task10/browser-controls.json)、[受控错误截图](D:/aaaagent开发文件/RepoPilot/output/playwright/task10-error.png)、[受控恶意文本截图](D:/aaaagent开发文件/RepoPilot/output/playwright/task10-untrusted-text.png)。

## 验收方法

现有 Qdrant 和 Ollama 保持运行，从仓库根目录启动：

```powershell
Set-Location 'D:\aaaagent开发文件\RepoPilot'
.\Start-RepoPilot.ps1 -NoBrowser
```

打开 `http://localhost:5173/#code-chat`，选择 `demo-repository`、TopK=5，点击示例问题后提问。应看到实际 Answer、S1、RefundService.refund、Lines 12–18 和展开的代码；也可点击引用收起/展开。回答文本以当前模型实际输出为准，引用是否有效由后端和前端校验。

输入新仓库名后按 Enter 确认，再提问；未索引仓库应显示证据不足和 Sources 0。空问题应只显示本地校验，Network 中没有 POST。生成中可停止或清空，清空后旧响应不能恢复记录。手机宽度 390px 下，代码可以内部滚动，页面不应横向溢出。

接口仍为 `POST /api/code-chat`；示例请求：

```json
{"repository":"demo-repository","question":"退款服务在哪里校验订单编号，然后把退款金额转回钱包？","top_k":5}
```

运行测试与构建：

```powershell
Push-Location 'D:\aaaagent开发文件\RepoPilot\services\api'
.\.venv\Scripts\python.exe -m pytest tests -q
Pop-Location
Set-Location 'D:\aaaagent开发文件\RepoPilot'
.\services\api\.venv\Scripts\python.exe -m pytest evaluation/tests -q
Push-Location 'D:\aaaagent开发文件\RepoPilot\apps\web'
npm.cmd test
npm.cmd run build
Pop-Location
```

| 检查 | 实际结果 |
| --- | --- |
| 后端完整回归 | 947 collected；941 passed、6 skipped、2 个原有 warning；本次新增 11 项 |
| 前端测试 | 28 passed；本次新增 19 项，原检索 9 项继续通过 |
| 评测测试 | 42 passed |
| TypeScript 与生产构建 | vue-tsc / Vite 通过；1620 个模块 |
| 真实浏览器流程 | 5 项通过：空输入无请求、真实空证据不生成、真实退款引用/预览、桌面/手机无外层横向溢出 |
| 受控浏览器检查 | 8 项通过：安全 502 无部分结果、异仓库拒绝、文本转义、伪造 Source 不成引用、清空隔离晚到响应、防重复提交、原工作区可切换、手机无外层溢出 |
| 证据复核 | 四条真实响应通过 DTO 校验；逐一重建 Context、重放 Citation Validator、预览逐字符匹配；检索内容核对本地原行 |
| 历史与生产索引 | 443 原文件仅 5 个预期修改；所有原集合 Vector/Payload/Metadata 完全一致，生产仍 17 个点 |
| 录制服务 | 正常停止并关闭 SDK Client；恢复普通 API 供页面复核 |

6 个 skip 和两项 warning 延续原有外部集成 opt-in、Windows 文件符号链接与框架弃用条件，没有降低原测试要求。[后端 JUnit](D:/aaaagent开发文件/RepoPilot/output/stage3-task10/backend-tests.xml)、[前端 JUnit](D:/aaaagent开发文件/RepoPilot/output/stage3-task10/frontend-tests.xml)、[评测 JUnit](D:/aaaagent开发文件/RepoPilot/output/stage3-task10/evaluation-tests.xml) 保存实际结果。

运行独立复核，只读当前 Qdrant 和保存证据，不调用模型或索引仓库：

```powershell
Set-Location 'D:\aaaagent开发文件\RepoPilot'
.\services\api\.venv\Scripts\python.exe output/stage3-task10/verify.py
```

历史 Task 9 文档保留当时响应契约；其旧脚本只接受当时字段，不应把本次新增字段后的旧脚本失败当作模型或索引回归。当前使用 Task 10 复核脚本。

## 当前未实现

未实现 Issue Analyzer、Streaming RAG、多轮上下文、历史持久化、索引仓库自动发现、完整 IDE、语法高亮或按路径加载完整文件。仓库选择器仅保存本页输入过的名字，不是 Qdrant 仓库列表。

没有新增 Hybrid、BM25、Reranker、查询扩展、Agent 工具循环或仓库修改能力。没有自动答案语义验证、完整 Secret/DLP 检测或通用 Prompt Injection 防御保证。

## Bad Case / 风险

**真实答案过短：** 两次退款问题都仅返回 `RefundService.refund`，没有解释空订单校验和钱包入账。引用坐标与预览正确，答案质量仍不足。UI 如实显示，没有替模型补写解释。

**前端测试脚本首轮失败：** 脚本在等待状态仍通过旧按钮名“提问”定位，但按钮已变为“正在回答”，因此定位超时。清理路由时一个挂起的请求被释放到真实 API；该请求和不相关答案已保存。随后改为定位表单 submit，并等待两条路由请求实际到达；最终八项受控检查通过。此记录解释额外的第三次生成调用，没有把测试脚本失败隐藏为一次全通过运行。

**停止不保证节省推理：** AbortController 可以停止浏览器等待、阻止旧响应写回；后端已提交的模型调用可能继续消耗资源。当前没有服务器任务取消协议。

**预览反映索引证据：** 预览与当前 Context 一致，不能证明磁盘文件一直新鲜；本次本地行内容已核对。普通源码中的硬编码敏感值不被既有文件名过滤完整发现，Code Preview 没有新增内容级 DLP。既有回答检查也有误报/漏报范围。

**页面安全有明确范围：** 当前纯文本展示通过受控恶意文本检查；以后增加 Markdown、自动链接、文件读取或工具执行，需要重新验收相应入口。受控 UI fixture 不能证明真实模型抗注入能力。

## 下一任务

Task 11：Issue Analyzer。以 Issue → Retrieval → Context → Structured Output → Validated Sources 流程返回任务摘要、相关代码、可能调用链、分析和修改建议。本次不进入 Task 11，等待下一条“继续”。
