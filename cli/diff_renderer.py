"""Diff 渲染器 — Claude Code 风格的彩色 diff 展示

消费 core/utils/diff.DiffResult，逐行渲染：
  - 行号 + 绿底: 新增行 (+)，背景从行号前一格起延伸到终端末端
  - 行号 + 红底: 删除行 (-)，背景从行号前一格起延伸到终端末端
  - 行号 + dim:  上下文行
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from rich.cells import cell_len
from rich.console import Console
from rich.text import Text

if TYPE_CHECKING:
    from core.utils.diff import DiffResult

_MAX_LINES = 60
_BG_ADD = "on #235C2B"  # 暗绿：新增行
_BG_DEL = "on #7A2936"  # 暗红：删除行
_LEFT_MARGIN = 6
_HUNK_RE = re.compile(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def _calc_num_width(lines: list[str]) -> int:
    """预扫描 hunk header，计算涉及的最大行号的位数"""
    max_num = 1
    for line in lines:
        m = _HUNK_RE.match(line)
        if m:
            old_start = int(m.group(1))
            old_count = int(m.group(2) or 1)
            new_start = int(m.group(3))
            new_count = int(m.group(4) or 1)
            max_num = max(max_num, old_start + old_count, new_start + new_count)
    return len(str(max_num))


def _wrap_to_chunks(text: str, first_cols: int, cont_cols: int) -> list[tuple[str, int]]:
    """将文本按显示列宽折行，返回 [(片段, 末尾空白列数), ...].

    - 第一段最多占 first_cols 列（行号 + 符号之后的剩余空间）
    - 续行最多占 cont_cols 列（与第一段对齐的缩进之后的剩余空间）
    - 使用 cell_len 正确计算全角字符宽度
    """
    chunks: list[tuple[str, int]] = []
    remaining = text
    cap = first_cols

    while True:
        w = cell_len(remaining)
        if w <= cap:
            chunks.append((remaining, cap - w))
            break

        # 按列宽切出当前段
        seg: list[str] = []
        used = 0
        cut = 0
        for i, ch in enumerate(remaining):
            ch_w = cell_len(ch)
            if used + ch_w > cap:
                cut = i
                break
            seg.append(ch)
            used += ch_w
        else:
            cut = len(remaining)

        chunks.append(("".join(seg), cap - used))
        remaining = remaining[cut:]
        cap = cont_cols  # 后续段用续行宽度

    return chunks


def render_diff(console: Console, diff: "DiffResult") -> None:
    """以 Claude Code 风格渲染 diff 到终端"""

    console.print(
        f"  ⎿  [dim]Added {diff.added} lines, removed {diff.removed} lines[/dim]"
    )

    if not diff.unified_diff.strip():
        return

    lines = diff.unified_diff.splitlines()
    num_w = _calc_num_width(lines)
    gap = " " * _LEFT_MARGIN
    # 彩色区域可用列数（终端宽度去掉左侧空白边距）
    fill = max(console.width - _LEFT_MARGIN, 20)

    old_num = 0
    new_num = 0
    shown = 0

    for idx, line in enumerate(lines):
        if line.startswith("---") or line.startswith("+++"):
            continue

        if line.startswith("@@"):
            m = _HUNK_RE.match(line)
            if m:
                old_num = int(m.group(1))
                new_num = int(m.group(3))
            continue

        if shown >= _MAX_LINES:
            remaining = sum(
                1 for l in lines[idx:]
                if not l.startswith(("---", "+++", "@@"))
            )
            if remaining > 0:
                console.print(f"  {'':>{num_w}}  [dim]... {remaining} more lines[/dim]")
            break

        if line.startswith("+"):
            content = line[1:].expandtabs(4)
            prefix = f" {new_num:>{num_w}} +"
            prefix_w = cell_len(prefix)
            # 续行缩进：与首行内容对齐（行号宽 + " +" 共 prefix_w 列，空格填充）
            cont_indent = " " * (prefix_w)
            cont_cols = fill - prefix_w
            chunks = _wrap_to_chunks(content, fill - prefix_w, cont_cols)
            for i, (chunk, padding) in enumerate(chunks):
                text = Text(no_wrap=True)
                text.append(gap)
                if i == 0:
                    text.append(prefix + chunk + " " * padding, style=_BG_ADD)
                else:
                    text.append(cont_indent + chunk + " " * padding, style=_BG_ADD)
                console.print(text)
                shown += 1
            new_num += 1

        elif line.startswith("-"):
            content = line[1:].expandtabs(4)
            prefix = f" {old_num:>{num_w}} -"
            prefix_w = cell_len(prefix)
            cont_indent = " " * (prefix_w)
            cont_cols = fill - prefix_w
            chunks = _wrap_to_chunks(content, fill - prefix_w, cont_cols)
            for i, (chunk, padding) in enumerate(chunks):
                text = Text(no_wrap=True)
                text.append(gap)
                if i == 0:
                    text.append(prefix + chunk + " " * padding, style=_BG_DEL)
                else:
                    text.append(cont_indent + chunk + " " * padding, style=_BG_DEL)
                console.print(text)
                shown += 1
            old_num += 1

        else:
            content = line[1:] if line.startswith(" ") else line
            pad = " " * (_LEFT_MARGIN + 1)
            text = Text(no_wrap=True)
            text.append(f"{pad}{new_num:>{num_w}}  {content}", style="dim")
            console.print(text)
            old_num += 1
            new_num += 1
            shown += 1