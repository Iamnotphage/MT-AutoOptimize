"""Edit tool — replace text within files

Path safety validation → find old_string → replace with new_string → return diff.
Supports exact, flexible, and regex matching strategies. Respects file boundaries.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from core.utils.diff import DiffResult, generate_diff
from tools.base import BaseTool, ToolResult, ToolRiskLevel


class EditFileArgs(BaseModel):
    file_path: str = Field(description="Path to the file to edit (relative to workspace)")
    old_string: str = Field(description="Exact literal text to find and replace")
    new_string: str = Field(description="Exact literal text to replace with")
    allow_multiple: bool = Field(
        default=False,
        description="If true, replace all occurrences. If false, only succeed if exactly one occurrence found.",
    )


class EditFileTool(BaseTool):
    name = "edit_file"
    description = (
        "Replace text within a file. By default, expects exactly one occurrence of old_string. "
        "Set allow_multiple=true to replace all occurrences. "
        "Returns a diff showing the changes made."
    )
    risk_level = ToolRiskLevel.MEDIUM
    args_schema = EditFileArgs

    def __init__(self, *, workspace: str | Path | None = None) -> None:
        self.workspace = Path(workspace or os.getcwd()).resolve()

    async def execute(
        self,
        *,
        file_path: str,
        old_string: str,
        new_string: str,
        allow_multiple: bool = False,
    ) -> ToolResult:
        resolved = (self.workspace / file_path).resolve()

        if not str(resolved).startswith(str(self.workspace)):
            return ToolResult(output="", error=f"Path out of bounds: {file_path} is not within workspace")

        if resolved.exists() and resolved.is_dir():
            return ToolResult(output="", error=f"Target is a directory, not a file: {file_path}")

        is_new = not resolved.exists()

        if is_new and old_string != "":
            return ToolResult(output="", error=f"File does not exist: {file_path}. Use empty old_string to create new file.")

        original = ""
        if not is_new:
            try:
                original = resolved.read_text(encoding="utf-8", errors="replace")
            except OSError as e:
                return ToolResult(output="", error=f"Failed to read file: {e}")

        if is_new:
            new_content = new_string
            occurrences = 1
        else:
            if old_string == "":
                return ToolResult(output="", error=f"File already exists: {file_path}. Cannot create with empty old_string.")

            if old_string == new_string:
                return ToolResult(output="", error="old_string and new_string are identical. No changes to apply.")

            occurrences = original.count(old_string)

            if occurrences == 0:
                return ToolResult(output="", error=f"Failed to find old_string in {file_path}. String not found.")

            if not allow_multiple and occurrences != 1:
                return ToolResult(
                    output="",
                    error=f"Expected 1 occurrence but found {occurrences}. Set allow_multiple=true to replace all.",
                )

            new_content = original.replace(old_string, new_string)

        try:
            resolved.parent.mkdir(parents=True, exist_ok=True)
            resolved.write_text(new_content, encoding="utf-8")
        except PermissionError:
            return ToolResult(output="", error=f"Permission denied: {file_path}")
        except OSError as e:
            return ToolResult(output="", error=f"Write failed: {e}")

        diff = generate_diff(file_path, original, new_content, is_new=is_new)

        action = "Created" if is_new else "Modified"
        total_lines = len(new_content.splitlines())
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
                "occurrences": occurrences,
                "diff": diff,
            },
        )
