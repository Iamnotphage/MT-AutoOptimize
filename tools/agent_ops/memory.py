"""save_memory — 让 Agent 能够持久化记忆

Agent 通过 function calling 调用此工具，将重要事实写入
~/.mtagent/CONTEXT.md 的 ## Agent Memories 区域。

遵循三层架构: Tool 不直接 import Core，通过 callback 访问 ContextManager。
"""

from __future__ import annotations

from typing import Any, Callable

from pydantic import BaseModel, Field

from tools.base import BaseTool, ToolResult, ToolRiskLevel


class SaveMemoryArgs(BaseModel):
    fact: str = Field(
        description="A clear, self-contained statement in natural language.",
    )


class SaveMemoryTool(BaseTool):
    """持久化 Agent 记忆"""

    name = "save_memory"
    description = (
        "Save an important fact to persistent memory so it can be reused across sessions. "
        "Use this when the user explicitly asks you to remember something, or when you "
        "discover stable project knowledge that will likely be useful later."
    )
    risk_level = ToolRiskLevel.LOW
    args_schema = SaveMemoryArgs

    def __init__(self, save_fn: Callable[[str], None]) -> None:
        """
        Args:
            save_fn: 写入记忆的回调函数，签名为 (fact: str) -> None。
                     由 AgentRuntime 传入 context_manager.save_memory。
        """
        self._save_fn = save_fn

    async def execute(self, **kwargs: Any) -> ToolResult:
        fact: str = kwargs["fact"]
        if not fact.strip():
            return ToolResult(output="", error="记忆内容不能为空")

        try:
            self._save_fn(fact)
            return ToolResult(
                output=f"已保存记忆: {fact}",
                display=f"💾 已记住: {fact}",
            )
        except Exception as e:
            return ToolResult(output="", error=f"保存记忆失败: {e}")
