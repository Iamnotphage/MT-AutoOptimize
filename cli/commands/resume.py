"""/resume 命令处理 — 交互式会话浏览、恢复、历史渲染"""

from __future__ import annotations

import uuid
from typing import Any

from prompt_toolkit import Application
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import Window
from prompt_toolkit.layout.controls import FormattedTextControl
from rich.console import Console
from rich.text import Text

from cli.utils.text import BG_USER, PROMPT_STYLE, PROMPT_SYMBOL, TOOL_DISPLAY, ljust_cols, truncate
from core.session import SessionRecorder, format_file_size, format_relative_time


def cmd_resume(
    console: Console,
    session: SessionRecorder,
    graph: Any,
) -> str | None:
    """
    交互式浏览并恢复历史会话。

    Returns:
        新的 thread_id（恢复成功时），或 None（取消/失败时）。
    """
    sessions = session.list_sessions()
    if not sessions:
        console.print("  [dim]暂无历史会话。[/dim]")
        return None

    selected = _session_picker(sessions)
    if selected is None:
        console.print("  [dim]已取消[/dim]")
        return None

    filepath = selected["filepath"]

    records = session.load_session(filepath)
    if not records:
        console.print("  [red]会话为空，无法恢复[/red]")
        return None

    # 构建 LangChain messages（若有压缩记录，仅恢复最后一次压缩摘要及其后的消息）
    thread_id = uuid.uuid4().hex
    config = {"configurable": {"thread_id": thread_id}}
    messages = session.build_resume_messages(filepath)

    if not messages:
        console.print("  [red]没有可恢复的消息[/red]")
        return None

    try:
        graph.update_state(config, {"message": messages})
    except Exception as e:
        console.print(f"  [red]恢复失败:[/red] {e}")
        return None

    session._resumed_from = filepath
    session.stats.last_input_tokens = session.estimate_messages_tokens(messages)

    # 渲染历史
    _render_resumed_history(console, session.load_raw_session(filepath))

    return thread_id


# ── 交互式会话选择器 ─────────────────────────────────────────────

def _session_picker(sessions: list[dict]) -> dict | None:
    """上下键 + 回车的交互式会话选择。"""
    state = {"selected": 0}

    items: list[tuple[str, str]] = []
    for s in sessions:
        title = s["first_user_message"]
        if len(title) > 70:
            title = title[:67] + "..."
        parts = [format_relative_time(s["timestamp"])]
        if s.get("branch"):
            parts.append(s["branch"])
        parts.append(format_file_size(s.get("file_size", 0)))
        items.append((title, " · ".join(parts)))

    kb = KeyBindings()

    @kb.add("up", eager=True)
    @kb.add("k", eager=True)
    def _(event):
        state["selected"] = (state["selected"] - 1) % len(items)

    @kb.add("down", eager=True)
    @kb.add("j", eager=True)
    def _(event):
        state["selected"] = (state["selected"] + 1) % len(items)

    @kb.add("enter", eager=True)
    def _(event):
        event.app.exit(result=state["selected"])

    @kb.add("escape", eager=True)
    @kb.add("c-c", eager=True)
    @kb.add("q", eager=True)
    def _(event):
        event.app.exit(result=None)

    def _render_list() -> FormattedText:
        frags: list[tuple[str, str]] = []
        frags.append(("bold", "  Resume a conversation\n"))
        frags.append(("", "\n"))
        for i, (title, subtitle) in enumerate(items):
            if i == state["selected"]:
                frags.append(("fg:#847ACE bold", f"  > {title}\n"))
                frags.append(("fg:#847ACE", f"    {subtitle}\n"))
            else:
                frags.append(("dim", f"    {title}\n"))
                frags.append(("dim", f"    {subtitle}\n"))
            if i < len(items) - 1:
                frags.append(("", "\n"))
        frags.append(("", "\n"))
        frags.append(("dim", "  ↑↓ navigate · enter select · esc cancel"))
        return FormattedText(frags)

    layout = Layout(
        Window(content=FormattedTextControl(_render_list), dont_extend_height=True)
    )

    result = Application(layout=layout, key_bindings=kb, mouse_support=False).run()
    if result is None:
        return None
    return sessions[result]


# ── 历史渲染 ─────────────────────────────────────────────────────

def _render_resumed_history(console: Console, records: list[dict]) -> None:
    """渲染恢复的历史消息，与实时渲染视觉一致。"""
    from cli.diff_renderer import render_diff
    from core.utils.diff import DiffResult

    console.print()
    console.print("  [dim]─── 恢复的会话历史 ───[/dim]")

    for record in records:
        rtype = record.get("type")

        if rtype == "user":
            content = record.get("display", record.get("content", ""))
            line = Text(no_wrap=True)
            line.append(
                ljust_cols(f"{PROMPT_SYMBOL} {content}", console.width),
                style=BG_USER,
            )
            console.print(line)

        elif rtype == "thought":
            text = record.get("text", "")
            if text:
                console.print(f"  [dim italic]{text}[/dim italic]", highlight=False)

        elif rtype == "assistant":
            content = record.get("content", "")
            if content:
                console.print()
                console.print(content, highlight=False, markup=False)
                console.print()

        elif rtype == "tool_request":
            name = record.get("tool_name", "?")
            args = record.get("arguments", {})
            display = TOOL_DISPLAY.get(name, name)
            file_path = args.get("file_path")

            if file_path:
                console.print(f"\n  [bold cyan]⏺ {display}[/bold cyan]({file_path})")
            else:
                args_brief = ", ".join(f"{k}={truncate(v)}" for k, v in args.items())
                console.print(
                    f"\n  [bold cyan]⏺ {display}[/bold cyan]"
                    f"[dim]({args_brief})[/dim]"
                )

        elif rtype == "tool_diff":
            diff = DiffResult(
                file_path=record.get("file_path", ""),
                unified_diff=record.get("unified_diff", ""),
                added=record.get("added", 0),
                removed=record.get("removed", 0),
                is_new=record.get("is_new", False),
            )
            render_diff(console, diff)

        elif rtype == "tool_complete":
            name = record.get("tool_name", "?")
            status = record.get("status", "")
            had_diff = record.get("had_diff", False)

            if had_diff:
                if status == "error":
                    err = record.get("error_msg", "unknown")
                    console.print(f"  [red]✗[/red] [dim]{name} 失败: {err}[/dim]")
            elif status == "success":
                result_preview = truncate(record.get("result", ""), 120)
                console.print(f"  [green]✓[/green] [dim]{name} → {result_preview}[/dim]")
            elif status == "error":
                err = record.get("error_msg", "unknown")
                console.print(f"  [red]✗[/red] [dim]{name} 失败: {err}[/dim]")
            elif status == "cancelled":
                console.print(f"  [yellow]⊘[/yellow] [dim]{name} 已取消[/dim]")

    console.print()
    console.print("  [dim]─── 继续对话 ───[/dim]")
    console.print()
