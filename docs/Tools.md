# Tools

此文档说明 Tool 模块的设计与调用链路，帮助理解现有工具实现和新增工具的步骤。

## 目录结构

```
tools/
├── __init__.py          # 导出 + create_default_tools() 集中注册
├── base.py              # BaseTool 抽象基类、ToolResult、ToolRiskLevel
├── registry.py          # ToolRegistry — 注册 / 查找 / schema / 执行
├── policy.py            # 风险等级映射表（供 tool_routing 查表）
└── file_ops/
    ├── read_file.py     # ReadFileTool  (LOW)
    ├── write_file.py    # WriteFileTool (MEDIUM)
    └── ls.py            # LsTool        (LOW)
```

## 核心概念

### ToolRiskLevel — 风险等级

决定工具调用是否需要用户确认：

| 等级 | 行为 | 典型场景 |
|------|------|----------|
| `LOW` | 自动执行，无需确认 | 读文件、列目录 |
| `MEDIUM` | 需要用户确认 | 写文件、编辑文件 |
| `HIGH` | 需要用户确认 + 高亮警告 | Shell 命令、远程操作 |

风险等级有两个来源，**工具自身声明**（`BaseTool.risk_level` 属性）和 **全局策略表**（`policy.py` 中的 `DEFAULT_TOOL_RISK`）。`tool_routing` 节点在路由时查表决定走自动执行还是人工审批。

### ToolResult — 执行结果

每次工具执行统一返回 `ToolResult`，面向两个消费方：

```python
@dataclass
class ToolResult:
    output: str                        # → LLM 看到的文本
    display: str = ""                  # → CLI 展示给用户的摘要
    error: str | None = None           # → 非 None 表示失败
    metadata: dict[str, Any] = {}      # → 结构化富数据（如 DiffResult）
```

- `output` 会被转成 `ToolMessage.content`，回写到消息历史供 LLM 下一轮推理
- `metadata` 不进入消息历史，而是通过 EventBus 推送给 CLI 层做渲染（如 diff 彩色展示）

### ToolCallInfo — 调用状态

一次工具调用在 ReAct 循环中的完整生命周期：

```
pending → awaiting_approval → pending → executing → success / error
                  ↓
              cancelled（用户拒绝）
```

```python
class ToolCallInfo(TypedDict):
    call_id: str
    tool_name: str
    arguments: dict
    status: Literal[
        "pending",
        "awaiting_approval",
        "executing",
        "success",
        "error",
        "cancelled",
    ]
    result: str | None
    error_msg: str | None
```

## 调用链路

从 LLM 决定调用工具到结果回写消息历史的完整路径：

```
reasoning_node
  │  LLM 返回 tool_calls: [{name: "write_file", args: {...}}]
  │  → 写入 state.pending_tool_calls
  ▼
tool_routing_node
  │  查 policy.py 风险表 → 标记 status
  │  LOW → 保持 "pending"（直接放行）
  │  MEDIUM/HIGH → 标记 "awaiting_approval"
  ▼
human_approval_node（仅 MEDIUM/HIGH 走此节点）
  │  LangGraph interrupt → CLI 渲染确认对话框
  │  用户批准 → "pending"  /  用户拒绝 → "cancelled"
  ▼
tool_execution_node
  │  筛选 status=="pending" 的调用
  │  executor(tool_name, arguments) → ToolResult
  │  → 写入 state.completed_tool_calls
  ▼
observation_node
  │  completed_tool_calls → ToolMessage 回写消息历史
  │  清空 pending / completed（瞬态缓冲区）
  ▼
reasoning_node（下一轮，LLM 看到 ToolMessage 决定继续或结束）
```

## 新增工具

### Step 1: 定义参数 Schema

继承 `BaseModel`，字段的 `description` 会被自动提取到 LLM function-calling schema：

```python
from pydantic import BaseModel, Field

class MyToolArgs(BaseModel):
    file_path: str = Field(description="目标文件路径（相对于工作区）")
    verbose: bool = Field(default=False, description="是否输出详细信息")
```

### Step 2: 实现工具类

继承 `BaseTool`，设置四个类属性，实现 `execute()` 方法：

```python
from tools.base import BaseTool, ToolResult, ToolRiskLevel

class MyTool(BaseTool):
    name = "my_tool"
    description = "这段描述会被 LLM 看到，直接影响 LLM 何时选择此工具"
    risk_level = ToolRiskLevel.LOW
    args_schema = MyToolArgs

    def __init__(self, *, workspace: str | Path | None = None) -> None:
        self.workspace = Path(workspace or os.getcwd()).resolve()

    async def execute(self, *, file_path: str, verbose: bool = False) -> ToolResult:
        # 实现逻辑...
        return ToolResult(
            output="LLM 看到的执行结果文本",
            display="CLI 展示的简短摘要",
            metadata={"key": "可选的结构化数据"},
        )
```

**关键点**：
- `description` 直接决定 LLM 的工具选择行为，需精心编写
- `execute` 参数名必须与 `args_schema` 的字段名一致
- 涉及文件路径的工具必须做 workspace 边界校验（防路径越界）
- 失败时返回 `ToolResult(output="", error="错误描述")` 而非抛异常

### Step 3: 注册

在 `tools/__init__.py` 的 `create_default_tools` 中添加一行：

```python
def create_default_tools(*, workspace: str) -> list[BaseTool]:
    return [
        ReadFileTool(workspace=workspace),
        WriteFileTool(workspace=workspace),
        LsTool(workspace=workspace),
        MyTool(workspace=workspace),  # ← 新增
    ]
```

### Step 4: 配置风险等级（可选）

如果工具的 `risk_level` 与 `policy.py` 中的全局策略表不一致，或该工具不在表中，在 `policy.py` 中补充：

```python
DEFAULT_TOOL_RISK: dict[str, str] = {
    # ...
    "my_tool": "low",  # ← 新增
}
```

未在表中的工具默认风险为 `DEFAULT_UNKNOWN_RISK = "medium"`。

## 已实现工具一览

### File System

| 工具 | 风险 | 说明 |
|------|------|------|
| `read_file` | LOW | 读取文件内容，支持行范围、自动截断 |
| `write_file` | MEDIUM | 写入/创建文件，返回 diff 供 CLI 渲染 |
| `ls` | LOW | 列出目录内容，自动跳过 `.git` 等无关目录 |
| `glob` | LOW | 查找匹配 glob 模式的文件 |
| `grep` | LOW | 搜索文件内容中的正则表达式，返回匹配行和行号 |
| `edit_file` | MEDIUM | 替换文件中的文本，支持精确/灵活/正则匹配，返回 diff |
