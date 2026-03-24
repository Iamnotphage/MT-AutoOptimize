"""MT-AutoOptimize — REPL 交互循环

通过 EventBus 订阅实时渲染 LLM 流式输出、工具调用和执行结果。
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from rich.console import Console

from core.event_bus import AgentEvent, EventType

if TYPE_CHECKING:
    from core.agent import AgentRuntime

PROMPT_STYLE = "#847ACE"
PROMPT_SYMBOL = "❯"


class Repl:
    """交互式 读取-执行-打印 循环"""

    def __init__(self, console: Console, runtime: AgentRuntime) -> None:
        self.console = console
        self.runtime = runtime
        self.running = True
        self.thread_id = uuid.uuid4().hex

        self._streaming = False

        self._bind_events()

    # ── EventBus 订阅 ───────────────────────────────────────────

    def _bind_events(self) -> None:
        bus = self.runtime.event_bus
        bus.subscribe(EventType.CONTENT, self._on_content)
        bus.subscribe(EventType.THOUGHT, self._on_thought)
        bus.subscribe(EventType.TOOL_CALL_REQUEST, self._on_tool_request)
        bus.subscribe(EventType.TOOL_CALL_COMPLETE, self._on_tool_complete)
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
        name = event.data.get("tool_name", "?")
        args = event.data.get("arguments", {})
        args_brief = ", ".join(f"{k}={_truncate(v)}" for k, v in args.items())
        self.console.print(
            f"\n  [bold cyan]⚡ {name}[/bold cyan]"
            f"[dim]({args_brief})[/dim]"
        )

    def _on_tool_complete(self, event: AgentEvent) -> None:
        name = event.data.get("tool_name", "?")
        status = event.data.get("status", "")
        if status == "success":
            result_preview = _truncate(event.data.get("result", ""), 120)
            self.console.print(f"  [green]✓[/green] [dim]{name} → {result_preview}[/dim]")
        elif status == "error":
            err = event.data.get("error_msg", "unknown")
            self.console.print(f"  [red]✗[/red] [dim]{name} 失败: {err}[/dim]")
        elif status == "cancelled":
            self.console.print(f"  [yellow]⊘[/yellow] [dim]{name} 已取消[/dim]")

    def _on_error(self, event: AgentEvent) -> None:
        self._end_stream()
        err = event.data.get("error", "未知错误")
        self.console.print(f"\n  [red bold]ERROR[/red bold] {err}")

    # ── Agent 调用 ───────────────────────────────────────────────

    def _invoke_agent(self, user_input: str) -> None:
        from langchain_core.messages import HumanMessage

        config = {"configurable": {"thread_id": self.thread_id}}
        state_input = {
            "message": [HumanMessage(content=user_input)],
        }

        try:
            self.runtime.graph.invoke(state_input, config)
        except Exception as e:
            self._end_stream()
            self.console.print(f"\n  [red bold]Agent 执行出错:[/red bold] {e}")

        self._end_stream()
        self.console.print()

    # ── REPL 命令 ────────────────────────────────────────────────

    def _prompt(self) -> str:
        return f"[{PROMPT_STYLE}]{PROMPT_SYMBOL}[/{PROMPT_STYLE}] "

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

    # ── 主循环 ───────────────────────────────────────────────────

    def run(self) -> None:
        while self.running:
            try:
                user_input = self.console.input(self._prompt())
            except EOFError:
                break
            except KeyboardInterrupt:
                self._end_stream()
                self.console.print()
                continue

            stripped = user_input.strip()
            if not stripped:
                continue

            if stripped.startswith("/"):
                if not self._handle_command(stripped.lower()):
                    break
                continue

            self._invoke_agent(stripped)


# ── 辅助 ────────────────────────────────────────────────────────


def _truncate(val: Any, maxlen: int = 60) -> str:
    s = str(val)
    return s if len(s) <= maxlen else s[:maxlen] + "…"
