# Architecture

> 参考 [gemini-cli](https://github.com/GoogleCloudPlatform/gemini-cli) 的架构思想

## 设计哲学

### 1. 分层解耦

整个系统分为三层

```
  ┌───────────────────────────────┐
  │           CLI 层               │  用户看到什么
  │      交互、渲染、命令            │
  ├───────────┬───────────────────┤
  │           │  EventBus          │  层间如何通信
  │           ↕                    │
  │          Core 层               │  Agent 如何思考
  │    ReAct 循环、LLM、状态       │
  ├───────────┬───────────────────┤
  │           │  Registry          │  工具如何接入
  │           ↓                    │
  │         Tools 层               │  Agent 能做什么
  │    工具定义、策略、执行          │
  └───────────────────────────────┘
```

每一层只知道自己的下一层，不知道自己的上一层。这意味着：

- **替换 CLI 不影响 Core** — 可以换成 Web UI、API 服务，Core 层无感知
- **替换 LLM 不影响工具** — 从 DeepSeek 换到 GPT，工具层无需改动
- **新增工具不影响循环** — 注册一个新工具，ReAct 循环自动可用

### 2. 事件驱动

gemini-cli 用 `message-bus` 做层间通信，此项目用 **EventBus** 实现同样的思想。

Core 层的节点在执行过程中**主动推送事件**（如 LLM 输出了一段文字、工具开始执行、工具执行完毕），CLI 层通过订阅这些事件实时渲染。两层之间只有事件流。

```
Core 节点执行中 ──emit──> EventBus ──callback──> CLI 实时渲染
```

详见 → [EventBus.md](./EventBus.md)

### 3. ReAct 循环：思考 → 行动 → 观察

Agent 的核心智能来自 **ReAct (Reasoning + Acting)** 模式，用 LangGraph 状态图实现：

```
         ┌─────────┐
         │reasoning │  LLM 思考，决定是否需要工具
         └────┬─────┘
              │
        有 tool_calls？
       ╱              ╲
     是                否
      ↓                ↓
┌────────────┐     ┌──────┐
│tool_routing│     │ END  │
└─────┬──────┘     └──────┘
      │
  风险等级？
  ╱        ╲
low      medium/high
  ↓          ↓
  │    ┌──────────────┐
  │    │human_approval│  用户确认
  │    └──────┬───────┘
  ↓           ↓
┌──────────────────┐
│ tool_execution   │  执行工具
└────────┬─────────┘
         ↓
┌──────────────────┐
│  observation     │  整合结果，回到 reasoning
└────────┬─────────┘
         │
         └──→ reasoning (循环闭合)
```

参考论文[ReAct: Synergizing Reasoning and Acting in Language Models](https://arxiv.org/abs/2210.03629)

详见 [ReActLoop.md](./ReActLoop.md)

### 4. 工具注册

Agent 能做什么，取决于注册了哪些工具。工具系统的设计原则：

- **统一接口** — 所有工具继承同一个抽象基类，实现 `execute()` 方法
- **Schema 自动生成** — 工具的参数定义用 Pydantic，自动转为 LLM 的 function-calling schema
- **风险分级** — 每个工具有风险等级（low / medium / high），决定是否需要用户确认
- **回调解耦** — Core 层不直接 import 任何具体工具，通过 `executor` 回调间接调用

新增一个工具只需要：定义参数 schema → 实现 execute → 注册。ReAct 循环、风险策略、UI 渲染全部自动适配。

### 5. 与 gemini-cli 的对照

我们的设计直接参考了 gemini-cli 的分层思想，但用 Python 生态重新实现：

| 概念 | gemini-cli (TypeScript) | 本项目 (Python) |
|------|------------------------|----------------|
| Agent 循环 | GeminiClient + Turn | LangGraph StateGraph |
| 单轮推理 | Turn.run() | reasoning 节点 |
| 工具调度 | Scheduler + ToolExecutor | tool_execution 节点 |
| 权限控制 | PolicyEngine | tool_routing 节点 + policy |
| 用户确认 | confirmation-bus + interrupt | human_approval 节点 + LangGraph interrupt |
| 层间通信 | message-bus | EventBus |
| 工具注册 | tool-registry.ts | ToolRegistry |
| 流式渲染 | React/Ink + useGeminiStream | Rich + EventBus 订阅 |

核心差异在于：gemini-cli 自己管理循环迭代，我们把这部分交给 **LangGraph 的状态图引擎**，获得内置的状态持久化、interrupt/resume、和可视化调试支持。

## 依赖规则

```
CLI 层  ──→  Core 层  ──→  Tools 层
                │
                └──→  Prompts (提示词模板)
```

三条铁律：

1. **禁止反向依赖** — Tools 不 import Core，Core 不 import CLI
2. **单点接入** — CLI 只通过一个 `AgentRuntime` 对象访问 Core 的全部能力
3. **回调接入** — Core 通过 `executor` 回调使用工具，不直接依赖具体工具类
