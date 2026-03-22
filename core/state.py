from typing import TypedDict, Annotated, Literal
from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage

class ToolCallInfo(TypedDict):
    """单个工具调用信息"""
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

class AgentState(TypedDict):
    """ReAct循环中Agent的状态"""
    # 会话历史消息
    message: Annotated[list[BaseMessage], add_messages]

    # 当轮工具调用情况
    pending_tool_calls: list[ToolCallInfo]      # 当前轮待执行的工具调用
    completed_tool_calls: list[ToolCallInfo]    # 当前轮已完成的工具调用

    # 控制流
    turn_count: int                             # 当前轮次
    max_turns: int                              # 最大轮次
    should_continue: bool                       # 是否继续循环
    needs_human_approval: bool                  # 是否需要人工审批
    approval_requests: list[dict]               # 待审批项

    # MT-3000相关
    optimization_mode: str | None               # 优化模式
    source_file: str | None                     # 源文件路径
    compile_results: list[dict]                 # 编译结果历史
    benchmark_results: list[dict]               # 基准测试结果历史
    current_candidates: dict | None             # 当前候选方案

    # Meta information
    working_directory: str                      # 工作目录
    session_id: str                             # 会话ID
