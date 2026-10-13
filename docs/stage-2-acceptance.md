# Stage 2 学习与验收：会找代码

Stage 2 / Task 10 已完成。当前流程是扫描受控仓库、按行切块、生成真实向量、
写入 Qdrant、对查询做向量检索、展示代码来源。检索结果还没有接入 LLM 回答。
完整文件地图、28 项验收清单和命令见 [Stage 2 文档](stage-2.md#task-10-final-tests-documentation-and-acceptance)。

## 实际验收结果

2026-10-04（北京时间）的默认后端测试：**466 通过、5 跳过**。
4 个跳过项是显式启用的真实服务集成测试，另行全部通过：首轮 3 通过，修正
Embedding 测试假设后定向复跑 1 通过。剩余 1 项因 Windows 无权创建文件符号链接
（WinError 1314）跳过。保留 2 条现有依赖弃用警告。
评测单元测试 **32 通过**，前端契约测试 **7 通过**，TypeScript/Vite 构建通过。

真实模型：`qwen3-embedding:0.6b`，实测向量维度 **1024**。
集合：`repopilot_code`，Cosine，**8 个真实 Point**。
固定的 12 条查询复跑得到：

| K | 命中问题数 | Hit@K | Macro Recall@K |
| ---: | ---: | ---: | ---: |
| 1 | 6/12 | 50.00% | 50.00% |
| 3 | 10/12 | 83.33% | 77.78% |
| 5 | 12/12 | 100.00% | 93.06% |

两条多文件问题的 Top5 仍漏掉 `WalletService.java`。这个手写小仓库仅有 8 个块，
不能据此宣称生产检索质量。原始结果在
[最终评测报告](../evaluation/reports/stage2_retrieval_20261004_task10.json)，
验证与真实请求在 [验收记录](stage-2-task-10-verification.json)。

## 25 个问题，结合当前代码回答

1. **Embedding 到底是什么？**
   模型把一段文本映射成有固定维度的数值向量。相似内容的向量通常更接近，
   但排序是否有用必须实测。它是推理结果，没有在本项目中训练模型。
   看 [`embedding/base.py`](../services/api/app/embedding/base.py) 的接口，
   [`provider.py`](../services/api/app/embedding/provider.py) 执行真实请求。

2. **Embedding 和 LLM 有什么区别？**
   当前 Embedding Provider 输出数值列表用于比较；聊天 Provider 输出文本、流、
   结构化结果或工具调用。分别使用 `EMBEDDING_*` 和 `LLM_*` 配置，不能借用聊天
   模型配置来建索引。对照 [`embedding/factory.py`](../services/api/app/embedding/factory.py)
   与 [`llm/factory.py`](../services/api/app/llm/factory.py)。

3. **为什么代码可以转换成 Vector？**
   代码包含命名、语法、注释和上下文，可以作为模型的文本输入。当前
   [`indexing/service.py`](../services/api/app/indexing/service.py) 发送 chunk 的源码内容。
   向量不是编译执行结果，也不能证明代码行为正确；不同模型的检索效果会不同。

4. **Embedding Dimension 是什么？**
   一个向量有多少个坐标。本次真实输出是 1024；调试 API 只显示其中最多 8 个。
   维度由响应测得，不能把这个模型换成另一个模型仍假定是 1024。
   看 [`embedding/validation.py`](../services/api/app/embedding/validation.py)。

5. **Cosine Similarity 是做什么的？**
   它用 `dot(a,b) / (norm(a) * norm(b))` 比较方向，范围为 [-1,1]，不是正确率。
   零向量不能计算有效余弦。当前算法在
   [`retrieval/similarity.py`](../services/api/app/retrieval/similarity.py)。
   同一个向量和自己比较为 1；同一句文本两次推理可能得到稍有差异的向量。

6. **Vector Database 为什么适合语义搜索？**
   它把数值相似度查询与持久化、TopK 和属性过滤结合起来。用户不必输入与源码
   完全一致的词，就能尝试找到相关代码。实际搜索仍会误排或漏排，需要评测。
   看 [`retrieval/vector_store.py`](../services/api/app/retrieval/vector_store.py)。

7. **为什么 RepoPilot 选择 Qdrant？**
   当前项目需要独立本地服务、持久化向量、源码 payload、Cosine 查询、repository
   过滤以及 Python 异步 SDK，这些都已经在当前实现中使用。阶段目标也明确选择
   Qdrant；本次没有做数据库选型性能比较。基础设施见
   [`infra/docker-compose.yml`](../infra/docker-compose.yml)。

8. **Collection 是什么？**
   命名的向量数据集合，规定向量维度与距离度量。当前代码集合为
   `repopilot_code`（1024/Cosine），历史基础设施实验集合是
   `repopilot_infra_smoke`（3 维、空集合），不能混用。
   创建与兼容性检查在 [`vector_store.py`](../services/api/app/retrieval/vector_store.py)。

9. **Point 是什么？**
   一条 `id + vector + payload` 记录，当前一块代码对应一条。真实退款块的 ID 是
   `67881397-a785-5ed6-ad27-40ce2d67e8e6`。稳定 UUID 由
   [`indexing/chunker.py`](../services/api/app/indexing/chunker.py) 的 chunk 标识逻辑产生，
   [`indexing/models.py`](../services/api/app/indexing/models.py) 定义 chunk 字段。

10. **Vector 和 Payload 有什么区别？**
    Vector 的坐标用于相似度比较；Payload 保存仓库名、文件路径、行号、语言和
    完整 chunk 源码等数据，便于过滤、展示和核验。仅有向量无法直接展示原代码。
    看 [`indexing/service.py`](../services/api/app/indexing/service.py) 的 payload 构造。

11. **为什么 Payload 要保存 repository？**
    防止不同仓库的代码混在一次结果里，也让重建索引只替换指定仓库。搜索和删除
    都必须应用这个条件；前端也检查响应仓库一致性。过滤见
    [`vector_store.py`](../services/api/app/retrieval/vector_store.py)，真实双仓库隔离见
    [`test_retrieval_integration.py`](../services/api/tests/test_retrieval_integration.py)。

12. **Metadata 为什么需要 path 和 line range？**
    用户必须能回到源文件验证结果。当前行号从 1 开始、包含两端，退款块是
    `backend/service/RefundService.java:1..19`。源码移动后旧索引可能失效，需重建。
    模型、schema 和切块设置还用于判断索引身份与来源。
    看 [`indexing/models.py`](../services/api/app/indexing/models.py)。

13. **Chunk 为什么不能无限大？**
    输入、内存和请求有预算；过大的内容也可能稀释局部逻辑。当前最多 8192 个
    Unicode 字符，按 60 行窗口，不是按模型 token 计数。
    超预算返回 413，不偷偷截断源码。看
    [`indexing/chunker.py`](../services/api/app/indexing/chunker.py)。

14. **Chunk 太小有什么问题？**
    可能拆开定义、条件和调用，丢失上下文，增加向量数量；同一文件的多个块还会
    占用 TopK 名额。`RefundService.java` 在 10/2 窗口下有 1..10、9..18、17..19
    三块，默认 60/10 下只有一块。看 [`chunker.py`](../services/api/app/indexing/chunker.py)。

15. **Overlap 是干什么的？**
    相邻块共享部分行，减轻边界割裂。例如 10 行窗口、重叠 2 行，下一块从第 9 行
    开始。重叠必须小于窗口，才能向前推进；也会增加索引重复内容。
    校验见 [`core/config.py`](../services/api/app/core/config.py)。

16. **Indexing 是什么？**
    把仓库转成可检索的点：扫描 → 切块 → 批量向量化 → 校验 → 替换该仓库的点。
    当前先准备所有向量再删除旧点，但删除和写入不是事务；中途失败需完整重试。
    看 [`indexing/service.py`](../services/api/app/indexing/service.py)。

17. **Query Embedding 为什么必须使用兼容的模型？**
    查询向量和源码向量必须处在兼容的坐标空间。维度相等也不能说明模型相同。
    当前检查 provider/model/endpoint 身份；更换时用新集合并完整重建。
    看 [`embedding/identity.py`](../services/api/app/embedding/identity.py)
    与 [`retrieval/service.py`](../services/api/app/retrieval/service.py)。

18. **TopK 是什么？**
    按向量分数排序最多返回 K 个代码块，当前允许 1..20；不是 K 个唯一文件。
    不足 K 个就返回实际数量。契约见
    [`schemas/retrieval.py`](../services/api/app/schemas/retrieval.py)。

19. **TopK 太大有什么问题？**
    返回更多代码、增加展示与后续处理负担，也会把不相关块带进来。本阶段只有
    8 个点，Top5 已取 5/8；提高 K 不能证明模型更懂代码。当前尚无 RAG，所以
    这些结果没有占用聊天上下文。看
    [`RetrievalDebug.vue`](../apps/web/src/components/RetrievalDebug.vue)。

20. **Similarity Score 高是否代表一定正确？**
    不代表。评测的退款实现问题中 README 排在真正实现之前；README 提到路径
    不等于实现该逻辑。必须核查源码来源、任务标签和覆盖范围。原始排序见
    [Task 9 首次报告](../evaluation/reports/stage2_retrieval_20261004_01.json)。

21. **为什么 Stage 2 暂时不让 LLM 回答？**
    先单独验证“是否检索到对的代码”，才能在后续判断生成答案的错误来自哪里。
    当前只展示结果，不把 chunk 注入聊天上下文。
    [`retrieval/service.py`](../services/api/app/retrieval/service.py) 不调用聊天 provider。

22. **为什么现在不用 Tree-sitter？**
    当前按阶段要求先用可观察的固定行窗口，验证端到端检索。语法感知切块需要
    处理不同语言、节点大小与边界，留在后续明确范围内讨论。当前实现是
    [`chunker.py`](../services/api/app/indexing/chunker.py)，没有 AST 解析器。

23. **为什么现在不用 BM25？**
    先得到纯向量检索基线，才能测量关键词检索或混合搜索增加的收益与代价。
    当前没有稀疏向量、词项索引或融合排序；实际查询见
    [`vector_store.py`](../services/api/app/retrieval/vector_store.py)。

24. **为什么现在不用 Reranker？**
    重排会增加一次候选排序过程及模型/延迟成本。当前先记录原始向量排序与漏召回，
    留下可比较的基线。候选都没召回时，仅重排现有候选也不能补回缺失文件。
    当前 [`retrieval/service.py`](../services/api/app/retrieval/service.py) 没有重排阶段。

25. **Recall@K / Hit@K 有什么意义？**
    Hit 问“这条问题是否至少找到一个目标文件”；Recall 问“目标文件找齐多少”。
    当前先取前 K 个块，再按路径去重；报告对每条问题的 Recall 求平均。
    两条多文件问题的 Recall@5 是 1/2、2/3，即便 Hit@5 都为 true。12 条问题的
    Macro Recall@5 为 `(10 + 1/2 + 2/3) / 12 = 67/72`。
    计算与失败规则见 [`evaluate.py`](../evaluation/retrieval/evaluate.py) 和
    [评测说明](../evaluation/README.md)。

## 亲手检查一次

1. 按 [README](../README.md#stage-2--task-10-final-acceptance) 启动本地服务。
2. 在 `http://localhost:5173/#retrieval` 选择 `demo-repository`，输入
   `退款逻辑在哪里？`，TopK 设为 5，点击 Search。
3. 展开 RefundService 的 Code Preview，对照
   [`RefundService.java`](../workspaces/demo-repository/backend/service/RefundService.java)
   的 1..19 行，找到 `wallets.credit(userId, amount);`。
4. 阅读 [最终报告](../evaluation/reports/stage2_retrieval_20261004_task10.json) 的两条
   多文件失败案例，自己解释为什么 Hit 与 Recall 不同。
5. 使用文档命令复跑离线测试或评测。每次评测保存新报告，不覆盖历史结果。

当前 `.env`、8 个 Point 和测试仓库在验收中保持原样。本阶段结束；下一阶段范围
确认后再开始。尚无 RAG 回答、AST 切块、Hybrid Search、Reranker 或仓库 Agent。
