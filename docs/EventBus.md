# EventBus

同步事件总线，是 Core 层（LangGraph 节点）与 CLI 层（REPL 渲染）之间的解耦通信机制。

对标 gemini-cli 的 `confirmation-bus/message-bus.ts`。

## 为什么需要 EventBus

LangGraph 节点在 `graph.invoke()` 内部执行，CLI 层无法直接介入。EventBus 让节点在执行过程中**同步推送事件**，CLI 层通过订阅实时渲染，无需轮询、不破坏分层。

```
Core 层 (生产者)                    CLI 层 (消费者)
┌──────────────┐   emit(event)    ┌──────────────┐
│  reasoning   │ ───────────────→ │  _on_content  │  流式打印文本
│  tool_exec   │ ───────────────→ │  _on_tool_*   │  显示工具状态
│  observation │ ───────────────→ │  _on_error    │  报错提示
└──────────────┘                  └──────────────┘
                    EventBus
```

## 核心 API

```python
bus = EventBus()

# 订阅指定事件
bus.subscribe(EventType.CONTENT, callback)

# 订阅所有事件（通配）
bus.subscribe_all(callback)

# 取消订阅
bus.unsubscribe(EventType.CONTENT, callback)

# 发送事件（同步调用所有订阅者）
bus.emit(AgentEvent(type=EventType.CONTENT, data={"text": "hello"}, turn=1))
```

**回调签名**: `(AgentEvent) -> None`，异常会被捕获并记录日志，不会中断其他订阅者。

## AgentEvent 结构

```python
@dataclass
class AgentEvent:
    type: EventType       # 事件类型
    data: Any             # 事件数据（dict），各类型格式见下表
    turn: int = 0         # 所属轮次
    timestamp: float      # 自动填充 time.time()
```

## 事件类型一览

### 流式输出

| 事件 | 生产者 | data 格式 | 说明 |
|------|--------|-----------|------|
| `CONTENT` | reasoning | `{"text": str}` | LLM 流式文本片段，逐 chunk 触发 |
| `THOUGHT` | reasoning | `{"text": str}` | LLM 思考过程（DeepSeek-R1 的 reasoning_content） |
| `TOOL_CALL_REQUEST` | reasoning | `{"call_id", "tool_name", "arguments"}` | LLM 请求调用工具 |

### 工具执行

| 事件 | 生产者 | data 格式 | 说明 |
|------|--------|-----------|------|
| `TOOL_STATE_UPDATE` | tool_execution | `{"call_id", "tool_name", "status"}` | 工具开始执行（status="executing"） |
| `TOOL_LIVE_OUTPUT` | 具体工具 | `{"call_id", "text"}` | 工具实时输出（如 shell 命令的 stdout） |
| `TOOL_CALL_COMPLETE` | tool_execution | `{"call_id", "tool_name", "status", "result"?, "error_msg"?}` | 单个工具执行完毕 |
| `ALL_TOOLS_COMPLETE` | tool_execution | `{"count": int}` | 当轮所有工具执行完毕 |

### 权限确认

| 事件 | 生产者 | data 格式 | 说明 |
|------|--------|-----------|------|
| `APPROVAL_REQUEST` | tool_routing | `{"call_id", "tool_name", "arguments", "risk_level"}` | 中/高风险工具请求用户确认 |
| `APPROVAL_RESPONSE` | human_approval | `{"decisions": {call_id: bool}}` | 用户审批结果 |

### 会话控制

| 事件 | 生产者 | data 格式 | 说明 |
|------|--------|-----------|------|
| `TURN_START` | reasoning | `{"turn", "has_tool_calls", "tool_count"}` | 新一轮推理开始 |
| `TURN_END` | observation | `{"turn", "tool_count", "should_continue"}` | 当轮结束 |
| `SESSION_END` | — | — | 会话结束（预留） |
| `ERROR` | reasoning | `{"error": str, "source": str}` | 运行时错误 |
| `CONTEXT_COMPRESSED` | — | — | 上下文被压缩（预留） |

## 一次完整调用的事件流

用户输入 `"读取 config.json"` 后触发的事件序列：

```
CONTENT × N          ← reasoning: LLM 流式输出 "我来读取..."
TOOL_CALL_REQUEST    ← reasoning: read_file(file_path="config.json")
TURN_START           ← reasoning: turn=1
APPROVAL_REQUEST     ← tool_routing: (仅 medium/high 风险时)
TOOL_STATE_UPDATE    ← tool_execution: read_file → executing
TOOL_CALL_COMPLETE   ← tool_execution: read_file → success
ALL_TOOLS_COMPLETE   ← tool_execution: count=1
TURN_END             ← observation: turn=1, should_continue=True
CONTENT × N          ← reasoning: LLM 流式输出最终回答
TURN_START           ← reasoning: turn=2
```

## CLI 层订阅示例

`cli/repl.py` 中的实际订阅（5 种事件）：

```python
bus.subscribe(EventType.CONTENT,            self._on_content)
bus.subscribe(EventType.THOUGHT,            self._on_thought)
bus.subscribe(EventType.TOOL_CALL_REQUEST,  self._on_tool_request)
bus.subscribe(EventType.TOOL_CALL_COMPLETE, self._on_tool_complete)
bus.subscribe(EventType.ERROR,              self._on_error)
```

## 设计要点

- **同步调用** — `emit()` 直接在当前线程调用回调，无异步开销。LangGraph `graph.invoke()` 本身是同步的，事件在节点执行过程中实时触发，CLI 立即渲染。
- **异常隔离** — 单个订阅者抛异常不影响其他订阅者，错误记录到日志。
- **通配订阅** — `subscribe_all()` 接收所有事件，适合日志记录或 LangSmith 集成。
- **无持久化** — 纯内存、无回放，适合交互式 CLI 场景。如需持久化用 LangSmith tracing。
