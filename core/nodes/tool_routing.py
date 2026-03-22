"""
工具路由节点 — 对每个 pending_tool_call 做策略判定

对应 gemini-cli PolicyEngine.check():
  1. 遍历 pending_tool_calls
  2. 按风险等级决定: 自动放行 / 需要人工确认 / 拒绝
  3. 设置 needs_human_approval 标志
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from core.event_bus import AgentEvent, EventBus, EventType
from core.state import AgentState, ToolCallInfo
from tools.registry import DEFAULT_TOOL_RISK, DEFAULT_UNKNOWN_RISK

logger = logging.getLogger(__name__)

def create_tool_routing_node(
    event_bus: EventBus,
    tool_risk_map: dict[str, str] | None = None,
) -> Callable[[AgentState], dict]:
    """
    创建 tool_routing 节点函数

    Args:
        event_bus: 事件总线
        tool_risk_map: 工具名 → 风险等级 ("low" / "medium" / "high")
                       未提供则使用 DEFAULT_TOOL_RISK

    Returns:
        LangGraph 节点函数 ``(AgentState) -> dict``
    """
    risk_map = {**DEFAULT_TOOL_RISK, **(tool_risk_map or {})}

    def tool_routing_node(state: AgentState) -> dict:
        pending = state.get("pending_tool_calls", [])

        updated_calls: list[ToolCallInfo] = []
        approval_requests: list[dict[str, Any]] = []

        for tc in pending:
            risk = risk_map.get(tc["tool_name"], DEFAULT_UNKNOWN_RISK)

            if risk == "low":
                # 自动放行, 状态保持 pending
                updated_calls.append({**tc, "status": "pending"})
            elif risk in ("medium", "high"):
                updated_calls.append({**tc, "status": "awaiting_approval"})
                approval_requests.append({
                    "call_id": tc["call_id"],
                    "tool_name": tc["tool_name"],
                    "arguments": tc["arguments"],
                    "risk_level": risk,
                })
            else:
                # 未知风险等级 → 拒绝
                updated_calls.append({**tc, "status": "cancelled"})
                logger.warning(
                    "tool_routing: unknown risk %r for %s, cancelled",
                    risk, tc["tool_name"],
                )

        needs_approval_flag = bool(approval_requests)

        # 为每个需要审批的工具发送 APPROVAL_REQUEST 事件
        for req in approval_requests:
            event_bus.emit(AgentEvent(
                type=EventType.APPROVAL_REQUEST,
                data=req,
                turn=state.get("turn_count", 0),
            ))

        logger.info(
            "tool_routing: %d calls, %d need approval",
            len(updated_calls), len(approval_requests),
        )

        return {
            "pending_tool_calls": updated_calls,
            "needs_human_approval": needs_approval_flag,
            "approval_requests": approval_requests,
        }

    return tool_routing_node



def needs_approval(state: AgentState) -> str:
    """
    tool_routing 之后的条件路由

    Usage::

        graph.add_conditional_edges("tool_routing", needs_approval, {
            "needs_approval": "human_approval",
            "approved": "tool_execution",
        })
    """
    if state.get("needs_human_approval"):
        return "needs_approval"
    return "approved"
