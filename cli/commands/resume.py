"""/resume 命令处理 — 交互式会话浏览、恢复、历史渲染"""

from __future__ import annotations

from typing import Any

from prompt_toolkit import Application
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import Window
from prompt_toolkit.layout.controls import FormattedTextControl
from langchain_core.messages import ToolMessage
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

    thread_id = str(selected.get("thread_id") or "").strip()
    messages = session.build_resume_messages(filepath)
    if not messages:
        console.print("  [red]没有可恢复的消息[/red]")
        return None

    if not thread_id:
        console.print("  [red]会话缺少 thread_id，无法恢复执行现场[/red]")
        return None

    config = {"configurable": {"thread_id": thread_id}}
    interrupt_requests: list[dict] = []
    restored_from_checkpoint = False
    try:
        snapshot = graph.get_state(config)
        snapshot_values = getattr(snapshot, "values", None) or {}
        if snapshot_values:
            if _is_pending_tool_execution(snapshot):
                snapshot = _recover_interrupted_tool_execution(
                    console=console,
                    graph=graph,
                    config=config,
                    snapshot=snapshot,
                )
                snapshot_values = getattr(snapshot, "values", None) or {}
            interrupt_requests = _extract_interrupt_requests(snapshot)
            if _has_awaiting_approval(snapshot) and not interrupt_requests:
                console.print("  [red]待审批工具状态不完整：存在 awaiting_approval，但没有可恢复的审批请求[/red]")
                return None
            restored_from_checkpoint = True
            restored_messages = snapshot_values.get("message") or messages
            session.stats.last_input_tokens = session.estimate_messages_tokens(restored_messages)
    except Exception:
        snapshot = None

    if not restored_from_checkpoint:
        console.print("  [red]未找到持久化 checkpoint，当前 /resume 仅支持执行态恢复[/red]")
        return None

    session._resumed_from = filepath

    for notice in _build_resume_consistency_notices(
        snapshot=snapshot,
        transcript_messages=messages,
        render_records=records,
    ):
        console.print(f"  [dim]{notice}[/dim]")

    # 渲染历史
    _render_resumed_history(console, session.load_raw_session(filepath))

    if snapshot and getattr(snapshot, "next", None):
        if interrupt_requests:
            console.print("  [dim]已恢复到待审批状态，接下来会重新请求你的确认。[/dim]")
        else:
            console.print("  [dim]已恢复到挂起执行现场，将继续处理未完成的审批/中断。[/dim]")
        console.print()

    return thread_id


def _is_pending_tool_execution(snapshot: Any) -> bool:
    """判断 checkpoint 是否停在未完成的 tool_execution。"""
    next_nodes = getattr(snapshot, "next", ()) or ()
    return "tool_execution" in next_nodes


def _has_awaiting_approval(snapshot: Any) -> bool:
    values = getattr(snapshot, "values", None) or {}
    pending_calls = values.get("pending_tool_calls") or []
    return any(tc.get("status") == "awaiting_approval" for tc in pending_calls)


def _extract_interrupt_requests(snapshot: Any) -> list[dict]:
    requests: list[dict] = []
    for task in getattr(snapshot, "tasks", ()) or ():
        for intr in getattr(task, "interrupts", ()) or ():
            value = getattr(intr, "value", None)
            if isinstance(value, list):
                requests.extend(v for v in value if isinstance(v, dict))
            elif isinstance(value, dict):
                requests.append(value)
    return requests


def _build_resume_consistency_notices(
    *,
    snapshot: Any,
    transcript_messages: list,
    render_records: list[dict],
) -> list[str]:
    """构建 checkpoint / transcript 一致性提示。"""
    notices: list[str] = []
    snapshot_values = getattr(snapshot, "values", None) or {}
    checkpoint_messages = snapshot_values.get("message") or []

    if checkpoint_messages and not transcript_messages:
        notices.append("恢复以 checkpoint 为准；本地 transcript 缺失，历史展示可能不完整。")
    elif transcript_messages and not checkpoint_messages:
        notices.append("checkpoint 中未找到消息历史，已回退使用 transcript 估算上下文。")
    elif checkpoint_messages and transcript_messages and len(checkpoint_messages) != len(transcript_messages):
        notices.append("checkpoint 与 transcript 历史长度不一致；执行恢复以 checkpoint 为准。")

    if not render_records:
        notices.append("当前 session 缺少可渲染历史记录；仅恢复执行状态。")

    return notices


def _recover_interrupted_tool_execution(
    *,
    console: Console,
    graph: Any,
    config: dict,
    snapshot: Any,
) -> Any:
    """将未完成的 tool_execution 安全收敛为 interrupted，避免恢复后误重跑。"""
    values = getattr(snapshot, "values", None) or {}
    pending_calls = list(values.get("pending_tool_calls") or [])
    interrupted_calls = []
    for tc in pending_calls:
        if tc.get("status") in {"pending", "executing"}:
            interrupted_calls.append({
                **tc,
                "status": "interrupted",
                "error_msg": "session interrupted before tool execution completed",
            })

    if not interrupted_calls:
        return snapshot

    interrupted_messages = [
        ToolMessage(
            content="[工具执行中断，等待恢复策略处理]",
            tool_call_id=tc["call_id"],
            name=tc["tool_name"],
        )
        for tc in interrupted_calls
    ]

    graph.update_state(
        config,
        {
            "message": interrupted_messages,
            "pending_tool_calls": [],
            "completed_tool_calls": [],
            "should_continue": False,
        },
        as_node="observation",
    )
    console.print(
        f"  [yellow]检测到 {len(interrupted_calls)} 个未完成工具执行，"
        "已标记为 interrupted，恢复后不会自动重跑。[/yellow]"
    )
    return graph.get_state(config)


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

        if rtype == "transcript_message":
            role = record.get("role")
            if role == "user":
                content = record.get("content", "")
                line = Text(no_wrap=True)
                line.append(
                    ljust_cols(f"{PROMPT_SYMBOL} {content}", console.width),
                    style=BG_USER,
                )
                console.print(line)
            elif role == "assistant":
                content = record.get("content", "")
                if content:
                    console.print()
                    console.print(content, highlight=False, markup=False)
                    console.print()
            elif role == "tool":
                name = record.get("name", "?")
                content = truncate(record.get("content", ""), 120)
                console.print(f"  [green]✓[/green] [dim]{name} → {content}[/dim]")

        elif rtype == "thought":
            text = record.get("text", "")
            if text:
                console.print(f"  [dim italic]{text}[/dim italic]", highlight=False)

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

        elif rtype == "approval_request":
            name = record.get("tool_name", "?")
            risk = record.get("risk_level", "medium")
            console.print(
                f"  [yellow]⚠ 审批请求[/yellow] "
                f"[dim]{name} (risk={risk})[/dim]"
            )

        elif rtype == "approval_decision":
            decisions = record.get("decisions", {})
            approved = sum(1 for v in decisions.values() if v)
            denied = len(decisions) - approved
            console.print(
                f"  [dim]审批结果: {approved} 通过, {denied} 拒绝[/dim]"
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
