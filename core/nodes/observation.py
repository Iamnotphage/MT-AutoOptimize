"""
观察节点 — 将工具执行结果整合回消息历史

对应 gemini-cli handleCompletedTools:
  1. 将 completed_tool_calls 转为 ToolMessage 追加到 messages
  2. 清空 pending_tool_calls 和 completed_tool_calls
  3. 检查是否超过 max_turns
  4. 设置 should_continue
"""

from __future__ import annotations

import logging
from typing import Callable

from langchain_core.messages import ToolMessage

from core.event_bus import AgentEvent, EventBus, EventType
from core.state import AgentState, ToolCallInfo

logger = logging.getLogger(__name__)

# 默认最大轮次
_DEFAULT_MAX_TURNS = 25


def create_observation_node(
    event_bus: EventBus,
) -> Callable[[AgentState], dict]:
    """
    创建 observation 节点函数

    Args:
        event_bus: 事件总线

    Returns:
        LangGraph 节点函数 ``(AgentState) -> dict``
    """

    def observation_node(state: AgentState) -> dict:
        completed = state.get("completed_tool_calls", [])
        turn = state.get("turn_count", 0)
        max_turns = state.get("max_turns", _DEFAULT_MAX_TURNS)

        # 1) completed_tool_calls → ToolMessage 列表
        tool_messages = _build_tool_messages(completed)

        # 2) 判断是否继续循环
        should_continue = turn < max_turns

        if not should_continue:
            logger.warning("max_turns reached (%d/%d), stopping", turn, max_turns)

        # 3) TURN_END 事件
        event_bus.emit(AgentEvent(
            type=EventType.TURN_END,
            data={
                "turn": turn,
                "tool_count": len(completed),
                "should_continue": should_continue,
            },
            turn=turn,
        ))

        logger.info(
            "observation: %d tool results → messages, should_continue=%s",
            len(tool_messages), should_continue,
        )

        return {
            "message": tool_messages,
            "pending_tool_calls": [],
            "completed_tool_calls": [],
            "should_continue": should_continue,
        }

    return observation_node


# ──────────────────────────────────────────────────────────────────
# 条件路由: observation 之后判断是否继续 ReAct 循环
# ──────────────────────────────────────────────────────────────────


def should_continue_loop(state: AgentState) -> str:
    """
    observation 之后的条件路由

    Usage::

        graph.add_conditional_edges("observation", should_continue_loop, {
            "continue": "reasoning",
            "final_answer": END,
        })
    """
    if state.get("should_continue", False):
        return "continue"
    return "final_answer"


# ──────────────────────────────────────────────────────────────────
# 内部辅助
# ──────────────────────────────────────────────────────────────────


def _build_tool_messages(completed: list[ToolCallInfo]) -> list[ToolMessage]:
    """将 completed_tool_calls 转为 LangChain ToolMessage 列表"""

    messages: list[ToolMessage] = []

    for tc in completed:
        if tc["status"] == "success":
            content = tc.get("result") or ""
        elif tc["status"] == "error":
            content = f"[工具执行失败] {tc.get('error_msg', 'unknown error')}"
        elif tc["status"] == "cancelled":
            content = "[工具调用已被用户拒绝]"
        else:
            content = f"[未知状态: {tc['status']}]"

        messages.append(ToolMessage(
            content=content,
            tool_call_id=tc["call_id"],
        ))

    return messages
