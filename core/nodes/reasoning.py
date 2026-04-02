"""
推理节点 — ReAct 循环中的 LLM 推理步骤

对应 gemini-cli 的 Turn.run():
  1. 组装系统提示词 (system prompt + 工具描述 + MT-3000 上下文)
  2. 调用 LLM (流式), 通过 EventBus 发送 CONTENT/THOUGHT 事件
  3. 解析 LLM 返回的 tool_calls → 写入 state.pending_tool_calls
  4. turn_count += 1
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, SystemMessage

from core.event_bus import AgentEvent, EventBus, EventType
from core.state import AgentState, ToolCallInfo
from prompts.system_prompt import build_system_prompt

logger = logging.getLogger(__name__)

# 类型引用，避免硬依赖
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from core.context import ContextManager


def create_reasoning_node(
    llm: BaseChatModel,
    event_bus: EventBus,
    tool_schemas: list[dict[str, Any]] | None = None,
    context_manager: ContextManager | None = None,
) -> Callable[[AgentState], dict]:
    """
    创建 reasoning 节点函数

    Args:
        llm: LangChain ChatModel (如 ChatOpenAI)
        event_bus: 事件总线, 用于向 CLI 层推送流式事件
        tool_schemas: OpenAI function-calling 格式的工具定义列表
        context_manager: Context & Memory 管理器 (可选)

    Returns:
        LangGraph 节点函数 ``(AgentState) -> dict``
    """
    bound_llm = llm.bind_tools(tool_schemas) if tool_schemas else llm

    def reasoning_node(state: AgentState) -> dict:
        turn = state.get("turn_count", 0)

        # 1) system prompt — 每轮动态生成, 含全局上下文 (Tier 1)
        global_context = ""
        if context_manager is not None:
            global_context = context_manager.build_system_context()

        system_msg = SystemMessage(
            content=build_system_prompt(state, tool_schemas, global_context=global_context)
        )

        # 2) 构建 messages: system + session_context (Tier 2, 仅首轮) + 历史
        messages = [system_msg]

        if turn == 0 and context_manager is not None:
            session_ctx = context_manager.build_session_context()
            if session_ctx:
                from langchain_core.messages import HumanMessage
                messages.append(HumanMessage(content=session_ctx))

        messages.extend(list(state.get("message", [])))

        # 3) 流式调用 LLM, 逐 chunk 发送 EventBus 事件
        collected = _stream_with_events(bound_llm, messages, event_bus, turn)

        if collected is None:
            logger.warning("LLM 返回空响应, turn=%d", turn)
            return {
                "message": [AIMessage(content="[LLM 无响应]")],
                "turn_count": turn + 1,
                "pending_tool_calls": [],
            }

        # 4) 收集 token usage → SessionStats
        if context_manager is not None:
            _record_token_usage(collected, context_manager)

        # 5) 构造 AIMessage 写入 state.message 历史
        ai_message = AIMessage(
            content=collected.content or "",
            tool_calls=collected.tool_calls or [],
        )

        # 6) tool_calls → pending_tool_calls
        pending = _extract_tool_calls(collected, event_bus, turn)

        # 7) TURN_START 事件
        event_bus.emit(AgentEvent(
            type=EventType.TURN_START,
            data={
                "turn": turn + 1,
                "has_tool_calls": bool(pending),
                "tool_count": len(pending),
            },
            turn=turn + 1,
        ))

        logger.info(
            "reasoning turn=%d content_len=%d tool_calls=%d",
            turn + 1, len(ai_message.content), len(pending),
        )

        return {
            "message": [ai_message],
            "turn_count": turn + 1,
            "pending_tool_calls": pending,
        }

    return reasoning_node



def should_use_tools(state: AgentState) -> str:
    """
    reasoning 之后的条件路由

    Usage::

        graph.add_conditional_edges("reasoning", should_use_tools, {
            "use_tools": "tool_routing",
            "final_answer": END,
        })
    """
    if state.get("pending_tool_calls"):
        return "use_tools"
    return "final_answer"



def _stream_with_events(
    llm: BaseChatModel,
    messages: list,
    event_bus: EventBus,
    turn: int,
) -> AIMessageChunk | None:
    """流式调用 LLM, 每个 chunk 通过 EventBus 推送事件"""

    collected: AIMessageChunk | None = None

    try:
        for chunk in llm.stream(messages):
            collected = chunk if collected is None else collected + chunk

            # 文本内容 → CONTENT
            if chunk.content:
                event_bus.emit(AgentEvent(
                    type=EventType.CONTENT,
                    data={"text": chunk.content},
                    turn=turn,
                ))

            # 思考过程 → THOUGHT (DeepSeek-R1 等模型的 reasoning_content)
            reasoning = (chunk.additional_kwargs or {}).get("reasoning_content")
            if reasoning:
                event_bus.emit(AgentEvent(
                    type=EventType.THOUGHT,
                    data={"text": reasoning},
                    turn=turn,
                ))

    except Exception as e:
        logger.error("LLM streaming error: %s", e)
        event_bus.emit(AgentEvent(
            type=EventType.ERROR,
            data={"error": str(e), "source": "reasoning_node"},
            turn=turn,
        ))
        return None

    return collected



def _extract_tool_calls(
    response: AIMessageChunk,
    event_bus: EventBus,
    turn: int,
) -> list[ToolCallInfo]:
    """从 LLM 响应中提取 tool_calls → ToolCallInfo, 并发送 TOOL_CALL_REQUEST"""

    pending: list[ToolCallInfo] = []

    for tc in response.tool_calls or []:
        info = ToolCallInfo(
            call_id=tc["id"],
            tool_name=tc["name"],
            arguments=tc["args"],
            status="pending",
            result=None,
            error_msg=None,
        )
        pending.append(info)

        event_bus.emit(AgentEvent(
            type=EventType.TOOL_CALL_REQUEST,
            data={
                "call_id": tc["id"],
                "tool_name": tc["name"],
                "arguments": tc["args"],
            },
            turn=turn,
        ))

    return pending


def _record_token_usage(
    response: AIMessageChunk,
    context_manager: ContextManager,
) -> None:
    """从 LLM 响应中提取 token usage 并记录到 SessionStats。

    LangChain 的 usage_metadata (如果模型提供) 格式:
        {"input_tokens": int, "output_tokens": int, "total_tokens": int}
    也支持 response_metadata.usage 格式 (OpenAI 兼容):
        {"prompt_tokens": int, "completion_tokens": int, "total_tokens": int}
    """
    stats = context_manager.session_stats

    # 优先从 usage_metadata 取 (LangChain 标准)
    usage = getattr(response, "usage_metadata", None)
    if usage and isinstance(usage, dict):
        stats.record_llm_usage(
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
        )
        return

    # 回退: response_metadata.usage (OpenAI 兼容)
    resp_meta = getattr(response, "response_metadata", None) or {}
    usage = resp_meta.get("usage") or resp_meta.get("token_usage") or {}
    if usage:
        stats.record_llm_usage(
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
        )

