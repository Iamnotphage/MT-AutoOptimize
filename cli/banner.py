"""MT-AutoOptimize — 欢迎 Banner 渲染

█ 亮色渐变主体 + ░ 灰色阴影，仿 gemini-cli longAsciiLogo 风格。
"""

from __future__ import annotations

from rich.console import Console
from rich.text import Text

GRADIENT = [
    (71, 150, 228),   # #4796E4
    (132, 122, 206),  # #847ACE
    (195, 103, 127),  # #C3677F
]

_BODY = [
    "████         ██████   ██████ █████████         █████     ███    ███ █████████  ████████ ",
    "  ████        ██████ ██████     ███           ██   ██    ███    ███    ███    ███    ███",
    "    ████      ███ █████ ███     ███          ███   ███   ███    ███    ███    ███    ███",
    "      ████    ███  ███  ███     ███   ████  ███████████  ███    ███    ███    ███    ███",
    "    ████      ███       ███     ███         ███     ███  ███    ███    ███    ███    ███",
    "  ████        ███       ███     ███         ███     ███  ███    ███    ███    ███    ███",
    "████         █████     █████    ███        █████   █████  ████████     ███     ████████ ",
]

_SHADOW_DY, _SHADOW_DX = 1, -1
_SHADOW_CHAR = "░"
_SHADOW_STYLE = "#555555"


def _has_block(r: int, c: int) -> bool:
    if 0 <= r < len(_BODY):
        line = _BODY[r]
        return 0 <= c < len(line) and line[c] == "█"
    return False


def _lerp(c1: tuple[int, int, int], c2: tuple[int, int, int], t: float) -> str:
    return "#{:02x}{:02x}{:02x}".format(
        int(c1[0] + (c2[0] - c1[0]) * t),
        int(c1[1] + (c2[1] - c1[1]) * t),
        int(c1[2] + (c2[2] - c1[2]) * t),
    )


def _gradient_at(pos: float) -> str:
    """pos ∈ [0, 1] → hex color along GRADIENT stops."""
    pos = max(0.0, min(1.0, pos))
    seg = pos * (len(GRADIENT) - 1)
    idx = min(int(seg), len(GRADIENT) - 2)
    return _lerp(GRADIENT[idx], GRADIENT[idx + 1], seg - idx)


def render_banner(console: Console) -> None:
    """渲染带渐变 + 阴影的 ASCII art Banner"""
    dy, dx = _SHADOW_DY, _SHADOW_DX
    body_w = max(len(ln) for ln in _BODY)
    left_pad = max(0, -dx)
    total_h = len(_BODY) + abs(dy)
    total_w = body_w + left_pad

    for r in range(total_h):
        text = Text()
        for c in range(total_w):
            bc = c - left_pad
            if _has_block(r, bc):
                text.append("█", style=f"bold {_gradient_at(bc / max(body_w - 1, 1))}")
            elif _has_block(r - dy, bc - dx):
                text.append(_SHADOW_CHAR, style=_SHADOW_STYLE)
            else:
                text.append(" ")
        console.print(text)
