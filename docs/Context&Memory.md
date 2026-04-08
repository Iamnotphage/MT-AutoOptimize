# Context & Memory

当前项目的 context 与 memory 已经形成一个可用的 v1 闭环，重点解决了三件事：

1. 启动时加载全局 / 项目上下文
2. 会话过程中在 token 压力下执行历史压缩
3. 退出后可基于 canonical transcript 恢复会话上下文

## 当前结构

### 1. 静态 Context

由 [`core/context.py`](../core/context.py) 管理，分为两层：

- Tier 1: 全局 `CONTEXT.md`
  注入到 system prompt，适合放全局规则、长期偏好、Agent memory。
- Tier 2: 项目 `CONTEXT.md`
  注入到首轮 `<session_context>`，适合放当前项目说明。

当前 `ContextManager` 负责：

- 加载全局 / 项目 context
- 构建 system context
- 构建 session context
- 提供基础 token / 文件统计

### 2. Memory

memory 也由 [`core/context.py`](../core/context.py) 管理，目前是最简单的持久化 facts 方案：

- 存储位置：全局 `CONTEXT.md` 的 `## Agent Memories` 区域
- 写入方式：追加 markdown list item
- 读取方式：启动时一并加载到全局 context

当前能力：

- Agent 可通过 `save_memory` tool 持久化一条 fact
- CLI 支持 `/memory list|add|remove`
- memory 对人类可见、可手工编辑

当前限制：

- 还没有 relevance retrieval
- 还没有去重、失效、冲突处理
- 还没有 project-scoped memory

## 会话 Context 管理

### 1. 历史压缩

由 [`core/compressor.py`](../core/compressor.py) 和 [`core/nodes/reasoning.py`](../core/nodes/reasoning.py) 负责。

当前已实现：

- 根据 `token_limit * compression_threshold` 判断是否触发压缩
- 保留最近一部分消息，压缩更早历史
- 用 LLM 生成结构化摘要
- 在触发压缩的当轮直接使用压缩后的消息视图

压缩结果会写入 session JSONL：

- `compression`
- `summary`
- `removed_count`
- `kept_count`

这解决了“压缩只在内存里生效、退出即丢失”的问题。

### 2. Transcript-only Resume

由 [`core/session.py`](../core/session.py)、[`cli/commands/resume.py`](../cli/commands/resume.py)、[`core/nodes/reasoning.py`](../core/nodes/reasoning.py)、[`core/nodes/observation.py`](../core/nodes/observation.py) 协同实现。

当前 session 文件里用于恢复状态的单一事实源是 `transcript_message`：

- `role=user`
- `role=assistant`，包含 `tool_calls`
- `role=tool`，包含 `tool_call_id`

resume 时会：

1. 扫描最后一条 `compression`
2. 只恢复该压缩摘要及其后的 transcript
3. 重建：
   - `HumanMessage`
   - `AIMessage(tool_calls=...)`
   - `ToolMessage`

因此当前 `/resume` 已经不再只是恢复纯文本聊天，而是能恢复：

- 用户消息
- assistant 的 tool call 语义
- tool result 对话历史

同时，resume 后会重新估算已加载消息的 token 数，用于输入框 context 占比显示。

## 当前边界

需要注意，当前 `/resume` 仍然是 **context resume**，不是 **execution resume**。

已完成的是：

- 恢复消息历史
- 恢复压缩摘要后的上下文视图
- 恢复 tool-aware transcript

尚未完成的是：

- LangGraph checkpoint 持久化
- interrupt / approval state 恢复
- pending node 恢复
- tool execution 中断恢复策略

换句话说：

- 现在系统已经能恢复“模型看过什么”
- 还不能恢复“图执行停在什么地方”

## 下一阶段

后续计划分两步：

1. Phase 2
   持久化 LangGraph checkpoint，真正恢复 interrupt / pending node / approval state。

2. Phase 3
   处理工具执行中断、未完成审批、压缩后 checkpoint 兼容等异常恢复策略。

当前已完成的 P3-1 为工具调用状态机正式引入 `interrupted`，用于表达“工具执行过程中被打断，结果不可信，等待恢复策略决定后续动作”。

当前已完成的 P3-2 为工具执行中断恢复的最小安全策略：

- 若 checkpoint 恢复时发现 graph 停在 `tool_execution`
- 且存在尚未完成的 `pending_tool_calls`
- 系统不会自动重跑工具
- 而是将这些工具调用收敛为 `interrupted`
- 并写入中断提示消息后结束该未完成执行链

这样可以避免 resume 后误重复执行工具。

当前已完成的 P3-3 为审批恢复策略：

- 若 checkpoint 中存在 `awaiting_approval`
- 恢复时必须同时存在可恢复的审批 interrupt 请求
- 若状态不一致（有待审批工具但没有审批请求），则拒绝恢复执行现场
- 若审批请求存在，则 `/resume` 会明确提示“将重新请求确认”

同时，session 日志会额外记录：

- `approval_request`
- `approval_decision`

用于后续回放和审计。

### 审批恢复边界

当前审批恢复的边界规则如下：

1. 工具处于 `awaiting_approval`，但你还没有做出确认，CLI 中断退出
   - 恢复后必须重新确认
   - 原因是旧审批动作并未真正完成

2. 你已经输入 `y` / `n`，但在工具真正执行完成前 CLI 中断退出
   - 若图状态已经离开 `awaiting_approval`，则不会再重新审批
   - 若随后停在 `tool_execution`，恢复时会按 P3-2 收敛为 `interrupted`
   - 不会自动重跑工具

3. 工具已经执行成功，之后 CLI 中断退出
   - 恢复后不会重新审批，也不会重新执行

因此，“是否重新确认”取决于恢复时 checkpoint 中的真实状态，而不是取决于用户主观记忆里“之前是不是差不多已经点过确认”。

当前系统采用的原则是：

- 仍处于 `awaiting_approval` → 重新确认
- 已进入 `tool_execution` 但未完成 → 标记 `interrupted`
- 已完成 → 不重放

## Phase 4: 一致性检查

当前已完成的 P3-4 为 checkpoint / transcript / compression 一致性规则收口。

当前优先级如下：

1. execution resume 以 checkpoint 为准
2. transcript 用于历史展示、压缩摘要恢复、fallback token 估算
3. compression 只影响 transcript/history，不覆盖 checkpoint 的执行语义

当前已实现的检查包括：

- session 缺少 `threadId` → 拒绝 execution resume
- 找不到持久化 checkpoint → 拒绝 execution resume
- 存在 `awaiting_approval`，但没有可恢复的审批请求 → 拒绝 execution resume
- checkpoint 与 transcript 历史长度不一致 → 允许恢复，但明确提示“以 checkpoint 为准”
- checkpoint 有状态但 transcript 不完整 → 允许恢复，但提示历史展示可能不完整

这一步的目的不是阻止恢复，而是让系统在不一致场景下做出明确、可解释的选择。

## Phase 2: 持久化 Checkpoint

当前已接入 LangGraph 的 SQLite checkpointer，用于恢复 graph execution state。

### 1. 存储位置

每个项目共享一个 SQLite checkpoint 文件，路径规则为：

`~/.mtagent/history/<project-hash>/checkpoints.sqlite`

其中：

- `<project-hash>` 由工作目录路径计算得到
- `checkpoints.sqlite` 是当前项目统一的 checkpoint 存储文件

不同项目因为目录不同，不会冲突；同一项目下的不同会话通过 `thread_id` 区分，而不是通过多个 sqlite 文件区分。

### 2. 它存什么

SQLite 不存 transcript，而是存 LangGraph 的 checkpoint state。

当前主要用于恢复：

- `message`
- `pending_tool_calls`
- `completed_tool_calls`
- `approval_requests`
- `needs_human_approval`
- graph 当前的 `next` / interrupt 状态

也就是说：

- JSONL 负责“过去发生了什么”
- SQLite checkpoint 负责“图执行停在什么地方”

### 3. 它怎么接入

在 [`core/agent.py`](../core/agent.py) 中：

1. 计算当前项目的 checkpoint 路径
2. 用 `SqliteSaver.from_conn_string(...)` 打开 sqlite 文件
3. 在 `build_agent_graph(..., checkpointer=...)` 时注入 checkpointer

之后 LangGraph 会在 graph 执行过程中自动把 checkpoint 写入 SQLite。

### 4. `/resume` 怎么使用它

当前 `/resume` 的执行态恢复流程为：

1. 从 session JSONL 中读取 `threadId`
2. 用该 `thread_id` 调用 `graph.get_state(config)`
3. 由 LangGraph 从 `checkpoints.sqlite` 中取回该 thread 最近的 checkpoint
4. 若恢复出的 snapshot 有 `values`，说明 execution state 可恢复
5. 若 snapshot 仍有 `next`，则继续处理未完成的 interrupt / approval

因此当前 `/resume` 已经从单纯的 transcript 恢复，升级成了 checkpoint-first 的 execution resume。

### 5. 调试方式

`checkpoints.sqlite` 是 LangGraph 的内部状态库，不建议作为手工维护的业务文件。

如需调试，可用 sqlite 工具查看：

```bash
sqlite3 ~/.mtagent/history/<project-hash>/checkpoints.sqlite
```

常见查看方式：

```sql
.tables
.schema
SELECT name FROM sqlite_master WHERE type='table';
```

需要注意：

- 表结构以 LangGraph 当前版本实现为准
- 可以用于排障
- 不应在业务代码中直接依赖内部表结构
