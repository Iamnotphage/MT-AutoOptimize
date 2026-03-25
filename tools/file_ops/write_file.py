"""WriteFile 工具 — 写入/创建文件

路径安全校验 → 读取原始内容 → 写入新内容 → 返回结构化 DiffResult。
Diff 渲染由 CLI 层负责（cli/diff_renderer.py）。
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, Field

from core.utils.diff import DiffResult, generate_diff
from tools.base import BaseTool, ToolResult, ToolRiskLevel


class WriteFileArgs(BaseModel):
    file_path: str = Field(description="要写入的文件路径（相对于工作区）")
    content: str = Field(description="要写入的完整文件内容")


class WriteFileTool(BaseTool):
    name = "write_file"
    description = (
        "写入内容到指定文件。文件不存在时自动创建（含父目录）。"
        "文件已存在时覆盖，并返回变更 diff 供确认。"
        "必须提供完整文件内容，不要省略任何部分。"
    )
    risk_level = ToolRiskLevel.MEDIUM
    args_schema = WriteFileArgs

    def __init__(self, *, workspace: str | Path | None = None) -> None:
        self.workspace = Path(workspace or os.getcwd()).resolve()

    async def execute(self, *, file_path: str, content: str) -> ToolResult:
        resolved = (self.workspace / file_path).resolve()

        if not str(resolved).startswith(str(self.workspace)):
            return ToolResult(output="", error=f"路径越界: {file_path} 不在工作区内")

        if resolved.exists() and resolved.is_dir():
            return ToolResult(output="", error=f"目标是目录而非文件: {file_path}")

        is_new = not resolved.exists()

        original = ""
        if not is_new:
            try:
                original = resolved.read_text(encoding="utf-8", errors="replace")
            except OSError as e:
                return ToolResult(output="", error=f"读取原文件失败: {e}")

        try:
            resolved.parent.mkdir(parents=True, exist_ok=True)
            resolved.write_text(content, encoding="utf-8")
        except PermissionError:
            return ToolResult(output="", error=f"权限不足: {file_path}")
        except OSError as e:
            return ToolResult(output="", error=f"写入失败: {e}")

        diff = generate_diff(file_path, original, content, is_new=is_new)

        action = "Created" if is_new else "Overwrote"
        total_lines = len(content.splitlines())
        llm_output = f"{action} file: {file_path} ({total_lines} lines, {diff.stat})"
        if diff.unified_diff:
            preview = diff.unified_diff[:2000]
            if len(diff.unified_diff) > 2000:
                preview += "\n... (diff truncated)"
            llm_output += f"\n\nDiff:\n{preview}"

        return ToolResult(
            output=llm_output,
            display=f"{file_path} ({diff.stat})",
            metadata={
                "is_new": is_new,
                "lines": total_lines,
                "diff": diff,
            },
        )
