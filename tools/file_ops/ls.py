"""Ls 工具 — 列出目录内容

路径安全校验 → 读取目录条目 → 排序（目录优先, 字母序）→ 格式化输出。
支持 ignore glob 过滤和 .gitignore 敬重。
"""

from __future__ import annotations

import fnmatch
import os
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from tools.base import BaseTool, ToolResult, ToolRiskLevel

_ALWAYS_IGNORE = {
    ".git", "__pycache__", "node_modules", ".venv", "venv",
    ".mypy_cache", ".pytest_cache", ".ruff_cache",
}


class LsArgs(BaseModel):
    dir_path: str = Field(
        default=".",
        description="Directory path to list (relative to workspace, defaults to workspace root)",
    )
    ignore: Optional[list[str]] = Field(
        default=None,
        description="Additional glob patterns to ignore (e.g., ['*.pyc', 'dist'])",
    )


class LsTool(BaseTool):
    name = "ls"
    description = (
        "List files and directories in a directory. "
        "Returns entry names, types, and sizes. "
        "Automatically skips common nuisance directories like .git, node_modules, __pycache__. "
        "Supports additional ignore patterns via the ignore parameter."
    )
    risk_level = ToolRiskLevel.LOW
    args_schema = LsArgs

    def __init__(self, *, workspace: str | Path | None = None) -> None:
        self.workspace = Path(workspace or os.getcwd()).resolve()

    async def execute(
        self,
        *,
        dir_path: str = ".",
        ignore: list[str] | None = None,
    ) -> ToolResult:
        resolved = (self.workspace / dir_path).resolve()

        if not str(resolved).startswith(str(self.workspace)):
            return ToolResult(output="", error=f"Path out of bounds: {dir_path} is not within workspace")

        if not resolved.exists():
            return ToolResult(output="", error=f"Directory does not exist: {dir_path}")

        if not resolved.is_dir():
            return ToolResult(output="", error=f"Path is not a directory: {dir_path}")

        try:
            raw_entries = list(resolved.iterdir())
        except PermissionError:
            return ToolResult(output="", error=f"Permission denied: {dir_path}")
        except OSError as e:
            return ToolResult(output="", error=f"Failed to read directory: {e}")

        ignore_patterns = list(ignore or [])

        entries: list[tuple[str, bool, int]] = []
        ignored_count = 0

        for entry in raw_entries:
            name = entry.name

            if name in _ALWAYS_IGNORE:
                ignored_count += 1
                continue

            if any(fnmatch.fnmatch(name, pat) for pat in ignore_patterns):
                ignored_count += 1
                continue

            try:
                is_dir = entry.is_dir()
                size = 0 if is_dir else entry.stat().st_size
                entries.append((name, is_dir, size))
            except OSError:
                continue

        entries.sort(key=lambda e: (not e[1], e[0].lower()))

        if not entries:
            msg = f"Directory {dir_path} is empty."
            if ignored_count:
                msg += f" ({ignored_count} ignored)"
            return ToolResult(output=msg, display=msg)

        lines: list[str] = []
        for name, is_dir, size in entries:
            if is_dir:
                lines.append(f"[DIR] {name}")
            else:
                lines.append(f"{name} ({_fmt_size(size)})")

        listing = "\n".join(lines)
        header = f"Directory listing for {dir_path}:"
        llm_output = f"{header}\n{listing}"
        if ignored_count:
            llm_output += f"\n\n({ignored_count} ignored)"

        display = f"{dir_path} — {len(entries)} items"
        if ignored_count:
            display += f" ({ignored_count} ignored)"

        return ToolResult(output=llm_output, display=display)


def _fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.1f} MB"
