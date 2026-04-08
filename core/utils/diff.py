"""Diff 工具函数 — 生成 unified diff 和变更统计

工具层调用 generate_diff() 获取结构化结果，
CLI 层消费 DiffResult 做彩色渲染。
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass


@dataclass(frozen=True)
class DiffResult:
    """结构化 diff 结果，供工具层和 CLI 层共同消费"""

    file_path: str
    unified_diff: str
    added: int
    removed: int
    is_new: bool

    @property
    def stat(self) -> str:
        if self.is_new:
            return f"+{self.added} (new file)"
        return f"+{self.added} -{self.removed}"


def _ensure_newline(s: str) -> str:
    """确保字符串以换行符结尾。"""
    return s if not s or s.endswith("\n") else s + "\n"


def generate_diff(
    file_path: str,
    old_content: str,
    new_content: str,
    *,
    is_new: bool = False,
) -> DiffResult:
    """一次计算生成 unified diff + 变更统计"""

    old_lines = _ensure_newline(old_content).splitlines(keepends=True)
    new_lines = _ensure_newline(new_content).splitlines(keepends=True)

    diff_lines = list(difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=f"a/{file_path}",
        tofile=f"b/{file_path}",
    ))
    unified = "".join(diff_lines)

    added = sum(1 for ln in diff_lines if ln.startswith("+") and not ln.startswith("+++"))
    removed = sum(1 for ln in diff_lines if ln.startswith("-") and not ln.startswith("---"))

    return DiffResult(
        file_path=file_path,
        unified_diff=unified,
        added=added,
        removed=removed,
        is_new=is_new,
    )