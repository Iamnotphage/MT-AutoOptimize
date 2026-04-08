"""prompt_toolkit 输入组件 — 带命令下拉补全的输入框"""

from __future__ import annotations

from typing import Callable

from prompt_toolkit import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.cursor_shapes import CursorShape
from prompt_toolkit.document import Document
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings, merge_key_bindings
from prompt_toolkit.key_binding.bindings.emacs import load_emacs_bindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import ConditionalContainer, HSplit, VSplit, Window
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl

from cli.utils.text import COMMANDS, PROMPT_SYMBOL


def read_input(
    history: InMemoryHistory,
    status_func: Callable[[], str] | None = None,
) -> str:
    """构建 prompt_toolkit Application，实现命令下拉菜单。"""
    dropdown: dict = {"items": [], "selected": 0}

    def _refresh(text: str) -> None:
        if text.startswith("/"):
            prefix = text[1:].lower()
            dropdown["items"] = [
                (cmd, desc) for cmd, desc in COMMANDS
                if cmd[1:].startswith(prefix)
            ]
        else:
            dropdown["items"] = []
        dropdown["selected"] = 0

    buf = Buffer(
        name="main",
        history=history,
        on_text_changed=lambda b: _refresh(b.text),
    )

    _pfx: list = [("fg:#847ACE", f"{PROMPT_SYMBOL} ")]
    _pfx_wrap: list = [("", "  ")]

    def _render_dropdown() -> FormattedText:
        items = dropdown["items"]
        if not items:
            return FormattedText([])
        col = max(len(cmd) for cmd, _ in items) + 4
        frags = []
        for i, (cmd, desc) in enumerate(items):
            line = f"  {cmd:<{col}}{desc}"
            style = "fg:#B2B9F9" if i == dropdown["selected"] else ""
            frags.append((style, line))
            if i < len(items) - 1:
                frags.append(("", "\n"))
        return FormattedText(frags)

    kb = KeyBindings()
    _visible = Condition(lambda: bool(dropdown["items"]))

    @kb.add("down", filter=_visible, eager=True)
    def _(event):
        dropdown["selected"] = (dropdown["selected"] + 1) % len(dropdown["items"])

    @kb.add("up", filter=_visible, eager=True)
    def _(event):
        dropdown["selected"] = (dropdown["selected"] - 1) % len(dropdown["items"])

    @kb.add("tab", filter=_visible, eager=True)
    def _(event):
        cmd = dropdown["items"][dropdown["selected"]][0]
        buf.set_document(Document(cmd + " ", len(cmd) + 1))
        dropdown["items"] = []

    @kb.add("escape", eager=True)
    def _(event):
        dropdown["items"] = []

    @kb.add("enter", eager=True)
    @kb.add("c-j", eager=True)
    def _(event):
        if dropdown["items"]:
            cmd = dropdown["items"][dropdown["selected"]][0]
            buf.set_document(Document(cmd, len(cmd)))
            dropdown["items"] = []
        event.app.exit(result=buf.text)

    @kb.add("c-c", eager=True)
    def _(event):
        event.app.exit(exception=KeyboardInterrupt())

    @kb.add("c-d", eager=True)
    def _(event):
        if not buf.text:
            event.app.exit(exception=EOFError())

    # ── 状态栏（右侧显示上下文占比等信息）────────────────────
    def _render_status() -> FormattedText:
        if status_func is None:
            return FormattedText([])
        text = status_func()
        if not text:
            return FormattedText([])
        return FormattedText([("fg:#847ACE", text)])

    _has_status = Condition(lambda: status_func is not None)

    # 输入行: 左侧输入框 + 右侧状态
    input_row = VSplit([
        Window(
            content=BufferControl(buffer=buf),
            get_line_prefix=lambda lineno, wc: _pfx if lineno == 0 and wc == 0 else _pfx_wrap,
            dont_extend_height=True,
            wrap_lines=True,
        ),
        ConditionalContainer(
            Window(
                content=FormattedTextControl(_render_status),
                dont_extend_height=True,
                width=12,
                align=1,  # right align
            ),
            filter=_has_status,
        ),
    ])

    layout = Layout(
        HSplit([
            input_row,
            ConditionalContainer(
                Window(
                    content=FormattedTextControl(_render_dropdown),
                    dont_extend_height=True,
                ),
                filter=_visible,
            ),
        ])
    )

    return Application(
        layout=layout,
        key_bindings=merge_key_bindings([kb, load_emacs_bindings()]),
        cursor=CursorShape.BEAM,
        mouse_support=False,
    ).run()
