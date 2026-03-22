# MT-3000 Coding Agent 详细开发计划

> 基于 gemini-cli 架构设计，使用 Python + LangChain/LangGraph 实现面向 MT-3000 超算平台的 Coding Agent

---

## 一、架构总览

### 1.1 层级对照 (gemini-cli → MT-3000 Agent)

```
gemini-cli (TypeScript/React)          MT-3000 Agent (Python)
═══════════════════════════════        ═══════════════════════════
A. 用户层                               A. 用户层
B. packages/cli (React/Ink UI)          B. cli/ (Rich/Textual TUI)
C. packages/core (Agent Loop)           C. core/ (LangGraph ReAct)
D. Gemini API                           D. LLM (OpenAI-compatible)
E. 本地/外部工具                         E. 本地/远程工具 + MT-3000
```

### 1.2 核心模块映射

| gemini-cli 模块 | MT-3000 Agent 模块 | 说明 |
|---|---|---|
| `core/client.ts` (GeminiClient) | `core/agent.py` (AgentRunner) | ReAct 主循环，用 LangGraph StateGraph 实现 |
| `core/turn.ts` (Turn) | LangGraph 节点: `reasoning_node` | 单轮 LLM 推理 + 工具调用提取 |
| `core/geminiChat.ts` (GeminiChat) | `core/llm.py` (LLMClient) | LangChain ChatModel 封装，流式输出 |
| `scheduler/scheduler.ts` | `core/scheduler.py` (ToolScheduler) | 工具执行调度 + 并发控制 |
| `scheduler/tool-executor.ts` | `core/executor.py` (ToolExecutor) | 单个工具执行器 |
| `scheduler/state-manager.ts` | `core/state.py` (AgentState) | LangGraph State 定义 |
| `tools/tool-registry.ts` | `tools/registry.py` (ToolRegistry) | 工具注册中心 |
| `tools/*.ts` (各工具实现) | `tools/*.py` (各工具实现) | 具体工具 |
| `confirmation-bus/message-bus.ts` | `core/event_bus.py` (EventBus) | 事件总线 (asyncio.Event) |
| `policy/` | `core/policy.py` (PolicyEngine) | 工具执行策略/权限控制 |
| `cli/hooks/useGeminiStream.ts` | `cli/stream_handler.py` | 流式事件处理 |
| `cli/hooks/useToolScheduler.ts` | `cli/tool_renderer.py` | 工具状态 UI 渲染 |
| `core/prompts.ts` | `core/prompts.py` + `prompts/` | 系统提示词构建 |
| `services/compression` | `core/compression.py` | 上下文压缩 |

### 1.3 整体架构图

```
┌─────────────────────────────────────────────────────────────────┐
│                        A. 用户层 (User)                         │
└──────────────────────────┬──────────────────────────────────────┘
                           │ 用户输入/确认
┌──────────────────────────▼──────────────────────────────────────┐
│                   B. cli/ 交互层                                 │
│                                                                  │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────────┐     │
│  │  app.py     │  │ stream_      │  │  tool_renderer.py  │     │
│  │  (CLI入口)  │  │ handler.py   │  │  (工具状态渲染)    │     │
│  │  Rich TUI   │  │ (流式处理)   │  │                    │     │
│  └──────┬──────┘  └──────┬───────┘  └─────────┬──────────┘     │
│         │                │                     │                 │
│         └────────────────┼─────────────────────┘                │
│                          │ EventBus (事件驱动)                   │
└──────────────────────────┼──────────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────────┐
│                   C. core/ 内核层                                │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │              LangGraph StateGraph (ReAct Loop)           │    │
│  │                                                          │    │
│  │  ┌──────────┐    ┌───────────┐    ┌────────────────┐    │    │
│  │  │ reasoning │───▶│ tool_exec │───▶│  observation   │    │    │
│  │  │ _node    │    │ _node     │    │  _node         │    │    │
│  │  └─────▲────┘    └───────────┘    └───────┬────────┘    │    │
│  │        │                                   │             │    │
│  │        └───────────────────────────────────┘             │    │
│  │                    (循环直到无工具调用)                     │    │
│  └─────────────────────────────────────────────────────────┘    │
│                                                                  │
│  ┌──────────┐ ┌───────────┐ ┌────────┐ ┌──────────────────┐    │
│  │ llm.py   │ │scheduler. │ │policy. │ │ compression.py   │    │
│  │(LLM封装) │ │py(调度器) │ │py(策略)│ │ (上下文压缩)     │    │
│  └──────────┘ └───────────┘ └────────┘ └──────────────────┘    │
└──────────────────────────┬──────────────────────────────────────┘
                           │
            ┌──────────────┼──────────────┐
            ▼              ▼              ▼
┌────────────────┐ ┌─────────────┐ ┌──────────────┐
│   D. LLM API   │ │ E-1. 本地   │ │ E-2. 远程    │
│  (OpenAI兼容)  │ │   工具      │ │   工具       │
│                │ │             │ │              │
│ - DeepSeek     │ │ - ReadFile  │ │ - SSH编译    │
│ - Qwen         │ │ - WriteFile │ │ - Slurm提交  │
│ - GPT-4o       │ │ - EditFile  │ │ - 结果拉取   │
│ - Claude       │ │ - Glob      │ │              │
│                │ │ - Grep      │ │              │
│                │ │ - Shell     │ │              │
│                │ │ - Patch     │ │              │
└────────────────┘ └─────────────┘ └──────────────┘
```

---

## 二、目录结构设计

```
MT-AutoOptimize/
├── main.py                          # 保留: 旧版流水线入口(兼容)
├── config.json                      # 保留: 配置文件
├── config.example.json              # 保留: 配置模板
├── environment.yml                  # 更新: 新增依赖
├── pyproject.toml                   # 新增: 项目元数据 + 依赖管理
│
├── cli/                             # ★ 新增: 交互层 (对应 gemini-cli/packages/cli)
│   ├── __init__.py
│   ├── app.py                       # CLI 主入口 (Typer + Rich)
│   ├── repl.py                      # 交互式 REPL 循环
│   ├── stream_handler.py            # 流式事件消费与渲染
│   ├── tool_renderer.py             # 工具执行状态渲染 (spinner/progress)
│   ├── confirmation.py              # 用户确认对话框 (高风险操作)
│   ├── themes.py                    # 终端主题/样式
│   └── commands/                    # CLI 子命令
│       ├── __init__.py
│       ├── chat.py                  # `mt-agent chat` 交互模式
│       ├── optimize.py              # `mt-agent optimize` 一键优化
│       ├── run_slurm.py             # `mt-agent slurm` Slurm 调试
│       └── config_cmd.py            # `mt-agent config` 配置管理
│
├── core/                            # ★ 重构: 内核层 (对应 gemini-cli/packages/core)
│   ├── __init__.py
│   ├── agent.py                     # ★ 核心: AgentRunner (LangGraph ReAct 主循环)
│   ├── state.py                     # AgentState 定义 (LangGraph TypedDict)
│   ├── graph.py                     # LangGraph StateGraph 构建
│   ├── nodes/                       # LangGraph 节点实现
│   │   ├── __init__.py
│   │   ├── reasoning.py             # 推理节点: 调用 LLM, 解析 tool_calls
│   │   ├── tool_execution.py        # 工具执行节点: 调度 + 执行工具
│   │   ├── observation.py           # 观察节点: 整理工具结果, 决定是否继续
│   │   └── human_approval.py        # 人工审批节点: interrupt_before 实现
│   ├── llm.py                       # LLM 客户端封装 (LangChain ChatModel)
│   ├── scheduler.py                 # 工具调度器 (并发执行, 状态管理)
│   ├── event_bus.py                 # 事件总线 (asyncio, 层间通信)
│   ├── policy.py                    # 工具执行策略/权限引擎
│   ├── compression.py               # 上下文窗口压缩
│   ├── prompts.py                   # 系统提示词组装
│   ├── config.py                    # 保留+扩展: 配置加载
│   ├── pipeline.py                  # 保留: 旧版流水线(兼容模式)
│   ├── analyzer.py                  # 保留: 源码分析
│   ├── optimizer.py                 # 保留: 代码生成(作为 tool 的后端)
│   └── compiler.py                  # 保留: 编译封装(作为 tool 的后端)
│
├── tools/                           # ★ 新增: 工具系统 (对应 gemini-cli/packages/core/tools)
│   ├── __init__.py
│   ├── base.py                      # BaseTool 抽象基类 + 工具装饰器
│   ├── registry.py                  # ToolRegistry 注册中心
│   ├── schemas.py                   # 工具输入/输出 Pydantic Schema
│   │
│   ├── file_ops/                    # 文件操作工具组
│   │   ├── __init__.py
│   │   ├── read_file.py             # ReadFile: 读取文件内容
│   │   ├── write_file.py            # WriteFile: 写入/创建文件
│   │   ├── edit_file.py             # EditFile: 精确文本替换
│   │   ├── glob_search.py           # GlobSearch: 文件模式匹配
│   │   └── grep_search.py           # GrepSearch: 内容搜索
│   │
│   ├── shell/                       # Shell 执行工具组
│   │   ├── __init__.py
│   │   └── run_command.py           # RunShellCommand: 本地 shell 执行
│   │
│   ├── mt3000/                      # ★ MT-3000 专用工具组
│   │   ├── __init__.py
│   │   ├── analyze_source.py        # AnalyzeSource: 源码分析 (复用 analyzer.py)
│   │   ├── generate_optimized.py    # GenerateOptimized: AM/SM 代码生成 (复用 optimizer.py)
│   │   ├── compile_device.py        # CompileDevice: MT-3000 编译 (复用 compiler.py)
│   │   ├── apply_patch.py           # ApplyPatch: 应用代码补丁
│   │   └── diff_summary.py          # DiffSummary: 变更对比
│   │
│   ├── remote/                      # ★ 远程执行工具组
│   │   ├── __init__.py
│   │   ├── ssh_command.py           # SSHCommand: 远程命令执行
│   │   ├── ssh_upload.py            # SSHUpload: 文件上传
│   │   ├── ssh_download.py          # SSHDownload: 文件下载
│   │   ├── slurm_submit.py          # SlurmSubmit: 提交作业
│   │   ├── slurm_status.py          # SlurmStatus: 查询作业状态
│   │   └── slurm_fetch.py           # SlurmFetch: 拉取作业结果
│   │
│   └── agent_ops/                   # Agent 控制工具组
│       ├── __init__.py
│       ├── ask_user.py              # AskUser: 向用户提问
│       └── plan_mode.py             # PlanMode: 进入/退出计划模式
│
├── prompts/                         # 保留+扩展: Prompt 模板
│   ├── analyze_prompts.py           # 保留: 分析 prompt
│   ├── optimize_prompts.py          # 保留: 优化 prompt
│   ├── system_prompt.py             # ★ 新增: Agent 系统提示词模板
│   └── tool_prompts.py              # ★ 新增: 工具描述提示词
│
├── skills/                          # 保留: 技能资源库
│   ├── am-vectorization-templates/
│   ├── sm-cache-optimization-templates/
│   ├── hthreads-kernel-programming/
│   ├── mt3000-platform-basics/
│   └── compile-test-feedback-loop/
│
├── common/                          # 保留: 公共头文件
├── input/                           # 保留: 输入目录
├── output/                          # 保留: 输出目录
│
├── tests/                           # ★ 新增: 测试
│   ├── __init__.py
│   ├── test_tools/                  # 工具单元测试
│   ├── test_core/                   # 核心逻辑测试
│   ├── test_graph/                  # LangGraph 流程测试
│   └── conftest.py                  # pytest 公共 fixture
│
└── docs/                            # ★ 新增: 开发文档
    └── architecture.md              # 架构说明
```

---

## 三、核心数据模型

### 3.1 AgentState (LangGraph 状态定义)

```python
# core/state.py
from typing import TypedDict, Annotated, Literal
from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage

class ToolCallInfo(TypedDict):
    """单个工具调用的信息"""
    call_id: str                          # 唯一标识
    tool_name: str                        # 工具名
    arguments: dict                       # 参数
    status: Literal[
        "pending",        # 等待执行
        "awaiting_approval",  # 等待用户确认
        "executing",      # 执行中
        "success",        # 成功
        "error",          # 失败
        "cancelled"       # 被取消
    ]
    result: str | None                    # 执行结果
    error_msg: str | None                 # 错误信息

class AgentState(TypedDict):
    """LangGraph 全局状态 — ReAct 循环的数据载体"""
    # --- 会话历史 (LangGraph 内置消息累加) ---
    messages: Annotated[list[BaseMessage], add_messages]

    # --- 当前轮工具调用 ---
    pending_tool_calls: list[ToolCallInfo]     # 当前轮待执行的工具调用
    completed_tool_calls: list[ToolCallInfo]   # 当前轮已完成的工具调用

    # --- 控制流 ---
    turn_count: int                            # 当前轮次
    max_turns: int                             # 最大轮次
    should_continue: bool                      # 是否继续循环
    needs_human_approval: bool                 # 是否需要人工审批
    approval_requests: list[dict]              # 待审批项

    # --- MT-3000 专用上下文 ---
    optimization_mode: str | None              # "am" | "sm" | "auto" | None
    source_file: str | None                    # 当前优化的源文件路径
    compile_results: list[dict]                # 编译结果历史
    benchmark_results: list[dict]              # 性能测试结果历史
    current_candidate: dict | None             # 当前候选方案

    # --- 元信息 ---
    working_directory: str                     # 工作目录
    session_id: str                            # 会话 ID
```

### 3.2 事件类型

```python
# core/event_bus.py
from enum import Enum
from dataclasses import dataclass
from typing import Any

class EventType(Enum):
    """事件类型 — 对应 gemini-cli 的 ServerGeminiStreamEvent"""
    # 流式输出
    CONTENT = "content"                  # LLM 文本输出片段
    THOUGHT = "thought"                  # LLM 思考过程
    TOOL_CALL_REQUEST = "tool_call_request"  # LLM 请求调用工具

    # 工具执行
    TOOL_STATUS_UPDATE = "tool_status_update"  # 工具状态变更
    TOOL_LIVE_OUTPUT = "tool_live_output"      # 工具实时输出 (如 shell 命令)
    TOOL_CALL_COMPLETE = "tool_call_complete"  # 单个工具完成
    ALL_TOOLS_COMPLETE = "all_tools_complete"  # 所有工具完成

    # 权限确认
    CONFIRMATION_REQUEST = "confirmation_request"  # 请求用户确认
    CONFIRMATION_RESPONSE = "confirmation_response"  # 用户确认结果

    # 会话控制
    TURN_START = "turn_start"            # 新一轮开始
    TURN_END = "turn_end"                # 一轮结束
    SESSION_END = "session_end"          # 会话结束
    ERROR = "error"                      # 错误
    CONTEXT_COMPRESSED = "context_compressed"  # 上下文被压缩

@dataclass
class AgentEvent:
    type: EventType
    data: Any
    turn: int = 0
    timestamp: float = 0.0
```

---

## 四、ReAct 循环实现 (核心)

### 4.1 LangGraph 状态图定义

```python
# core/graph.py
from langgraph.graph import StateGraph, END
from core.state import AgentState

def build_agent_graph() -> StateGraph:
    """
    构建 ReAct 循环的 LangGraph 状态图

    对应 gemini-cli 的:
    - client.ts: sendMessageStream() → processTurn() 循环
    - turn.ts: Turn.run() 单轮推理

    流程图:
    ┌───────────────┐
    │   __start__   │
    └───────┬───────┘
            ▼
    ┌───────────────┐     无工具调用     ┌─────────┐
    │   reasoning   │ ──────────────────▶│  __end__  │
    │   (推理节点)   │                    └─────────┘
    └───────┬───────┘
            │ 有工具调用
            ▼
    ┌───────────────┐    需要确认    ┌────────────────┐
    │  tool_routing  │ ────────────▶│ human_approval  │
    │  (路由判断)    │              │ (人工审批)       │
    └───────┬───────┘              └───────┬─────────┘
            │ 无需确认                      │ 确认完成
            ▼                              ▼
    ┌───────────────┐
    │ tool_execution │
    │ (工具执行)     │
    └───────┬───────┘
            │ 执行完毕
            ▼
    ┌───────────────┐
    │  observation   │ ──────────────▶ reasoning (循环)
    │  (观察整合)    │
    └───────────────┘
    """
    graph = StateGraph(AgentState)

    # 添加节点
    graph.add_node("reasoning", reasoning_node)
    graph.add_node("tool_routing", tool_routing_node)
    graph.add_node("human_approval", human_approval_node)
    graph.add_node("tool_execution", tool_execution_node)
    graph.add_node("observation", observation_node)

    # 入口
    graph.set_entry_point("reasoning")

    # reasoning → 条件路由
    graph.add_conditional_edges(
        "reasoning",
        should_use_tools,           # 判断函数
        {
            "use_tools": "tool_routing",
            "final_answer": END,
        }
    )

    # tool_routing → 条件路由
    graph.add_conditional_edges(
        "tool_routing",
        needs_approval,             # 判断是否需要确认
        {
            "needs_approval": "human_approval",
            "approved": "tool_execution",
        }
    )

    # human_approval → tool_execution
    graph.add_edge("human_approval", "tool_execution")

    # tool_execution → observation
    graph.add_edge("tool_execution", "observation")

    # observation → reasoning (ReAct 循环闭合点)
    graph.add_conditional_edges(
        "observation",
        should_continue_loop,       # 判断是否继续
        {
            "continue": "reasoning",
            "stop": END,
        }
    )

    return graph.compile()
```

### 4.2 各节点实现概要

#### reasoning_node (推理节点) — 对应 gemini-cli Turn.run()

```
输入: AgentState (含 messages 历史)
处理:
  1. 组装系统提示词 (system prompt + 工具描述 + MT-3000 上下文)
  2. 调用 LLM (流式), 通过 EventBus 发送 CONTENT/THOUGHT 事件
  3. 解析 LLM 返回:
     - 如果有 tool_calls → 写入 state.pending_tool_calls
     - 如果是纯文本 → 直接作为最终回答
  4. turn_count += 1
输出: 更新后的 AgentState
```

#### tool_routing_node (路由节点) — 对应 gemini-cli PolicyEngine.check()

```
输入: AgentState (含 pending_tool_calls)
处理:
  1. 遍历 pending_tool_calls
  2. 对每个工具调用查询 PolicyEngine:
     - ALLOW → 标记为 "pending" (无需确认)
     - ASK_USER → 标记为 "awaiting_approval"
     - DENY → 标记为 "cancelled"
  3. 如果有任何 "awaiting_approval" → needs_human_approval = True
输出: 更新后的 AgentState
```

#### human_approval_node (审批节点) — 对应 gemini-cli confirmation-bus

```
输入: AgentState (含 awaiting_approval 的工具调用)
处理:
  1. 通过 EventBus 发送 CONFIRMATION_REQUEST 给 cli 层
  2. LangGraph interrupt_before 暂停图执行
  3. cli 层渲染确认对话框, 用户选择 (允许/拒绝/始终允许)
  4. 用户响应通过 graph.update_state() 回写
  5. 根据用户选择更新工具调用状态和策略
输出: 更新后的 AgentState (工具调用状态已更新)
```

#### tool_execution_node (执行节点) — 对应 gemini-cli Scheduler + ToolExecutor

```
输入: AgentState (含 approved 的工具调用)
处理:
  1. ToolScheduler 接收所有 pending 工具调用
  2. 并发执行 (asyncio.gather, 受限并发数)
  3. 每个工具:
     a. 通过 EventBus 发送 TOOL_STATUS_UPDATE (executing)
     b. ToolExecutor.execute(tool_call)
     c. 支持 live_output 回调 (如 shell 命令的实时输出)
     d. 捕获结果或异常
     e. 通过 EventBus 发送 TOOL_CALL_COMPLETE
  4. 所有完成后发送 ALL_TOOLS_COMPLETE
  5. 将结果写入 state.completed_tool_calls
输出: 更新后的 AgentState
```

#### observation_node (观察节点) — 对应 gemini-cli handleCompletedTools

```
输入: AgentState (含 completed_tool_calls)
处理:
  1. 将每个工具结果转为 ToolMessage, 追加到 messages
  2. 清空 pending_tool_calls 和 completed_tool_calls
  3. 检查是否超过 max_turns
  4. 检查上下文窗口大小, 必要时触发压缩
  5. 设置 should_continue
输出: 更新后的 AgentState (messages 已包含工具结果)
```

### 4.3 ReAct 循环完整流程 (对照 PROMPT.md 中的步骤)

```
步骤1: 用户输入 "请优化这个 GEMM 核函数"
  │
  ▼
步骤2: cli/repl.py 接收输入, 构造 HumanMessage
  │
  ▼
步骤3: core/agent.py 调用 graph.astream(state)
  │    → reasoning_node: 调用 LLM (流式)
  │
  ▼
步骤4: LLM 返回: "我需要先读取源文件"
  │    + tool_calls: [ReadFile(path="input/test.dev.c")]
  │
  ▼
步骤5: reasoning_node → EventBus → cli 层渲染 LLM 思考过程
  │
  ▼
步骤6: tool_routing_node: ReadFile → ALLOW (低风险)
  │
  ▼
步骤7: tool_execution_node: 执行 ReadFile
  │
  ▼
步骤8: ReadFile 返回文件内容
  │
  ▼
步骤9: observation_node: 将文件内容作为 ToolMessage 加入 messages
  │
  ▼
步骤10: → 回到 reasoning_node (ReAct 循环)
  │
  ▼
步骤11: LLM 分析后返回: "源码适合 AM 向量化优化, 我来生成优化代码"
  │    + tool_calls: [GenerateOptimized(mode="am", source=...)]
  │
  ▼
步骤12: (多轮循环: 生成→编译→修复→再编译...)
  │
  ▼
步骤N: LLM 返回最终答案 (无 tool_calls):
  │    "优化完成! 生成的向量化代码已通过编译, 性能提升 35%..."
  │
  ▼
步骤N+1: reasoning_node → should_use_tools → "final_answer" → END
  │
  ▼
步骤N+2: cli 层渲染最终结果
```

---

## 五、工具系统详细设计

### 5.1 工具基类

```python
# tools/base.py
from abc import ABC, abstractmethod
from pydantic import BaseModel
from typing import Literal

class ToolRiskLevel(str, Enum):
    LOW = "low"           # 只读操作, 无需确认 (ReadFile, Glob, Grep)
    MEDIUM = "medium"     # 写操作, 默认确认 (WriteFile, EditFile)
    HIGH = "high"         # 危险操作, 总是确认 (Shell, SSH, Slurm)

class BaseTool(ABC):
    name: str                        # 工具唯一名称
    description: str                 # 给 LLM 看的描述
    risk_level: ToolRiskLevel        # 风险等级 (决定是否需要确认)
    args_schema: type[BaseModel]     # Pydantic 输入 Schema

    @abstractmethod
    async def execute(self, args: BaseModel, context: dict) -> str:
        """执行工具, 返回结果文本"""
        ...

    def get_confirmation_prompt(self, args: BaseModel) -> str:
        """高风险操作时显示给用户的确认信息"""
        return f"Allow {self.name} with args: {args}?"
```

### 5.2 工具清单与风险等级

| 工具名 | 风险等级 | 对应 gemini-cli | 说明 |
|---|---|---|---|
| **文件操作** | | | |
| `read_file` | LOW | read_file | 读取文件内容 (支持行范围) |
| `write_file` | MEDIUM | write_file | 创建/覆盖文件 |
| `edit_file` | MEDIUM | edit | 精确文本替换 (old_string → new_string) |
| `glob_search` | LOW | glob | 文件名模式匹配搜索 |
| `grep_search` | LOW | grep_search | 文件内容正则搜索 |
| **Shell** | | | |
| `run_command` | HIGH | run_shell_command | 执行本地 shell 命令 |
| **MT-3000 专用** | | | |
| `analyze_source` | LOW | (无) | 分析源码推荐优化模式 |
| `generate_optimized` | MEDIUM | (无) | 调用 LLM 生成 AM/SM 优化代码 |
| `compile_device` | MEDIUM | (无) | MT-3000 交叉编译 |
| `apply_patch` | MEDIUM | (无) | 应用 diff 补丁 |
| `diff_summary` | LOW | (无) | 对比两个文件的差异 |
| **远程执行** | | | |
| `ssh_command` | HIGH | (无) | 远程执行命令 |
| `ssh_upload` | HIGH | (无) | 上传文件到远程 |
| `ssh_download` | MEDIUM | (无) | 从远程下载文件 |
| `slurm_submit` | HIGH | (无) | 提交 Slurm 作业 |
| `slurm_status` | LOW | (无) | 查询作业状态 |
| `slurm_fetch` | LOW | (无) | 拉取作业结果 |
| **Agent 控制** | | | |
| `ask_user` | LOW | ask_user | 向用户提问 |
| `plan_mode` | LOW | enter_plan_mode | 进入计划模式 |

### 5.3 工具注册与 LLM 绑定

```python
# tools/registry.py
class ToolRegistry:
    """
    工具注册中心 — 对应 gemini-cli tool-registry.ts
    负责: 注册工具、生成 LLM function schema、按名查找工具
    """
    def __init__(self):
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool):
        self._tools[tool.name] = tool

    def get_tool(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def get_langchain_tools(self) -> list:
        """转为 LangChain StructuredTool 列表, 用于 bind_tools()"""
        ...

    def get_openai_functions(self) -> list[dict]:
        """转为 OpenAI function calling 格式, 用于非 LangChain 场景"""
        ...
```

LLM 绑定方式:
```python
# core/llm.py
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(model="deepseek-chat", ...)
llm_with_tools = llm.bind_tools(registry.get_langchain_tools())
```

---

## 六、CLI 交互层设计

### 6.1 入口与命令结构

```python
# cli/app.py (Typer)
import typer
app = typer.Typer(name="mt-agent")

@app.command()
def chat(model: str = "deepseek-chat"):
    """交互式 Agent 对话 (ReAct 模式)"""
    repl = AgentREPL(model=model)
    repl.run()

@app.command()
def optimize(
    input_file: str,
    mode: str = "auto",
    strategy: str = "am,sm",
):
    """一键优化模式 (自动完成整个优化流程)"""
    ...

@app.command()
def slurm(script: str):
    """Slurm 调试命令"""
    ...
```

### 6.2 REPL 交互循环

```python
# cli/repl.py
class AgentREPL:
    """
    交互式 REPL — 对应 gemini-cli 的 App 组件 + useGeminiStream

    流程:
    1. 显示欢迎信息
    2. 读取用户输入 (支持多行, 快捷键)
    3. 创建/更新 AgentState
    4. 调用 agent_graph.astream_events()
    5. 消费事件流, 实时渲染:
       - CONTENT → 流式打印 LLM 输出 (Markdown 渲染)
       - THOUGHT → 折叠显示思考过程
       - TOOL_CALL_REQUEST → 显示工具调用意图
       - TOOL_STATUS_UPDATE → 更新 spinner
       - TOOL_LIVE_OUTPUT → 实时显示 (如 shell 输出)
       - CONFIRMATION_REQUEST → 渲染确认对话框
       - ALL_TOOLS_COMPLETE → 清除 spinner
    6. 等待 agent 完成 或 用户中断 (Ctrl+C)
    7. 回到步骤 2
    """
    async def run(self):
        console = Console()
        event_bus = EventBus()
        agent = AgentRunner(config, event_bus)

        while True:
            user_input = await self.read_input()
            if user_input in ("/exit", "/quit"):
                break

            async for event in agent.run(user_input):
                self.render_event(event, console)
```

### 6.3 用户确认交互

```python
# cli/confirmation.py
class ConfirmationDialog:
    """
    高风险操作确认对话框
    对应 gemini-cli 的 confirmation-bus + CLI 确认渲染

    显示格式:
    ┌─────────────────────────────────────┐
    │ ⚠ 需要确认                          │
    │                                     │
    │ 工具: run_command                    │
    │ 命令: MT-3000-gcc -c -Wall ...      │
    │                                     │
    │ [y] 允许  [a] 始终允许  [n] 拒绝    │
    └─────────────────────────────────────┘
    """
    async def prompt(self, tool_name: str, args: dict) -> ConfirmationResult:
        ...
```

---

## 七、系统提示词设计

### 7.1 System Prompt 组装 (对应 gemini-cli prompts.ts)

```python
# core/prompts.py
def build_system_prompt(
    tools: list[BaseTool],
    mt3000_context: dict | None = None,
    skills_context: str | None = None,
) -> str:
    """
    组装完整系统提示词, 结构:

    1. 角色定义
    2. 核心能力说明
    3. 工具使用规范
    4. MT-3000 平台知识 (从 skills/ 加载)
    5. 安全约束
    6. 输出格式要求
    """
    ...
```

### 7.2 提示词模板 (核心)

```markdown
# 系统提示词核心内容 (prompts/system_prompt.py)

你是 MT-3000 Coding Agent，一个面向迈创超算平台的代码优化专家。

## 核心能力
- 分析 C/C++ 核函数，判断适合 AM 向量化还是 SM 缓存优化
- 使用 MT-3000 专用向量指令集和缓存 API 生成高性能代码
- 通过编译-测试-迭代循环持续优化代码
- 通过 SSH/Slurm 在超算集群上运行基准测试

## 工作流程
1. 先理解用户需求和源码结构
2. 分析源码特征 (循环、访存模式、分支等)
3. 制定优化策略 (AM 向量化 / SM 缓存 / 两者结合)
4. 生成优化代码并编译验证
5. 如有错误，根据编译信息迭代修复
6. [可选] 提交到集群运行基准测试
7. 汇总结果，报告优化效果

## 工具使用规范
- 先读后写：修改文件前必须先读取
- 最小变更：只修改必要部分
- 编译验证：每次代码变更后必须编译检查
- 危险操作：shell 命令、SSH、Slurm 操作需用户确认

## MT-3000 平台知识
{mt3000_platform_context}

## 可用工具
{tool_descriptions}
```

---

## 八、分阶段开发计划

### Phase 0: 基础设施搭建 (预计 3 天)

**目标**: 搭建项目骨架，跑通最小 ReAct 循环

| 任务 | 文件 | 详情 |
|---|---|---|
| P0-1. 项目初始化 | `pyproject.toml`, `environment.yml` | 添加依赖: langgraph, langchain-openai, rich, typer, pydantic, asyncio, paramiko |
| P0-2. AgentState 定义 | `core/state.py` | 定义 TypedDict, 最小字段: messages, pending_tool_calls, completed_tool_calls, turn_count, should_continue |
| P0-3. EventBus 实现 | `core/event_bus.py` | asyncio 事件总线, 支持 publish/subscribe, 事件类型枚举 |
| P0-4. LLM 客户端封装 | `core/llm.py` | LangChain ChatOpenAI 封装, 支持流式, 多模型配置 |
| P0-5. 工具基类 + 注册 | `tools/base.py`, `tools/registry.py` | BaseTool ABC, ToolRegistry, LangChain tool 转换 |
| P0-6. 最小工具集 | `tools/file_ops/read_file.py` | 先实现 ReadFile 一个工具即可 |
| P0-7. LangGraph 图构建 | `core/graph.py` | StateGraph 定义, 节点桩函数 |
| P0-8. reasoning_node | `core/nodes/reasoning.py` | 调用 LLM, 解析 tool_calls, 流式事件 |
| P0-9. tool_execution_node | `core/nodes/tool_execution.py` | 简单同步执行, 结果写入 state |
| P0-10. observation_node | `core/nodes/observation.py` | 工具结果→ToolMessage, 循环判断 |
| P0-11. 最小 CLI | `cli/app.py`, `cli/repl.py` | 简单 input() 循环 + print 输出 |

**验收标准**: 能在终端输入问题, Agent 调用 ReadFile 读取文件后给出回答 (完成一次完整的 Reason→Act→Observe 循环)

---

### Phase 1: 完善工具系统 (预计 5 天)

**目标**: 实现全部本地工具, Agent 能自主读写文件、搜索代码、执行命令

| 任务 | 文件 | 详情 |
|---|---|---|
| P1-1. WriteFile | `tools/file_ops/write_file.py` | 创建/覆盖文件, 自动创建目录 |
| P1-2. EditFile | `tools/file_ops/edit_file.py` | old_string→new_string 精确替换, 唯一性检查 |
| P1-3. GlobSearch | `tools/file_ops/glob_search.py` | glob 模式匹配, 返回文件列表 |
| P1-4. GrepSearch | `tools/file_ops/grep_search.py` | 正则搜索文件内容, 支持上下文行 |
| P1-5. RunCommand | `tools/shell/run_command.py` | asyncio.subprocess, 超时控制, 实时输出, 输出截断 |
| P1-6. AskUser | `tools/agent_ops/ask_user.py` | 中断图执行, 向用户提问 |
| P1-7. 权限策略引擎 | `core/policy.py` | 基于 risk_level 判断, 支持 "始终允许" 规则缓存 |
| P1-8. human_approval_node | `core/nodes/human_approval.py` | LangGraph interrupt, 确认对话框集成 |
| P1-9. tool_routing_node | `core/nodes/tool_routing.py` (合入 `tool_execution.py` 的前置逻辑亦可) | 策略检查, 分流 |
| P1-10. 并发工具调度 | `core/scheduler.py` | asyncio.Semaphore 控制并发, 状态管理 |

**验收标准**: Agent 能自主执行多步操作 — 搜索文件→读取→编辑→执行 shell 命令, 高风险操作需用户确认

---

### Phase 2: MT-3000 编译优化工具 (预计 4 天)

**目标**: 将现有优化流水线封装为工具, Agent 能自主完成 "分析→生成→编译→修复" 循环

| 任务 | 文件 | 详情 |
|---|---|---|
| P2-1. AnalyzeSource 工具 | `tools/mt3000/analyze_source.py` | 封装 `core/analyzer.py`, 输出结构化分析结果 |
| P2-2. GenerateOptimized 工具 | `tools/mt3000/generate_optimized.py` | 封装 `core/optimizer.py`, 支持 AM/SM 模式 |
| P2-3. CompileDevice 工具 | `tools/mt3000/compile_device.py` | 封装 `core/compiler.py`, 结构化编译结果 |
| P2-4. ApplyPatch 工具 | `tools/mt3000/apply_patch.py` | unified diff 格式, 安全应用+回滚 |
| P2-5. DiffSummary 工具 | `tools/mt3000/diff_summary.py` | 两文件对比, 统计变更 |
| P2-6. MT-3000 系统提示词 | `prompts/system_prompt.py` | 加载 skills/ 中的平台知识、模板、API 文档 |
| P2-7. 优化模式提示词 | `prompts/optimize_prompts.py` (扩展) | 结合 Agent 工具调用格式重写 |
| P2-8. 集成测试 | `tests/test_graph/test_optimize_flow.py` | 模拟完整 "分析→生成→编译→修复" 流程 |

**验收标准**: 用户输入 "优化 input/test.dev.c", Agent 自主完成分析→生成→编译→迭代修复, 最终输出编译通过的优化代码

---

### Phase 3: 远程执行与 Slurm 集成 (预计 5 天)

**目标**: Agent 能通过 SSH 上传代码到集群, 提交 Slurm 作业, 拉取并分析结果

| 任务 | 文件 | 详情 |
|---|---|---|
| P3-1. SSH 基础封装 | `tools/remote/ssh_base.py` | paramiko/asyncssh 连接池, 密钥认证, 连接复用 |
| P3-2. SSHCommand 工具 | `tools/remote/ssh_command.py` | 远程命令执行, 超时, 输出捕获 |
| P3-3. SSHUpload 工具 | `tools/remote/ssh_upload.py` | SCP/SFTP 文件上传, 支持目录打包 |
| P3-4. SSHDownload 工具 | `tools/remote/ssh_download.py` | 远程文件下载 |
| P3-5. SlurmSubmit 工具 | `tools/remote/slurm_submit.py` | 生成 sbatch 脚本, 提交作业, 返回 job_id |
| P3-6. SlurmStatus 工具 | `tools/remote/slurm_status.py` | squeue/sacct 查询, 结构化状态 |
| P3-7. SlurmFetch 工具 | `tools/remote/slurm_fetch.py` | 下载 stdout/stderr, 解析性能指标 |
| P3-8. Slurm 作业模板 | `templates/slurm/` | benchmark, correctness_check 模板 |
| P3-9. SSH 配置扩展 | `config.json` 扩展 | ssh_host, ssh_user, ssh_key, remote_dir, slurm_partition |
| P3-10. 端到端验证 | `tests/test_graph/test_remote_flow.py` | 本地编译→上传→提交→轮询→拉取结果 |

**验收标准**: Agent 能完成完整链路: 本地编译通过→SSH 上传→Slurm 提交→轮询等待→拉取结果→分析性能/正确性

---

### Phase 4: CLI 交互体验 (预计 4 天)

**目标**: 完善终端 UI, 达到接近 gemini-cli / Claude Code 的交互体验

| 任务 | 文件 | 详情 |
|---|---|---|
| P4-1. Rich Markdown 渲染 | `cli/stream_handler.py` | 流式 Markdown 渲染, 代码高亮 |
| P4-2. 工具执行状态面板 | `cli/tool_renderer.py` | Spinner, 进度条, 实时输出面板 |
| P4-3. 确认对话框 | `cli/confirmation.py` | 带语法高亮的命令预览, 快捷键 |
| P4-4. 多行输入 | `cli/repl.py` | prompt_toolkit 集成, 支持粘贴、历史 |
| P4-5. 会话管理 | `cli/commands/chat.py` | /history, /clear, /save, /load 子命令 |
| P4-6. 主题与配色 | `cli/themes.py` | 亮色/暗色主题 |
| P4-7. 优化报告渲染 | `cli/report.py` | 表格展示编译结果、性能对比、正确性 |
| P4-8. 错误处理与优雅退出 | 全局 | Ctrl+C 中断当前操作(不退出), 异常恢复 |

**验收标准**: 流式输出有 Markdown 渲染, 工具执行有 spinner 和实时输出, 高风险操作有清晰的确认提示

---

### Phase 5: 高级 Agent 能力 (预计 5 天)

**目标**: 上下文管理、迭代优化策略、候选管理

| 任务 | 文件 | 详情 |
|---|---|---|
| P5-1. 上下文压缩 | `core/compression.py` | token 计数, 80% 阈值触发, LLM 摘要压缩 |
| P5-2. 最大轮次控制 | `core/agent.py` | 可配置 max_turns, 超限优雅终止 |
| P5-3. 循环检测 | `core/loop_detection.py` | 检测重复工具调用模式, 提前终止 |
| P5-4. 候选管理 | `core/candidate_manager.py` | 保留 top-K 候选, 按性能/正确性排序 |
| P5-5. 失败模式分类 | `core/failure_classifier.py` | 编译失败/运行失败/正确性失败/无提升, 结构化记录 |
| P5-6. 策略自适应 | `core/strategy.py` | 根据失败模式动态调整提示词/约束 |
| P5-7. 结果存档 | `core/result_store.py` | 每轮结构化 JSON: 轮次、策略、补丁、编译/性能/正确性 |
| P5-8. 计划模式 | `tools/agent_ops/plan_mode.py` | 先输出计划 MD, 用户确认后再执行 |
| P5-9. 会话持久化 | `core/session.py` | checkpoint 保存/恢复 (LangGraph checkpointer) |

**验收标准**: Agent 能多轮迭代优化, 自动管理候选方案, 失败时智能调整策略, 支持中断恢复

---

### Phase 6: 多模型支持与扩展 (预计 3 天)

**目标**: 支持多种 LLM 后端, 不同任务使用不同模型

| 任务 | 文件 | 详情 |
|---|---|---|
| P6-1. 模型路由 | `core/llm.py` 扩展 | 分析用 Qwen, 生成用 DeepSeek, Agent 推理用 GPT-4o/Claude |
| P6-2. 模型降级 | `core/llm.py` | 配额/错误时自动切换备选模型 |
| P6-3. 统一 OpenAI 兼容 | `core/llm.py` | 所有模型通过 OpenAI-compatible API 接入 |
| P6-4. 配置扩展 | `config.json` | 新增 agent_llm 配置段, 独立于 analyze_llm 和 code_llm |
| P6-5. 流式回调统一 | `core/llm.py` | 不同模型的流式 API 差异抹平 |

**验收标准**: 能配置不同模型负责不同任务, 模型不可用时自动降级

---

## 九、关键技术决策

### 9.1 为什么用 LangGraph 而非手写循环?

| 对比项 | 手写 while 循环 | LangGraph StateGraph |
|---|---|---|
| ReAct 循环 | 需自己管理状态流转 | 声明式节点+条件边, 天然支持 |
| 人工审批中断 | 需 hack (如 input() 阻塞) | `interrupt_before` 原生支持 |
| 持久化/恢复 | 需自己实现序列化 | 内置 checkpointer (SQLite/Postgres) |
| 并行工具执行 | 需自己管理 asyncio | 节点内自由使用 async |
| 可视化/调试 | 无 | LangGraph Studio 可视化 |
| 流式事件 | 需自己实现 | `astream_events()` 原生支持 |

### 9.2 CLI 框架选择

| 选项 | 优点 | 缺点 | 决定 |
|---|---|---|---|
| Rich + Typer | 成熟, 丰富的终端 UI, 类型安全 | 不是响应式 | **采用** |
| Textual | 类 React 的终端 UI 框架 | 学习曲线较陡 | 备选 (Phase 4 评估) |
| prompt_toolkit | 强大的输入处理 | UI 能力弱 | 仅用于输入 |

### 9.3 SSH 库选择

| 选项 | 优点 | 缺点 | 决定 |
|---|---|---|---|
| paramiko | 成熟稳定, 文档丰富 | 同步 API | **采用** (用 asyncio.to_thread 包装) |
| asyncssh | 原生 async | 社区较小 | 备选 |

### 9.4 与现有代码的兼容策略

**原则: 保留现有模块, 封装为工具**

```
现有模块                    封装为工具
──────────                 ──────────
core/analyzer.py       →   tools/mt3000/analyze_source.py (调用 analyzer)
core/optimizer.py      →   tools/mt3000/generate_optimized.py (调用 optimizer)
core/compiler.py       →   tools/mt3000/compile_device.py (调用 compiler)
core/pipeline.py       →   保留, 作为 "兼容模式" 入口
main.py                →   保留, 旧版入口
```

新的 Agent 入口: `cli/app.py` → `mt-agent chat` / `mt-agent optimize`
旧的流水线入口: `main.py` → 原有 pipeline 不受影响

---

## 十、依赖清单

```toml
# pyproject.toml [project.dependencies]
[project]
name = "mt-autooptimize"
version = "0.2.0"
requires-python = ">=3.10"

dependencies = [
    # Agent 核心
    "langgraph>=0.3",
    "langchain>=0.3",
    "langchain-openai>=0.3",
    "langchain-community>=0.3",

    # CLI
    "typer>=0.12",
    "rich>=13.0",
    "prompt-toolkit>=3.0",

    # 数据校验
    "pydantic>=2.0",

    # 远程执行
    "paramiko>=3.0",

    # 现有依赖
    "openai>=1.0",
    "pyyaml>=6.0",
    "tenacity>=8.0",
    "requests>=2.31",

    # 工具
    "tiktoken>=0.7",            # token 计数
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "pytest-mock>=3.0",
]

[project.scripts]
mt-agent = "cli.app:app"
```

---

## 十一、测试策略

### 11.1 单元测试

```
tests/
├── test_tools/
│   ├── test_read_file.py         # 文件读取各种边界
│   ├── test_write_file.py        # 文件写入、目录创建
│   ├── test_edit_file.py         # 精确替换、唯一性
│   ├── test_glob_search.py       # 模式匹配
│   ├── test_grep_search.py       # 正则搜索
│   ├── test_run_command.py       # 命令执行、超时、截断
│   └── test_mt3000_tools.py      # MT-3000 工具 (mock 编译器)
│
├── test_core/
│   ├── test_event_bus.py         # 事件发布/订阅
│   ├── test_policy.py            # 权限策略
│   ├── test_scheduler.py         # 并发调度
│   ├── test_compression.py       # 上下文压缩
│   └── test_llm.py               # LLM 客户端 (mock API)
│
├── test_graph/
│   ├── test_minimal_loop.py      # 最小 ReAct 循环 (1轮)
│   ├── test_multi_turn.py        # 多轮循环
│   ├── test_tool_approval.py     # 人工审批流程
│   ├── test_optimize_flow.py     # 完整优化流程 (mock)
│   └── test_error_recovery.py    # 错误恢复
│
└── conftest.py                   # mock LLM, mock 编译器, 临时目录
```

### 11.2 集成测试

- **本地集成**: 不需要 MT-3000 环境, 用 mock 编译器验证完整流程
- **远程集成**: 需要 SSH 可达的测试机器, 验证 SSH + Slurm 工具链
- **端到端**: 需要真实 MT-3000 环境, 验证完整优化→编译→运行→性能对比

---

## 十二、里程碑与验收

| 里程碑 | 包含 Phase | 核心验收标准 | 预估工时 |
|---|---|---|---|
| **M0: 最小可用** | Phase 0 | 一次完整的 ReAct 循环 (Reason→Act→Observe) | 3 天 |
| **M1: 本地 Agent** | Phase 0+1 | 自主读写文件+执行命令, 有权限确认 | 8 天 |
| **M2: 优化 Agent** | Phase 0+1+2 | 自主完成 MT-3000 代码优化 (分析→生成→编译→修复) | 12 天 |
| **M3: 全链路 Agent** | Phase 0-3 | 本地编译→远程提交→性能验证 完整链路 | 17 天 |
| **M4: 产品级** | Phase 0-4 | 接近 gemini-cli/Claude Code 的交互体验 | 21 天 |
| **M5: 智能迭代** | Phase 0-5 | 多轮迭代、候选管理、策略自适应 | 26 天 |
| **M6: 完整版** | Phase 0-6 | 多模型支持、模型降级、完整测试覆盖 | 29 天 |

---

## 十三、快速启动指南 (Phase 0 实施步骤)

### Step 1: 创建项目骨架

```bash
# 创建目录
mkdir -p cli/commands core/nodes tools/{file_ops,shell,mt3000,remote,agent_ops} tests/{test_tools,test_core,test_graph}

# 创建 __init__.py
find cli core tools tests -type d -exec touch {}/__init__.py \;
```

### Step 2: 安装依赖

```bash
pip install langgraph langchain langchain-openai typer rich pydantic prompt-toolkit tiktoken
```

### Step 3: 实现最小循环

按以下顺序实现, 每完成一步验证一次:

```
core/state.py           → 定义 AgentState
core/event_bus.py       → 实现 EventBus
core/llm.py             → 封装 LLM
tools/base.py           → BaseTool + ToolRegistry
tools/file_ops/read_file.py → ReadFile 工具
core/nodes/reasoning.py → 推理节点
core/nodes/tool_execution.py → 工具执行节点
core/nodes/observation.py → 观察节点
core/graph.py           → 组装 StateGraph
core/agent.py           → AgentRunner 入口
cli/app.py + cli/repl.py → 最小 REPL
```

### Step 4: 验证

```bash
# 启动 Agent
python -m cli.app chat

# 输入测试
> 请读取 input/test.dev.c 的内容并告诉我这段代码做了什么

# 预期行为:
# 1. Agent 调用 ReadFile 读取文件
# 2. Agent 分析文件内容
# 3. Agent 输出分析结果
```

---

## 十四、风险与应对

| 风险 | 影响 | 应对措施 |
|---|---|---|
| LLM 不稳定返回非法 tool_calls | Agent 循环中断 | 1. 严格校验 tool_calls 格式 2. 错误时 retry 3. 降级为纯文本模式 |
| 上下文窗口溢出 | Agent 丢失关键信息 | 1. Phase 5 的压缩机制 2. 控制工具输出长度 (截断) 3. 增量传递而非全量 |
| MT-3000 编译环境不可用 | 无法验证生成代码 | 1. mock 编译器用于开发/测试 2. 编译工具优雅降级 (只生成不编译) |
| SSH/Slurm 连接不稳定 | 远程执行链路断裂 | 1. 连接池 + 重试 2. 作业状态持久化 3. 断点恢复 |
| Agent 陷入无限循环 | 资源浪费 | 1. max_turns 硬限制 2. 循环检测 (重复工具调用模式) 3. 超时终止 |
| 多模型 API 格式差异 | tool_calls 解析失败 | 1. LangChain 统一抽象 2. 针对各模型的 adapter |

---

## 十五、术语对照表

| 中文 | 英文 | gemini-cli 对应 | 本项目对应 |
|---|---|---|---|
| 推理 | Reasoning | Turn.run() + LLM 调用 | reasoning_node |
| 行动 | Action | ToolExecutor.execute() | tool_execution_node |
| 观察 | Observation | handleCompletedTools | observation_node |
| 工具调用请求 | ToolCallRequest | ServerGeminiStreamEvent.ToolCallRequest | state.pending_tool_calls |
| 工具调用结果 | ToolCallResponse | ToolCallResponseInfo | state.completed_tool_calls |
| 事件总线 | EventBus | MessageBus + coreEvents | EventBus (asyncio) |
| 策略引擎 | PolicyEngine | PolicyEngine | PolicyEngine |
| 工具注册中心 | ToolRegistry | ToolRegistry | ToolRegistry |
| 上下文压缩 | Compression | ChatCompressionService | compression.py |
| 循环检测 | Loop Detection | loop detection service | loop_detection.py |
