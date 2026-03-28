"""MT-AutoOptimize — REPL 交互循环

通过 EventBus 订阅实时渲染 LLM 流式输出、工具调用和执行结果。
支持 LangGraph interrupt/resume 处理 human_approval 节点。
"""

from __future__ import annotations

import sys
import uuid
from typing import TYPE_CHECKING, Any

from langgraph.types import Command
from rich.console import Console
from rich.text import Text
import unicodedata
from prompt_toolkit import prompt as pt_prompt
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.cursor_shapes import CursorShape

from core.event_bus import AgentEvent, EventType

if TYPE_CHECKING:
    from core.agent import AgentRuntime

PROMPT_STYLE = "#847ACE"
PROMPT_SYMBOL = "❯"
_RISK_STYLE = {"low": "green", "medium": "yellow", "high": "red bold"}
_TOOL_DISPLAY = {"write_file": "Write", "read_file": "Read", "ls": "Ls"}
_BG_USER = "on #252530"


class Repl:
    """交互式 读取-执行-打印 循环"""

    def __init__(self, console: Console, runtime: AgentRuntime) -> None:
        self.console = console
        self.runtime = runtime
        self.running = True
        self.thread_id = uuid.uuid4().hex

        self._streaming = False
        self._last_tool_had_diff = False

        self._bind_events()

    # ── EventBus 订阅 ───────────────────────────────────────────

    def _bind_events(self) -> None:
        bus = self.runtime.event_bus
        bus.subscribe(EventType.CONTENT, self._on_content)
        bus.subscribe(EventType.THOUGHT, self._on_thought)
        bus.subscribe(EventType.TOOL_CALL_REQUEST, self._on_tool_request)
        bus.subscribe(EventType.TOOL_CALL_COMPLETE, self._on_tool_complete)
        bus.subscribe(EventType.TOOL_LIVE_OUTPUT, self._on_tool_live_output)
        bus.subscribe(EventType.ERROR, self._on_error)

    def _end_stream(self) -> None:
        if self._streaming:
            self.console.print()
            self._streaming = False

    def _on_content(self, event: AgentEvent) -> None:
        text = event.data.get("text", "")
        if not text:
            return
        if not self._streaming:
            self.console.print()
            self._streaming = True
        self.console.print(text, end="", highlight=False, markup=False)

    def _on_thought(self, event: AgentEvent) -> None:
        text = event.data.get("text", "")
        if not text:
            return
        self._end_stream()
        self.console.print(f"  [dim italic]{text}[/dim italic]", end="", highlight=False)

    def _on_tool_request(self, event: AgentEvent) -> None:
        self._end_stream()
        self._last_tool_had_diff = False
        name = event.data.get("tool_name", "?")
        args = event.data.get("arguments", {})
        display = _TOOL_DISPLAY.get(name, name)
        file_path = args.get("file_path")

        if file_path:
            self.console.print(f"\n  [bold cyan]⏺ {display}[/bold cyan]({file_path})")
        else:
            args_brief = ", ".join(f"{k}={_truncate(v)}" for k, v in args.items())
            self.console.print(
                f"\n  [bold cyan]⏺ {display}[/bold cyan]"
                f"[dim]({args_brief})[/dim]"
            )

    def _on_tool_complete(self, event: AgentEvent) -> None:
        name = event.data.get("tool_name", "?")
        status = event.data.get("status", "")

        if self._last_tool_had_diff:
            self._last_tool_had_diff = False
            if status == "error":
                err = event.data.get("error_msg", "unknown")
                self.console.print(f"  [red]✗[/red] [dim]{name} 失败: {err}[/dim]")
            return

        if status == "success":
            result_preview = _truncate(event.data.get("result", ""), 120)
            self.console.print(f"  [green]✓[/green] [dim]{name} → {result_preview}[/dim]")
        elif status == "error":
            err = event.data.get("error_msg", "unknown")
            self.console.print(f"  [red]✗[/red] [dim]{name} 失败: {err}[/dim]")
        elif status == "cancelled":
            self.console.print(f"  [yellow]⊘[/yellow] [dim]{name} 已取消[/dim]")

    def _on_tool_live_output(self, event: AgentEvent) -> None:
        if event.data.get("kind") == "diff":
            from cli.diff_renderer import render_diff
            self._last_tool_had_diff = True
            render_diff(self.console, event.data["diff"])

    def _on_error(self, event: AgentEvent) -> None:
        self._end_stream()
        err = event.data.get("error", "未知错误")
        self.console.print(f"\n  [red bold]ERROR[/red bold] {err}")

    # ── Agent 调用 ───────────────────────────────────────────────

    def _invoke_agent(self, user_input: str) -> None:
        from langchain_core.messages import HumanMessage

        config = {"configurable": {"thread_id": self.thread_id}}
        state_input: dict | Command = {
            "message": [HumanMessage(content=user_input)],
        }

        try:
            self.runtime.graph.invoke(state_input, config)

            while self._has_pending_interrupt(config):
                requests = self._get_interrupt_requests(config)
                decisions = self._prompt_approval(requests)
                self.runtime.graph.invoke(Command(resume=decisions), config)

        except Exception as e:
            self._end_stream()
            self.console.print(f"\n  [red bold]Agent 执行出错:[/red bold] {e}")

        self._end_stream()
        self.console.print()

    # ── Interrupt / 人工审批 ─────────────────────────────────────

    def _has_pending_interrupt(self, config: dict) -> bool:
        snapshot = self.runtime.graph.get_state(config)
        return bool(snapshot.next)

    def _get_interrupt_requests(self, config: dict) -> list[dict]:
        snapshot = self.runtime.graph.get_state(config)
        requests: list[dict] = []
        for task in snapshot.tasks:
            for intr in getattr(task, "interrupts", []):
                val = intr.value
                if isinstance(val, list):
                    requests.extend(val)
                elif isinstance(val, dict):
                    requests.append(val)
        return requests

    def _prompt_approval(self, requests: list[dict]) -> dict[str, bool]:
        self._end_stream()

        if not requests:
            return {}

        self.console.print()
        self.console.print("  [bold yellow]⚠ 以下工具需要确认[/bold yellow]")
        self.console.print()

        for req in requests:
            name = req.get("tool_name", "?")
            risk = req.get("risk_level", "medium")
            args = req.get("arguments", {})
            style = _RISK_STYLE.get(risk, "yellow")

            self.console.print(f"    [{style}]● {name}[/{style}]  [dim]risk={risk}[/dim]")
            for k, v in args.items():
                self.console.print(f"      [dim]{k}: {_truncate(v, 100)}[/dim]")

        self.console.print()

        decisions: dict[str, bool] = {}

        if len(requests) == 1:
            answer = self.console.input(
                f"  [{PROMPT_STYLE}]允许执行?[/{PROMPT_STYLE}] [dim](y/N)[/dim] "
            ).strip().lower()
            approved = answer in ("y", "yes")
            decisions[requests[0]["call_id"]] = approved
        else:
            answer = self.console.input(
                f"  [{PROMPT_STYLE}]全部允许?[/{PROMPT_STYLE}] [dim](y/N/逐条确认输入 e)[/dim] "
            ).strip().lower()

            if answer in ("y", "yes"):
                for req in requests:
                    decisions[req["call_id"]] = True
            elif answer == "e":
                for req in requests:
                    name = req.get("tool_name", "?")
                    ans = self.console.input(
                        f"    [{PROMPT_STYLE}]{name}?[/{PROMPT_STYLE}] [dim](y/N)[/dim] "
                    ).strip().lower()
                    decisions[req["call_id"]] = ans in ("y", "yes")
            else:
                for req in requests:
                    decisions[req["call_id"]] = False

        approved_count = sum(1 for v in decisions.values() if v)
        denied_count = len(decisions) - approved_count
        if approved_count:
            self.console.print(f"  [green]✓ 已批准 {approved_count} 项[/green]", end="")
        if denied_count:
            self.console.print(f"  [red]✗ 已拒绝 {denied_count} 项[/red]", end="")
        self.console.print()

        return decisions

    # ── REPL 命令 ────────────────────────────────────────────────


    def _prompt(self) -> str:
        # ANSI 紫色 ❯，prompt_toolkit 会正确计算其宽度
        return f"\x1b[38;2;132;122;206m{PROMPT_SYMBOL}\x1b[0m "

    def _handle_command(self, cmd: str) -> bool:
        """处理 /command。返回 True 继续循环，False 退出。"""
        match cmd:
            case "/help" | "/h" | "/?":
                self._show_help()
            case "/exit" | "/quit" | "/q":
                self.console.print("\n  [dim]再见！[/dim]\n")
                return False
            case "/version" | "/v":
                from cli.app import VERSION
                self.console.print(f"  [dim]v{VERSION}[/dim]")
            case "/clear":
                self.console.clear()
            case "/new":
                self.thread_id = uuid.uuid4().hex
                self.console.print("  [dim]已开启新会话[/dim]")
            case _:
                self.console.print(f"  [red]未知命令:[/red] {cmd}")
                self.console.print("  [dim]输入 /help 查看可用命令[/dim]")
        return True

    def _show_help(self) -> None:
        self.console.print()
        self.console.print("  [bold]可用命令[/bold]")
        self.console.print()
        cmds = [
            ("/help, /h", "显示帮助信息"),
            ("/version, /v", "显示版本号"),
            ("/clear", "清屏"),
            ("/new", "开启新会话 (清空对话历史)"),
            ("/exit, /q", "退出"),
        ]
        for name, desc in cmds:
            self.console.print(
                f"    [{PROMPT_STYLE}]{name:<16}[/{PROMPT_STYLE}] [dim]{desc}[/dim]"
            )
        self.console.print()
        self.console.print("  [bold]使用示例[/bold]")
        self.console.print()
        self.console.print("  [dim]  读取 config.json 文件[/dim]")
        self.console.print("  [dim]  分析 src/main.c 的优化方向[/dim]")
        self.console.print()

    # ── 渲染 ─────────────────────────────────────────────────────

    def _render_user_input(self, user_input: str) -> None:
        sys.stdout.write("\x1b[A\x1b[2K\r")
        sys.stdout.flush()
        line = Text(no_wrap=True)
        content = f"{PROMPT_SYMBOL} {user_input}"
        line.append(
            _ljust_cols(content, self.console.width),
            style=_BG_USER,
        )
        self.console.print(line)

    # ── 主循环 ───────────────────────────────────────────────────

    def run(self) -> None:
        while self.running:
            try:
                user_input = pt_prompt(ANSI(self._prompt()), cursor=CursorShape.BEAM)
            except EOFError:
                break
            except KeyboardInterrupt:
                self._end_stream()
                self.console.print()
                continue

            stripped = user_input.strip()
            if not stripped:
                continue

            self._render_user_input(stripped)

            if stripped.startswith("/"):
                if not self._handle_command(stripped.lower()):
                    break
                continue

            self._invoke_agent(stripped)


# ── 辅助 ────────────────────────────────────────────────────────


def _truncate(val: Any, maxlen: int = 60) -> str:
    s = str(val)
    return s if len(s) <= maxlen else s[:maxlen] + "…"

# ── 新增辅助函数（类外或类内均可）──────────────────────────────────

def _display_width(s: str) -> int:
    """计算字符串的终端显示列数（全角字符占 2 列）"""
    width = 0
    for ch in s:
        eaw = unicodedata.east_asian_width(ch)
        width += 2 if eaw in ("W", "F") else 1
    return width


def _ljust_cols(s: str, total_cols: int, fillchar: str = " ") -> str:
    """按显示列数右填充，而非字符数"""
    pad = max(0, total_cols - _display_width(s))
    return s + fillchar * pad