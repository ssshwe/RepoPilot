# Stage 4 / Task 5：共享词法索引快照

实现了持久化 Snapshot Store、从已有 Qdrant Point 构建/加载快照，以及协调重建和读取的一致性边界。BM25、Exact 和 Dense 后续可使用同一个冻结的 Chunk 集合；本任务没有实现这些词法检索算法，也没有切换生产请求或页面。

## 输入与输出

实现文件为 [`snapshot_models.py`](../services/api/app/retrieval/snapshot_models.py)、[`snapshot_store.py`](../services/api/app/retrieval/snapshot_store.py)、[`snapshots.py`](../services/api/app/retrieval/snapshots.py)。

`QdrantImageReader` 只读取已有索引的完整 Payload 和 Vector，不读取最新源文件或重新切块。它验证仓库过滤、Point/Chunk UUID、Embedding 身份、1024 等实际维度、Cosine、行坐标及 AST/fallback 元数据，核对分页、数量及读取前后的配置。协调器再比较连续两份完整仓库映像，拒绝漂移。

`IndexedRepository` 保存按 Chunk ID 排序的原内容、路径、行号、Symbol，以及完整 Payload / Vector 的 SHA-256。快照保留这些代码和摘要，不重复保存向量数值；Task 1/3 的原始完整向量快照仍单独保留。`LexicalSnapshot` 增加 Task 4 的有序 Tokens、词频、Tokenizer/Unicode/实现指纹，供后续通道共享。

作用域由 Qdrant 地址摘要、Collection、repository 三者确定。不会把地址或密钥写进作用域。不同仓库、Collection 或后端的快照互相隔离，其他仓库重建不使本仓库失效。

版本有三个不同用途：

- `index_revision`：本仓库映像的 SHA-256，绑定作用域、Embedding/切块配置、所有 Point 的代码及 Payload/Vector 摘要。这是内部计算的身份，不是新增的生产 API 字段或 Qdrant 原生修订号；它与 Task 3 的整 Collection 摘要属于不同范围。
- `snapshot.revision`：仓库映像、Tokenizer 规则/实现和文档 Tokens 共同确定的不可变版本。
- `ticket`：每次重建的持久化世代，内容相同的重复发布也会有新 ticket，用于拒绝读取途中发生的 ABA 发布。

## 构建、重建与状态

Snapshot Store 使用本机 SQLite WAL 和短事务持久化状态、不可变版本及当前指针；发布版本和更新 `ready` 指针在同一个事务内提交。`synchronous=FULL`，数据库格式版本为 1。每个作用域还持有 Windows/POSIX 内核文件锁，阻止同作用域的并发写者；进程死亡后锁自动释放，数据库中的状态保留。

| 状态 | 读取行为 | 进入方式 |
| --- | --- | --- |
| `incomplete` | 拒绝词法/多路读取 | 未构建、重建失败/取消、确认索引或快照漂移 |
| `rebuilding` | 拒绝词法/多路读取 | 写 Dense 前先持久化目标 Chunk 指纹及新 ticket |
| `ready` | 校验版本后加载；多路请求还需核对实时 Dense | 所有目标 Chunk 与两侧映像完整一致后原子发布 |

重建前必须提供**完整预期 Chunk 内容指纹**。仅仅“当前 Point 数量稳定”不足以证明完整，因为先删后写失败也可能留下一个稳定的部分索引。`SnapshotCoordinator.reindex(prepared, indexer)` 从已经准备好的完整 Chunk 集合计算目标指纹，再执行原 Indexer；全部写入并确认后，才允许发布。

失败或取消会留下 `incomplete`。旧版本保留用于审计，但不会回退为可查询版本；恢复需要显式完整重建。进程意外退出可能留下 `rebuilding`，新的写者获得已释放的内核锁后，可以用新 ticket 完整重建。

```python
from pathlib import Path
from app.retrieval.snapshot_store import SnapshotStore
from app.retrieval.snapshots import QdrantImageReader, SnapshotCoordinator

# qdrant_store/settings/indexer/prepared 由已有构建流程提供。
store = SnapshotStore(Path(".tools/lexical/snapshots.sqlite3"))
coordinator = SnapshotCoordinator(store, QdrantImageReader(qdrant_store, settings))
snapshot = await coordinator.reindex(prepared, indexer)

# 从已知完整索引首次构建时，目标指纹须来自可信的完整清单。
snapshot = await coordinator.rebuild(repository,
    expected_source_sha256=trusted_complete_inventory.source_sha256)
```

加载只依赖持久化数据和相同 Tokenizer 实现，服务重启后无需重扫仓库。格式、摘要、词频、Chunk 内容、Tokenizer 版本、Unicode 版本或实现不匹配会拒绝加载。代码边界与预算保持明确：最多 10000 个 Chunk、200 万内容字符/向量坐标、32 MiB 单份持久化快照。

## 搜索一致性边界

未来多路查询通过 `coordinator.use(repository, operation)`。回调拿到同一不可变快照，使用其中的源代码和 Tokens 计算结果；协调器在回调前后核对 Dense 映像和持久化 ticket，全部一致后才返回回调结果。期间发生重建、向量/Payload 变化或同内容重新发布，结果会被丢弃。

回调内不得提前发布或流式发送结果。单独 `store.load_ready` 只证明本地快照完整，不证明实时 Dense 仍相同；混合检索必须使用协调器。当前生产索引/检索入口保持原样，尚未启用词法通道。Task 13 接入产品时，需要让参与多路检索的写者和读者经过此边界。

这是单机、协作写者的一致性方案，不是 Qdrant 与 SQLite 的跨系统事务。完整读取与前后核对能拒绝观察到的外部修改；绕过文件锁、在两次核对之间修改又恢复的外部写者无法由 SHA-256 轮询排除。生产接入时必须约束这些写者。本任务不会自动重新索引生产仓库。

## 复现与实际验证

```powershell
.\infra\Qdrant.ps1 up
# 新时间戳报告；原有生产索引只读，写入使用随机临时 Collection
& '.\services\api\.venv\Scripts\python.exe' evaluation/stage4/verify_snapshots.py
# 完全离线复算本次快照、生命周期与 SQLite 备份
& '.\services\api\.venv\Scripts\python.exe' evaluation/stage4/verify_snapshots.py `
  --verify-only evaluation/reports/stage4_snapshots_20261026_task5.json

Push-Location services/api
& '.\.venv\Scripts\python.exe' -m pytest -q
Pop-Location
& '.\services\api\.venv\Scripts\python.exe' -m pytest evaluation/tests -q
```

报告为 [`stage4_snapshots_20261026_task5.json`](../evaluation/reports/stage4_snapshots_20261026_task5.json)，其 `_artifacts` 含十份不可变 JSON 版本及 SQLite 备份；[机器验收](stage-4-task-5-verification.json) 关联测试和保护清单。运行时库位于忽略的 `.tools` 独立目录，WAL/SHM 不作为交付备份。已有报告、产物目录和备份拒绝覆盖；失败不发布完成报告。

2026-10-26 的真实运行完成 **14 个生命周期阶段**：

- Task 3 保存的 **129 个 Chunk** 按四个仓库构建；Tokens 与 Task 4 完全一致，没有查询保留集。
- 原有生产仓库 17 个 Point 只读构建。另在私有 3 维 Collection 中创建两个临时 Python 仓库，执行真实 Qdrant 索引、读取和失效核对。
- 修改文件但不重建时，快照仍返回原索引内容；显式重建后变更生效、删除文件消失；删空仓库后得到空快照及空 Dense 结果。
- 注入“写入一个 Point 后失败”，实际 Dense 留下一个 Point，状态为 `incomplete`，旧词法快照读取被阻断。显式完整恢复后，内容与失败前声明的完整目标指纹一致。
- 外部改变私有仓库一个 Vector，读取被拒绝且状态失效；另一个仓库的快照不受重建影响。
- **七个作用域**均在独立 Python 进程中恢复；SQLite 备份可只读重算，读取不改变备份字节。

未调用真实 Embedding 或生成模型。六次确定性测试 Embedding 调用只为 3 维存储夹具提供向量，不作为语义检索质量证据；生产 Embedding 配置未改变。随机临时 Collection 已确认删除，两个既有 Collection 的配置、Metadata、Payload、Vector 均保持一致。

新增 **32 项快照测试**覆盖进程死亡后的锁恢复、同仓库并发写拒绝、不同仓库并行、发布事务回滚、静默少写、取消、读取期间漂移、旧读者不能使新版本失效、损坏/版本变化以及只读备份。默认后端全套 **1160 通过、6 跳过**，保留两个既有依赖弃用警告。新增 **11 项收据测试**后，评测套件 **141 项通过**。本次另行执行了上述真实 Qdrant 快照生命周期验证；五个需要显式启用的既有模型/索引集成用例未重复运行。前端和公共请求/响应没有改动，本次未重复构建前端。

成功案例是改动/删除后显式重建清除了旧 Chunk；受控失败案例是一个 Point 的部分写入，保留失败状态并阻断读取。这里验证的是一致性与可恢复性，没有声称改变 Dense 的答案质量或排序。

## 下一任务

Task 6 将在这些按仓库隔离的文档、Tokens 和版本上实现 BM25，并返回内部候选。空语料、无有效 Token、零词匹配及 Tokenizer/索引漂移都已有明确边界。Exact、Hybrid 和产品接入仍在后续任务。
