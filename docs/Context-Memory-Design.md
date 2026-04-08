# Context & Memory 系统设计文档

> Phase 0 产出 | 严格参考 gemini-cli 的分层上下文 + 会话记录 + 压缩策略

---

## 1. 核心概念定义

### 1.1 Context（上下文指令）

**用户手写**的、声明式的项目/全局指令文件，对应 gemini-cli 的 `GEMINI.md`。

- 文件名: `CONTEXT.md`（可配置）
- 性质: 只读（Agent 不修改）、静态、人类可读
- 用途: 告诉 Agent "你在这个项目中应该怎么做"
- 示例内容: 项目架构说明、编码规范、常用命令、优化约束等

### 1.2 Memory（持久化记忆）

**Agent 自主学习**并持久化到全局 `CONTEXT.md` 中的事实记忆。

对应 gemini-cli 的做法：facts 追加到 `~/.gemini/GEMINI.md` 的 `## Gemini Added Memories` 小节。
我们类似地追加到 `~/.mtagent/CONTEXT.md` 的 `## Agent Memories` 小节，格式为 Markdown 列表项。

- 性质: Agent 可读写、持久化、人类可编辑
- 用途: 跨会话积累的知识（如 "用户偏好 AM 模式"、"该项目使用 OpenMP"）
- 存储位置: `~/.mtagent/CONTEXT.md` 的 `## Agent Memories` 区域

### 1.3 Session History（会话历史持久化）

参考 Claude 的 `history.jsonl` 格式，每次会话结束时将会话记录持久化。

- 存储位置: `~/.mtagent/history/{projectHash}/session-{timestamp}-{sessionId}.jsonl`
- 每行一条记录（JSONL），包含消息内容、token 用量、时间戳等
- 用途: 会话回顾、统计分析、可选的历史上下文恢复

### 1.4 Session Stats（会话统计）

参考 gemini-cli 的 `SessionMetrics`，在会话期间实时收集统计数据，退出时在 CLI 渲染。

- 总 token 消耗（input / output / cached）
- 会话持续时间
- 使用的模型
- 工具调用次数及成功/失败
- 轮次数

### 1.5 各概念关系图

```
                  持久化层（磁盘）                    运行时（内存）
          ┌──────────────────────────┐      ┌──────────────────────────┐
          │ ~/.mtagent/              │      │  AgentState              │
          │   CONTEXT.md             │─────→│    message (会话历史)     │
          │     (全局指令 + Memories) │      │    compressed_history    │
          │                          │      │    session_stats         │
          │ ./CONTEXT.md             │─────→│                          │
          │     (项目指令)            │      │  ContextManager          │
          │                          │      │    _global_context       │
          │ ~/.mtagent/history/      │      │    _project_context      │
          │   session-*.jsonl        │←─────│    _memory_facts         │
          │     (会话记录)            │      │    _session_stats        │
          └──────────────────────────┘      └──────────────────────────┘
```

---

## 2. 分层上下文注入（参考 gemini-cli 三层模型）

gemini-cli 将 Memory 分为三层注入到不同位置：

| 层级 | gemini-cli | 本项目 | 注入位置 |
|------|-----------|--------|---------|
| Tier 1 | 全局 GEMINI.md | `~/.mtagent/CONTEXT.md` | System Instruction（系统提示词） |
| Tier 2 | 项目 GEMINI.md | `./CONTEXT.md` | 首条 User Message 的 session_context |
| Tier 3 | 子目录 GEMINI.md (JIT) | 延后实现 | Tool Output（按需注入） |

### 2.1 Tier 1: 全局上下文 → System Instruction

```
~/.mtagent/CONTEXT.md
```

- 启动时加载，注入到 system prompt 中
- 包含用户手写的全局指令 + `## Agent Memories` 区域

### 2.2 Tier 2: 项目上下文 → 首条消息

```
./CONTEXT.md
```

- 启动时加载，作为 `<session_context>` 注入到首条 user message
- 包含项目特定指令

### 2.3 注入模板

gemini-cli 的做法是将 Tier 2 和环境信息包装在 `<session_context>` 中作为首条 user message：

```xml
<session_context>
Today's date: 2026-04-02
OS: darwin
Working directory: /path/to/project
Project context:
{project_context_content}
</session_context>
```

而 Tier 1（全局）直接嵌入 system instruction。

### 2.4 Prompt 最终结构

```
┌─ System Instruction ──────────────────────────────────┐
│  基础角色指令                                           │
│  全局 CONTEXT.md 内容 (Tier 1)                         │
│  Agent Memories 列表                                   │
│  可用工具列表                                           │
└────────────────────────────────────────────────────────┘

┌─ Messages ─────────────────────────────────────────────┐
│  [0] User: <session_context>                           │
│         日期、OS、工作目录                                │
│         项目 CONTEXT.md 内容 (Tier 2)                   │
│       </session_context>                               │
│  [1] User: 用户第一条消息                                │
│  [2] AI: ...                                           │
│  ...                                                   │
│  [compressed_history 替代早期消息]                       │
│  [最近 N 条消息]                                        │
└────────────────────────────────────────────────────────┘
```

---

## 3. Memory（Agent 记忆）

### 3.1 存储格式

参考 gemini-cli，facts 直接存在全局 `CONTEXT.md` 文件末尾的专用 section 中，格式为 Markdown 列表：

```markdown
## Agent Memories

- 用户偏好使用 AM 模式优化矩阵运算
- 该项目的编译目标板为 MT-3000A
- OpenMP 并行度默认设置为 4
```

优势：
- 用户可以直接编辑（Markdown 可读）
- 与全局指令在同一文件中，加载逻辑简单
- 和 gemini-cli 的 `## Gemini Added Memories` 完全一致

### 3.2 写入逻辑

1. 读取 `~/.mtagent/CONTEXT.md` 全部内容
2. 查找 `## Agent Memories` section
3. 如果不存在，在文件末尾创建该 section
4. 将新 fact 追加为 `- {sanitized_fact}`（移除换行等特殊字符）
5. 写回文件

### 3.3 读取逻辑

加载全局 CONTEXT.md 时，整个文件（包含 Memories）一起读入。
`ContextManager` 内部可解析出 Memories 列表用于展示（`/memory list`）。

### 3.4 删除逻辑

从文件中移除对应行，重写文件。支持 `/memory remove <index>` 命令。

---

## 4. Session History 持久化

### 4.1 存储位置

```
~/.mtagent/history/{projectHash}/session-{timestamp}-{sessionId}.jsonl
```

- `projectHash`: 项目路径的 hash（区分不同项目）
- 每个会话一个文件
- JSONL 格式，每行一条记录

### 4.2 消息记录格式

参考 Claude 的 history.jsonl 和 gemini-cli 的 `MessageRecord`：

```jsonl
{"type": "session_start", "sessionId": "abc123", "project": "/path/to/project", "model": "deepseek-chat", "timestamp": 1775115575203}
{"type": "user", "display": "请分析这段代码", "timestamp": 1775115575300}
{"type": "assistant", "content": "我来分析一下...", "tokens": {"input": 1200, "output": 350, "total": 1550}, "model": "deepseek-chat", "timestamp": 1775115576000}
{"type": "tool_call", "toolName": "read_file", "arguments": {"file_path": "/src/main.c"}, "status": "success", "timestamp": 1775115576500}
{"type": "session_end", "sessionId": "abc123", "stats": {"duration_ms": 120000, "total_tokens": 15000, "turns": 5, "tool_calls": 3}, "timestamp": 1775115695203}
```

### 4.3 记录字段说明

**session_start 记录**:
| 字段 | 类型 | 说明 |
|------|------|------|
| `type` | str | `"session_start"` |
| `sessionId` | str | UUID |
| `project` | str | 项目绝对路径 |
| `model` | str | 使用的 LLM 模型名 |
| `timestamp` | int | Unix 毫秒时间戳 |

**user / assistant 记录**:
| 字段 | 类型 | 说明 |
|------|------|------|
| `type` | str | `"user"` / `"assistant"` |
| `content` / `display` | str | 消息内容 |
| `tokens` | dict? | (assistant only) `{input, output, cached?, total}` |
| `model` | str? | (assistant only) 使用的模型 |
| `timestamp` | int | Unix 毫秒时间戳 |

**tool_call 记录**:
| 字段 | 类型 | 说明 |
|------|------|------|
| `type` | str | `"tool_call"` |
| `toolName` | str | 工具名称 |
| `arguments` | dict | 调用参数 |
| `result` | str? | 执行结果（可截断） |
| `status` | str | `"success"` / `"error"` / `"cancelled"` |
| `timestamp` | int | Unix 毫秒时间戳 |

**session_end 记录**:
| 字段 | 类型 | 说明 |
|------|------|------|
| `type` | str | `"session_end"` |
| `sessionId` | str | UUID |
| `stats` | dict | 会话统计摘要（见 Section 5） |
| `timestamp` | int | Unix 毫秒时间戳 |

---

## 5. Session Stats（会话统计）

### 5.1 收集的指标

参考 gemini-cli 的 `SessionMetrics`：

```python
class SessionStats:
    session_id: str
    start_time: float                    # time.time()
    model: str                           # 当前使用的模型

    # Token 统计
    total_input_tokens: int              # 输入 token 总计
    total_output_tokens: int             # 输出 token 总计
    total_tokens: int                    # 总 token

    # 轮次统计
    turn_count: int                      # 推理轮次
    prompt_count: int                    # 用户提问次数

    # 工具统计
    tool_calls_total: int                # 工具调用总次数
    tool_calls_success: int              # 成功次数
    tool_calls_failed: int               # 失败次数
    tool_calls_by_name: dict[str, int]   # 按工具名统计
```

### 5.2 数据收集点

| 事件 | 收集什么 | 在哪收集 |
|------|---------|---------|
| LLM 响应完成 | input/output tokens, model | `reasoning` 节点 |
| 工具执行完成 | tool_name, status | `tool_execution` 节点 |
| 用户提交消息 | prompt_count++ | REPL |
| 会话结束 | duration | REPL quit handler |

Token 数据来源: OpenAI-compatible API 响应中的 `usage` 字段。
（gemini-cli 从 `GenerateContentResponseUsageMetadata` 取，我们从 LangChain 的 `AIMessage.usage_metadata` 或 `response_metadata` 取）

### 5.3 CLI 退出时渲染

会话结束时在终端渲染统计摘要（参考 gemini-cli 的退出显示）：

```
─────────────────────────────────────────
  Session Summary
  Model:     deepseek-chat
  Duration:  12m 34s
  Turns:     8
  Tokens:    12,450 (in: 10,200 / out: 2,250)
  Tools:     5 calls (4 success, 1 failed)
─────────────────────────────────────────
```

使用 `rich` 库渲染（项目已有依赖）。

---

## 6. 上下文窗口管理（压缩策略）

### 6.1 Token 估算

参考 gemini-cli 的启发式方法（不引入 tokenizer 依赖）：

```python
def estimate_tokens(text: str) -> int:
    ascii_chars = sum(1 for c in text if ord(c) < 128)
    non_ascii_chars = len(text) - ascii_chars
    return int(ascii_chars / 4 + non_ascii_chars * 1.3)
```

- ASCII: ~4 字符/token
- 非 ASCII (CJK): ~1.3 token/字符

### 6.2 压缩触发条件

参考 gemini-cli：**当历史消息 token 数超过 context window 的 50% 时触发压缩**。

```python
COMPRESSION_THRESHOLD = 0.50    # 50% of token limit
PRESERVE_RATIO = 0.30           # 保留最近 30% 的消息
```

### 6.3 压缩流程

参考 gemini-cli 的 `chatCompressionService`：

```
1. 估算当前 messages 总 token 数
2. 如果 < 50% of token_limit → 不处理
3. 找到安全分割点（用户消息之后、工具调用之前，不切断 turn）
4. 将分割点之前的消息交给 LLM 生成摘要
5. 用摘要替换旧消息:
   messages = [session_context] + [compressed_summary] + [最近的消息]
```

### 6.4 大型工具输出截断

参考 gemini-cli 的做法：旧的大型工具输出（如 read_file 结果）截断为最后 30 行，避免撑满 context window。

### 6.5 备选方案

优先考虑 LangGraph 内置的 `trim_messages` 工具。如果不满足需求（如无法做 LLM 摘要），再自行实现。

---

## 7. Context Budget（大小限制）

防止 context 注入超过合理范围：

| 部分 | 预算占比 | 说明 |
|------|---------|------|
| System Instruction (Tier 1 + 工具) | ~15% | 全局 context + 角色指令 + 工具描述 |
| Session Context (Tier 2) | ~10% | 项目 context + 环境信息 |
| 会话历史 | ~75% | 压缩摘要 + 最近消息 + 工具输出 |

当 Context 超过预算时，按优先级截断：
1. 先截断 Memories（保留最近的）
2. 再截断全局 context
3. 项目 context 最后截断（最高优先级）

初期实现用 `estimate_tokens()` 估算，不引入 tokenizer 依赖。

---

## 8. State 扩展

**最小化原则**: 只在 `AgentState` 中新增必要字段，Context 缓存由 `ContextManager` 自行管理。

```python
class AgentState(TypedDict):
    # ... 现有字段不变 ...

    # 新增
    compressed_history: str          # 压缩后的历史摘要
```

**不放入 State 的内容:**
- `loaded_context` — Context 是 prompt 构建的输入，不是运行状态
- `context_files` — 调试信息由 ContextManager 自己维护
- `memory_facts` — 由 ContextManager 管理，不参与 checkpoint 序列化
- `session_stats` — 由 ContextManager 管理，仅在会话结束时序列化

---

## 9. 与现有架构的集成

### 9.1 依赖关系（遵守三层铁律）

```
CLI 层
  └─ cli/repl.py           — 注册 /context、/memory 命令
  └─ cli/commands/          — 命令处理
  └─ cli/event_handlers/    — 订阅 SESSION_END 渲染统计

Core 层
  └─ core/context.py        — ContextManager（核心模块）
  └─ core/nodes/reasoning.py — 调用 context_manager 构建 prompt
  └─ core/agent.py          — 初始化 ContextManager

Tools 层
  └─ tools/agent_ops/memory.py — save_memory 工具（通过回调访问 ContextManager）

Prompts
  └─ prompts/system_prompt.py — 接受 context 参数拼接 prompt
```

ContextManager 位于 Core 层，**Tools 层的 memory 工具通过回调/注册机制访问 ContextManager，不直接 import Core**。

### 9.2 初始化流程

```python
# core/agent.py 中:
class AgentRuntime:
    def __init__(self, ...):
        self.context_manager = ContextManager(
            working_directory=working_dir,
            config=settings.CONTEXT,
        )
        self.context_manager.load()   # 启动时加载全部 context + memory
        # ... 传递给 graph builder ...
```

### 9.3 不改动的部分

- `core/graph.py` — 不新增节点，不改边
- `core/state.py` — 仅新增 `compressed_history` 字段
- `core/event_bus.py` — 已有 `CONTEXT_COMPRESSED` 事件类型（预留），无需改动

---

## 10. 文件清单

Phase 0 之后的文件变更:

| 操作 | 文件 | 说明 |
|------|------|------|
| 新增 | `docs/Context-Memory-Design.md` | 本文档 |
| 新增 | `core/context.py` | ContextManager 骨架 |
| 修改 | `config/settings.py` | 新增 CONTEXT 配置项 |

后续 Phase 的文件变更预览:

| Phase | 内容 | 新增/修改 |
|-------|------|----------|
| 1 | ContextManager 完整实现 + 单测 | `core/context.py` + `tests/test_core/test_context.py` |
| 2 | Prompt 集成 + session_context 注入 | `prompts/system_prompt.py` + `core/nodes/reasoning.py` + `core/agent.py` |
| 3 | Memory tool + /memory CLI + 单测 | `tools/agent_ops/memory.py` + `cli/commands/memory.py` |
| 4 | 会话压缩 + 历史持久化 + 统计 | `core/compressor.py` + `core/session_recorder.py` + `cli/event_handlers/` |
| 5 | 文档 + 集成测试 | `docs/Context&Memory.md` + `tests/` |
