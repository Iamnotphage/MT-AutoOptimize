"""
工具执行节点 — 执行已批准的工具调用

对应 gemini-cli Scheduler + ToolExecutor:
  1. 筛选 pending_tool_calls 中 status=="pending" 的调用 (跳过 cancelled)
  2. 逐个执行, 通过 EventBus 发送状态变更事件
  3. 捕获结果或异常, 写入 completed_tool_calls
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Protocol

from core.event_bus import AgentEvent, EventBus, EventType
from core.state import AgentState, ToolCallInfo

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────
# 工具执行器协议 (供 P1+ ToolRegistry 实现)
# ──────────────────────────────────────────────────────────────────


class ToolExecutor(Protocol):
    """工具执行器接口, tool_registry 实现此协议即可"""
    # TODO: 实现工具执行器接口
    def execute(self, tool_name: str, arguments: dict) -> str: ...


# ──────────────────────────────────────────────────────────────────
# 工厂: 创建 tool_execution 节点
# ──────────────────────────────────────────────────────────────────


def create_tool_execution_node(
    event_bus: EventBus,
    executor: ToolExecutor | Callable[[str, dict], str],
) -> Callable[[AgentState], dict]:
    """
    创建 tool_execution 节点函数

    Args:
        event_bus: 事件总线
        executor: 工具执行器, 满足以下任一形式:
            - 实现 ToolExecutor 协议的对象 (有 .execute 方法)
            - 可调用对象 ``(tool_name, arguments) -> result_str``

    Returns:
        LangGraph 节点函数 ``(AgentState) -> dict``
    """
    # 统一为 callable:
    #   - 实现了 ToolExecutor 协议 → 取 .execute 方法
    #   - 否则视为可直接调用的函数
    execute_fn: Callable[[str, dict], str]
    if "execute" in dir(type(executor)):
        execute_fn = executor.execute
    else:
        execute_fn = executor

    def tool_execution_node(state: AgentState) -> dict:
        pending = state.get("pending_tool_calls", [])
        turn = state.get("turn_count", 0)

        completed: list[ToolCallInfo] = []

        for tc in pending:
            if tc["status"] == "cancelled":
                completed.append(tc)
                continue

            if tc["status"] != "pending":
                completed.append(tc)
                continue

            # ── 标记 executing ──
            executing_tc: ToolCallInfo = {**tc, "status": "executing"}
            event_bus.emit(AgentEvent(
                type=EventType.TOOL_STATE_UPDATE,
                data={
                    "call_id": tc["call_id"],
                    "tool_name": tc["tool_name"],
                    "status": "executing",
                },
                turn=turn,
            ))

            # ── 执行 ──
            try:
                result = execute_fn(tc["tool_name"], tc["arguments"])
                executing_tc = {
                    **executing_tc,
                    "status": "success",
                    "result": str(result),
                }
            except Exception as e:
                logger.error(
                    "tool %s (call_id=%s) failed: %s",
                    tc["tool_name"], tc["call_id"], e,
                )
                executing_tc = {
                    **executing_tc,
                    "status": "error",
                    "error_msg": str(e),
                }

            completed.append(executing_tc)

            # ── TOOL_CALL_COMPLETE ──
            event_bus.emit(AgentEvent(
                type=EventType.TOOL_CALL_COMPLETE,
                data={
                    "call_id": tc["call_id"],
                    "tool_name": tc["tool_name"],
                    "status": executing_tc["status"],
                    "result": executing_tc.get("result"),
                    "error_msg": executing_tc.get("error_msg"),
                },
                turn=turn,
            ))

        # ── ALL_TOOLS_COMPLETE ──
        event_bus.emit(AgentEvent(
            type=EventType.ALL_TOOLS_COMPLETE,
            data={"count": len(completed)},
            turn=turn,
        ))

        success_count = sum(1 for tc in completed if tc["status"] == "success")
        error_count = sum(1 for tc in completed if tc["status"] == "error")
        logger.info(
            "tool_execution: %d success, %d error, %d cancelled",
            success_count, error_count,
            len(completed) - success_count - error_count,
        )

        return {
            "pending_tool_calls": [],
            "completed_tool_calls": completed,
        }

    return tool_execution_node
