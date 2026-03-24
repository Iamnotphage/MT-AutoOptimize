"""
Agent 运行时 — 组装 LLM + EventBus + Registry + Graph

将 Agent 的构建逻辑与 CLI 层解耦，CLI / 测试 / API 均可复用。
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Any

from core.config import load_app_config
from core.event_bus import EventBus
from core.graph import build_agent_graph
from core.llm import create_chat_model
from tools import ReadFileTool, ToolRegistry


@dataclass
class AgentRuntime:
    """封装完整的 Agent 运行时组件，供 CLI / 测试 / API 复用"""

    graph: Any
    event_bus: EventBus
    registry: ToolRegistry
    workspace: str


def _make_sync_executor(registry: ToolRegistry):
    """将 async ToolRegistry.execute 桥接为 sync (tool_name, args) -> str"""
    def executor(tool_name: str, arguments: dict) -> str:
        result = asyncio.run(registry.execute(tool_name, arguments))
        if result.error:
            raise RuntimeError(result.error)
        return result.output
    return executor


def create_agent_runtime(
    *,
    workspace: str | None = None,
    config_path: str = "config.json",
) -> AgentRuntime:
    """一行组装完整 Agent

    Usage::

        runtime = create_agent_runtime(workspace="/path/to/project")
        result = runtime.graph.invoke(state, config)
    """
    from langgraph.checkpoint.memory import MemorySaver

    cfg = load_app_config(config_path)

    llm = create_chat_model(cfg["code_llm"], env_prefix="CODE_LLM")

    event_bus = EventBus()

    registry = ToolRegistry()
    ws = workspace or os.getcwd()
    registry.register(ReadFileTool(workspace=ws))

    graph = build_agent_graph(
        llm=llm,
        event_bus=event_bus,
        tool_schemas=registry.schemas,
        executor=_make_sync_executor(registry),
        checkpointer=MemorySaver(),
    )

    return AgentRuntime(
        graph=graph,
        event_bus=event_bus,
        registry=registry,
        workspace=ws,
    )
