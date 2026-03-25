"""Diff 渲染器 — Claude Code 风格的彩色 diff 展示

消费 core/utils/diff.DiffResult，逐行渲染：
  - 行号 + 绿底: 新增行 (+)，背景从行号前一格起延伸到终端末端
  - 行号 + 红底: 删除行 (-)，背景从行号前一格起延伸到终端末端
  - 行号 + dim:  上下文行
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from rich.console import Console
from rich.text import Text

if TYPE_CHECKING:
    from core.utils.diff import DiffResult

_MAX_LINES = 60
_BG_ADD = "on #143d1f"
_BG_DEL = "on #3d1417"
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


def render_diff(console: Console, diff: DiffResult) -> None:
    """以 Claude Code 风格渲染 diff 到终端"""

    console.print(
        f"  ⎿  [dim]Added {diff.added} lines, removed {diff.removed} lines[/dim]"
    )

    if not diff.unified_diff.strip():
        return

    lines = diff.unified_diff.splitlines()
    num_w = _calc_num_width(lines)
    gap = " " * _LEFT_MARGIN
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

        text = Text(no_wrap=True)

        if line.startswith("+"):
            content = line[1:]
            text.append(gap)
            colored = f" {new_num:>{num_w}} +{content}"
            text.append(colored.ljust(fill), style=_BG_ADD)
            new_num += 1
        elif line.startswith("-"):
            content = line[1:]
            text.append(gap)
            colored = f" {old_num:>{num_w}} -{content}"
            text.append(colored.ljust(fill), style=_BG_DEL)
            old_num += 1
        else:
            content = line[1:] if line.startswith(" ") else line
            pad = " " * (_LEFT_MARGIN + 1)
            text.append(f"{pad}{new_num:>{num_w}}  {content}", style="dim")
            old_num += 1
            new_num += 1

        console.print(text)
        shown += 1
