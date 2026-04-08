"""
Agent 运行时 — 组装 LLM + EventBus + Registry + Graph

将 Agent 的构建逻辑与 CLI 层解耦，CLI / 测试 / API 均可复用。
"""

from __future__ import annotations

import asyncio
import os
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any

from config import load_llm_config
from config.settings import CONTEXT as CONTEXT_CONFIG
from core.compressor import ContextCompressor
from core.context import ContextManager
from core.session import SessionRecorder
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
    context_manager: ContextManager
    session: SessionRecorder
    checkpoint_manager: AbstractContextManager[Any] | None = None


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
    from langgraph.checkpoint.sqlite import SqliteSaver

    llm_cfg = load_llm_config()

    llm = create_chat_model(llm_cfg)

    event_bus = EventBus()

    # Context & Memory — 必须在工具注册之前初始化，因为 save_memory tool 需要回调
    ws = workspace or os.getcwd()
    ctx_manager = ContextManager(working_directory=ws, config=CONTEXT_CONFIG)
    ctx_manager.load()

    session = SessionRecorder(working_directory=ws, config=CONTEXT_CONFIG)
    session.stats.model = llm_cfg["model"]

    registry = ToolRegistry()
    registry.register(*create_default_tools(
        workspace=ws,
        save_memory_fn=ctx_manager.save_memory,
    ))

    # 上下文压缩器
    compressor = ContextCompressor(
        llm=llm,
        token_limit=CONTEXT_CONFIG.get("token_limit", 65536),
        threshold=CONTEXT_CONFIG.get("compression_threshold", 0.50),
        preserve_ratio=CONTEXT_CONFIG.get("compression_preserve_ratio", 0.30),
    )

    checkpoint_path = session.get_checkpoint_path()
    checkpoint_manager = SqliteSaver.from_conn_string(str(checkpoint_path))
    checkpointer = checkpoint_manager.__enter__()

    graph = build_agent_graph(
        llm=llm,
        event_bus=event_bus,
        tool_schemas=registry.schemas,
        executor=_make_sync_executor(registry, event_bus),
        checkpointer=checkpointer,
        context_manager=ctx_manager,
        session_stats=session.stats,
        compressor=compressor,
    )

    return AgentRuntime(
        graph=graph,
        event_bus=event_bus,
        registry=registry,
        workspace=ws,
        context_manager=ctx_manager,
        session=session,
        checkpoint_manager=checkpoint_manager,
    )
