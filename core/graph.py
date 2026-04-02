from __future__ import annotations

from langchain_core.language_models import BaseChatModel
from langgraph.graph import START, END, StateGraph
from core.event_bus import EventBus
from core.state import AgentState
from core.nodes.reasoning import create_reasoning_node, should_use_tools
from core.nodes.tool_routing import create_tool_routing_node, needs_approval
from core.nodes.human_approval import create_human_approval_node
from core.nodes.tool_execution import create_tool_execution_node
from core.nodes.observation import create_observation_node, should_continue_loop

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from core.context import ContextManager

def build_agent_graph(
    llm: BaseChatModel,
    event_bus: EventBus,
    tool_schemas: list[dict] | None = None,
    executor = None,
    checkpointer = None,
    context_manager: ContextManager | None = None,
) -> StateGraph:
    """
    工厂模式创建结点，构建 ReAct 循环的 LangGraph 状态图

    Args:
        llm: LangChain ChatModel (如 ChatOpenAI)
        event_bus: 事件总线, 用于向 CLI 层推送流式事件
        tool_schemas: OpenAI function-calling 格式的工具定义列表
        executor: 工具执行器 (tool_name, arguments) -> result_str
        checkpointer: LangGraph 检查点存储, 用于 interrupt/resume 和多轮对话

    Returns:
        编译后的 StateGraph runnable
    """

    # P0 fallback: executor 未提供时使用占位函数
    if executor is None:
        def executor(tool_name: str, arguments: dict) -> str:
            return f"[未注册工具: {tool_name}]"

    # 工厂模式创建结点函数
    reasoning_node = create_reasoning_node(llm, event_bus, tool_schemas, context_manager)
    tool_routing_node = create_tool_routing_node(event_bus)
    human_approval_node = create_human_approval_node(event_bus)
    tool_execution_node = create_tool_execution_node(event_bus, executor)
    observation_node = create_observation_node(event_bus)


    graph = StateGraph(AgentState)


    # add nodes
    graph.add_node("reasoning", reasoning_node)
    graph.add_node("tool_routing", tool_routing_node)
    graph.add_node("human_approval", human_approval_node)
    graph.add_node("tool_execution", tool_execution_node)
    graph.add_node("observation", observation_node)

    # 入口
    graph.add_edge(START, "reasoning")

    # reasoning -> 条件路由
    graph.add_conditional_edges(
        "reasoning",
        should_use_tools,
        {
            "use_tools": "tool_routing",
            "final_answer": END,
        }
    )

    # tool_routing -> 条件路由
    graph.add_conditional_edges(
        "tool_routing",
        needs_approval,
        {
            "needs_approval": "human_approval",
            "approved": "tool_execution",
        }
    )

    # human_approval -> tool_execution
    graph.add_edge("human_approval", "tool_execution")

    # tool_execution -> observation
    graph.add_edge("tool_execution", "observation")

    # observation -> reasoning (ReAct 循环闭合点)
    graph.add_conditional_edges(
        "observation",
        should_continue_loop,
        {
            "continue": "reasoning",
            "final_answer": END,
        }
    )

    return graph.compile(checkpointer=checkpointer)