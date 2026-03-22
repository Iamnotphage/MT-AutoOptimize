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
    pending_tool_calls: list[ToolCallInfo]
    completed_tool_calls: list[ToolCallInfo]

    # 控制流
    turn_count: int
    max_turns: int
    should_continue: bool
    needs_human_approval: bool
    approval_requests: list[dict]

    # MT-3000相关
    optimization_mode: str | None
    source_file: str | None
    compile_results: list[dict]
    benchmark_results: list[dict]
    current_candidates: dict | None

    # Meta information
    working_directory: str
    session_id: str
