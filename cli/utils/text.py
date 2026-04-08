"""CLI 工具函数和共享常量"""

from __future__ import annotations

import unicodedata
from typing import Any


# ── 样式常量 ─────────────────────────────────────────────────────

PROMPT_STYLE = "#847ACE"
PROMPT_SYMBOL = "❯"
RISK_STYLE = {"low": "green", "medium": "yellow", "high": "red bold"}
TOOL_DISPLAY = {"write_file": "Write", "read_file": "Read", "ls": "Ls"}
BG_USER = "on #252530"

COMMANDS = [
    ("/clear",   "清屏"),
    ("/new",     "开启新会话 (清空对话历史)"),
    ("/resume",  "恢复历史会话"),
    ("/context", "查看/刷新上下文 (show|reload)"),
    ("/memory",  "管理记忆 (list|add|remove)"),
    ("/help",    "显示帮助信息"),
    ("/version", "显示版本号"),
    ("/exit",    "退出"),
]


# ── 文本工具 ─────────────────────────────────────────────────────

def truncate(val: Any, maxlen: int = 60) -> str:
    s = str(val)
    return s if len(s) <= maxlen else s[:maxlen] + "…"


def display_width(s: str) -> int:
    """计算字符串的终端显示列数（全角字符占 2 列）"""
    width = 0
    for ch in s:
        eaw = unicodedata.east_asian_width(ch)
        width += 2 if eaw in ("W", "F") else 1
    return width


def ljust_cols(s: str, total_cols: int, fillchar: str = " ") -> str:
    """按显示列数右填充，而非字符数"""
    pad = max(0, total_cols - display_width(s))
    return s + fillchar * pad
