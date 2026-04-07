"""EventBus 流式事件处理 — 渲染 + 录制

接收 console 和 session_recorder 作为依赖（方案 B），不直接依赖 Repl。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Console

from cli.utils.text import TOOL_DISPLAY, truncate
from core.event_bus import AgentEvent, EventBus, EventType

if TYPE_CHECKING:
    from core.session import SessionRecorder


class StreamHandler:
    """订阅 EventBus 事件，实时渲染到 Console 并录制到 SessionRecorder。"""

    def __init__(self, console: Console, event_bus: EventBus, session: SessionRecorder) -> None:
        self._console = console
        self._session = session

        self._streaming = False
        self._last_tool_had_diff = False
        self._content_buf: list[str] = []
        self._thought_buf: list[str] = []

        # 订阅事件
        event_bus.subscribe(EventType.CONTENT, self.on_content)
        event_bus.subscribe(EventType.THOUGHT, self.on_thought)
        event_bus.subscribe(EventType.TOOL_CALL_REQUEST, self.on_tool_request)
        event_bus.subscribe(EventType.TOOL_CALL_COMPLETE, self.on_tool_complete)
        event_bus.subscribe(EventType.TOOL_LIVE_OUTPUT, self.on_tool_live_output)
        event_bus.subscribe(EventType.CONTEXT_COMPRESSED, self.on_context_compressed)
        event_bus.subscribe(EventType.ERROR, self.on_error)

    # ── 流式控制 ─────────────────────────────────────────────────

    def end_stream(self) -> None:
        """结束流式输出并 flush 累积的 content/thought 到录制。"""
        if self._streaming:
            self._console.print()
            self._streaming = False
        if self._thought_buf:
            self._session.record({
                "type": "thought",
                "text": "".join(self._thought_buf),
            })
            self._thought_buf.clear()
        if self._content_buf:
            self._session.record({
                "type": "assistant",
                "content": "".join(self._content_buf),
                "model": self._session.stats.model,
            })
            self._content_buf.clear()

    # ── 事件处理 ─────────────────────────────────────────────────

    def on_content(self, event: AgentEvent) -> None:
        text = event.data.get("text", "")
        if not text:
            return
        if not self._streaming:
            self._console.print()
            self._streaming = True
        self._console.print(text, end="", highlight=False, markup=False)
        self._content_buf.append(text)

    def on_thought(self, event: AgentEvent) -> None:
        text = event.data.get("text", "")
        if not text:
            return
        self.end_stream()
        self._console.print(f"  [dim italic]{text}[/dim italic]", end="", highlight=False)
        self._thought_buf.append(text)

    def on_tool_request(self, event: AgentEvent) -> None:
        self.end_stream()
        self._last_tool_had_diff = False
        name = event.data.get("tool_name", "?")
        args = event.data.get("arguments", {})
        display = TOOL_DISPLAY.get(name, name)
        file_path = args.get("file_path")

        if file_path:
            self._console.print(f"\n  [bold cyan]⏺ {display}[/bold cyan]({file_path})")
        else:
            args_brief = ", ".join(f"{k}={truncate(v)}" for k, v in args.items())
            self._console.print(
                f"\n  [bold cyan]⏺ {display}[/bold cyan]"
                f"[dim]({args_brief})[/dim]"
            )
        self._session.record({
            "type": "tool_request",
            "tool_name": name,
            "arguments": args,
        })

    def on_tool_complete(self, event: AgentEvent) -> None:
        name = event.data.get("tool_name", "?")
        status = event.data.get("status", "")

        if self._last_tool_had_diff:
            self._last_tool_had_diff = False
            if status == "error":
                err = event.data.get("error_msg", "unknown")
                self._console.print(f"  [red]✗[/red] [dim]{name} 失败: {err}[/dim]")
            self._session.record({
                "type": "tool_complete",
                "tool_name": name,
                "status": status,
                "had_diff": True,
                "error_msg": event.data.get("error_msg"),
            })
            return

        if status == "success":
            result_preview = truncate(event.data.get("result", ""), 120)
            self._console.print(f"  [green]✓[/green] [dim]{name} → {result_preview}[/dim]")
        elif status == "error":
            err = event.data.get("error_msg", "unknown")
            self._console.print(f"  [red]✗[/red] [dim]{name} 失败: {err}[/dim]")
        elif status == "cancelled":
            self._console.print(f"  [yellow]⊘[/yellow] [dim]{name} 已取消[/dim]")

        self._session.record({
            "type": "tool_complete",
            "tool_name": name,
            "status": status,
            "result": event.data.get("result", ""),
            "error_msg": event.data.get("error_msg"),
        })

    def on_tool_live_output(self, event: AgentEvent) -> None:
        if event.data.get("kind") == "diff":
            from cli.diff_renderer import render_diff
            self._last_tool_had_diff = True
            render_diff(self._console, event.data["diff"])
            diff_obj = event.data["diff"]
            self._session.record({
                "type": "tool_diff",
                "tool_name": event.data.get("tool_name", ""),
                "unified_diff": diff_obj.unified_diff,
                "added": diff_obj.added,
                "removed": diff_obj.removed,
                "file_path": diff_obj.file_path,
                "is_new": diff_obj.is_new,
            })

    def on_error(self, event: AgentEvent) -> None:
        self.end_stream()
        err = event.data.get("error", "未知错误")
        self._console.print(f"\n  [red bold]ERROR[/red bold] {err}")

    def on_context_compressed(self, event: AgentEvent) -> None:
        self.end_stream()
        removed = event.data.get("removed_count", 0)
        kept = event.data.get("kept_count", 0)
        summary = event.data.get("summary", "")
        self._session.record({
            "type": "compression",
            "summary": summary,
            "removed_count": removed,
            "kept_count": kept,
        })
        self._console.print(
            f"\n  [bold yellow]⚡ 上下文已压缩[/bold yellow] "
            f"[dim]({removed} 条消息摘要化, 保留 {kept} 条)[/dim]"
        )
