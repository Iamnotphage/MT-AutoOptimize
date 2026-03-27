"""
Agent 运行时 — 组装 LLM + EventBus + Registry + Graph

将 Agent 的构建逻辑与 CLI 层解耦，CLI / 测试 / API 均可复用。
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Any

from config import load_llm_config
from core.event_bus import EventBus
from core.graph import build_agent_graph
from core.llm import create_chat_model
from tools import ToolRegistry, create_default_tools


@dataclass
class AgentRuntime:
    """封装完整的 Agent 运行时组件，供 CLI / 测试 / API 复用"""

    graph: Any
    event_bus: EventBus
    registry: ToolRegistry
    workspace: str


def _make_sync_executor(registry: ToolRegistry, event_bus: EventBus):
    """将 async ToolRegistry.execute 桥接为 sync (tool_name, args) -> str

    当工具返回的 metadata 中包含 diff 等富数据时，
    通过 EventBus TOOL_LIVE_OUTPUT 推送给 CLI 层渲染。
    """
    from core.event_bus import AgentEvent, EventType

    def executor(tool_name: str, arguments: dict) -> str:
        result = asyncio.run(registry.execute(tool_name, arguments))
        if result.error:
            raise RuntimeError(result.error)

        diff = result.metadata.get("diff")
        if diff is not None:
            event_bus.emit(AgentEvent(
                type=EventType.TOOL_LIVE_OUTPUT,
                data={"tool_name": tool_name, "kind": "diff", "diff": diff},
            ))

        return result.output
    return executor


def create_agent_runtime(
    *,
    workspace: str | None = None,
) -> AgentRuntime:
    """一行组装完整 Agent

    Usage::

        runtime = create_agent_runtime(workspace="/path/to/project")
        result = runtime.graph.invoke(state, config)
    """
    from langgraph.checkpoint.memory import MemorySaver

    llm_cfg = load_llm_config()

    llm = create_chat_model(llm_cfg)

    event_bus = EventBus()

    registry = ToolRegistry()
    ws = workspace or os.getcwd()
    registry.register(*create_default_tools(workspace=ws))

    graph = build_agent_graph(
        llm=llm,
        event_bus=event_bus,
        tool_schemas=registry.schemas,
        executor=_make_sync_executor(registry, event_bus),
        checkpointer=MemorySaver(),
    )

    return AgentRuntime(
        graph=graph,
        event_bus=event_bus,
        registry=registry,
        workspace=ws,
    )
