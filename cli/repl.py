"""MT-AutoOptimize — REPL 交互循环

纯粹的 Read-Eval-Print Loop 骨架。
渲染、命令处理、输入组件均委托给独立模块。
"""

from __future__ import annotations

import sys
import uuid
from typing import TYPE_CHECKING

from langgraph.types import Command
from rich.console import Console
from rich.text import Text

from prompt_toolkit.history import InMemoryHistory

from cli.commands.context import cmd_context
from cli.commands.memory import cmd_memory
from cli.commands.resume import cmd_resume
from config.settings import CONTEXT as CONTEXT_CONFIG
from cli.event_handlers.stream import StreamHandler
from cli.ui.input import read_input
from cli.utils.text import (
    BG_USER,
    PROMPT_STYLE,
    PROMPT_SYMBOL,
    RISK_STYLE,
    ljust_cols,
    truncate,
)

if TYPE_CHECKING:
    from core.agent import AgentRuntime


class Repl:
    """交互式 读取-执行-打印 循环"""

    def __init__(self, console: Console, runtime: AgentRuntime) -> None:
        self.console = console
        self.runtime = runtime
        self.thread_id = uuid.uuid4().hex
        self.runtime.session.set_thread_id(self.thread_id)
        self._closed = False
        self._history = InMemoryHistory()
        self._token_limit = CONTEXT_CONFIG.get("token_limit", 65536)

        # 事件处理（渲染 + 录制）
        self._stream = StreamHandler(
            console=console,
            event_bus=runtime.event_bus,
            session=runtime.session,
        )

    # ── 主循环 ───────────────────────────────────────────────────

    def run(self) -> None:
        while True:
            try:
                user_input = read_input(self._history, status_func=self._context_status)
            except EOFError:
                self._on_exit()
                break
            except KeyboardInterrupt:
                self._stream.end_stream()
                self.console.print()
                continue

            stripped = user_input.strip()
            if not stripped:
                continue

            self._render_user_input(stripped)

            if stripped.startswith("/"):
                if not self._handle_command(stripped):
                    break
                continue

            self._invoke_agent(stripped)

    # ── Agent 调用 ───────────────────────────────────────────────

    def _invoke_agent(self, user_input: str) -> None:
        from langchain_core.messages import HumanMessage

        session = self.runtime.session
        session.set_thread_id(self.thread_id)
        session.stats.prompt_count += 1
        session.record({
            "type": "transcript_message",
            "role": "user",
            "content": user_input,
        })

        config = {"configurable": {"thread_id": self.thread_id}}
        state_input: dict | Command = {
            "message": [HumanMessage(content=user_input)],
        }

        try:
            self.runtime.graph.invoke(state_input, config)
            self._resume_pending_interrupts(config)

        except Exception as e:
            self._stream.end_stream()
            self.console.print(f"\n  [red bold]Agent 执行出错:[/red bold] {e}")

        self._stream.end_stream()
        self.console.print()

    def _resume_pending_interrupts(self, config: dict) -> None:
        """处理已存在的 interrupt，直到图继续运行完成。"""
        while self._has_pending_interrupt(config):
            requests = self._get_interrupt_requests(config)
            if not requests:
                break
            decisions = self._prompt_approval(requests)
            self.runtime.graph.invoke(Command(resume=decisions), config)

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
        self._stream.end_stream()

        if not requests:
            return {}

        self.console.print()
        self.console.print("  [bold yellow]⚠ 以下工具需要确认[/bold yellow]")
        self.console.print()

        for req in requests:
            name = req.get("tool_name", "?")
            risk = req.get("risk_level", "medium")
            args = req.get("arguments", {})
            style = RISK_STYLE.get(risk, "yellow")

            self.console.print(f"    [{style}]● {name}[/{style}]  [dim]risk={risk}[/dim]")
            for k, v in args.items():
                self.console.print(f"      [dim]{k}: {truncate(v, 100)}[/dim]")

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

    # ── 退出 & 统计 ──────────────────────────────────────────────

    def _on_exit(self) -> None:
        if self._closed:
            return
        self._closed = True

        filepath = self.runtime.session.flush()
        checkpoint_manager = getattr(self.runtime, "checkpoint_manager", None)
        if checkpoint_manager is not None:
            checkpoint_manager.__exit__(None, None, None)
            self.runtime.checkpoint_manager = None
        if filepath:
            self.console.print(f"\n  [dim]会话已保存 → {filepath}[/dim]")
        self._render_session_stats()
        self.console.print("  [dim]再见！[/dim]\n")

    def close(self) -> None:
        """对外暴露的幂等关闭入口。"""
        self._on_exit()

    def _render_session_stats(self) -> None:
        stats = self.runtime.session.stats

        if stats.turn_count == 0 and stats.prompt_count == 0:
            return

        duration = stats.duration_seconds
        if duration >= 60:
            dur_str = f"{int(duration // 60)}m {int(duration % 60)}s"
        else:
            dur_str = f"{int(duration)}s"

        self.console.print()
        self.console.print("  [dim]─────────────────────────────────────[/dim]")
        self.console.print("  [bold dim]Session Summary[/bold dim]")

        if stats.model:
            self.console.print(f"  [dim]Model:     {stats.model}[/dim]")
        self.console.print(f"  [dim]Duration:  {dur_str}[/dim]")

        if stats.prompt_count:
            self.console.print(f"  [dim]Prompts:   {stats.prompt_count}[/dim]")
        if stats.turn_count:
            self.console.print(f"  [dim]Turns:     {stats.turn_count}[/dim]")

        if stats.total_tokens > 0:
            self.console.print(
                f"  [dim]Tokens:    {stats.total_tokens:,} "
                f"(in: {stats.total_input_tokens:,} / out: {stats.total_output_tokens:,})[/dim]"
            )

        if stats.tool_calls_total > 0:
            self.console.print(
                f"  [dim]Tools:     {stats.tool_calls_total} calls "
                f"({stats.tool_calls_success} success, {stats.tool_calls_failed} failed)[/dim]"
            )

        self.console.print("  [dim]─────────────────────────────────────[/dim]")

    # ── 命令路由 ─────────────────────────────────────────────────

    def _handle_command(self, cmd: str) -> bool:
        """处理 /command。返回 True 继续循环，False 退出。"""
        parts = cmd.split(maxsplit=2)
        base = parts[0].lower()

        match base:
            case "/help" | "/h" | "/?":
                self._show_help()
            case "/exit" | "/quit" | "/q":
                self._on_exit()
                return False
            case "/version" | "/v":
                from app import VERSION
                self.console.print(f"  [dim]v{VERSION}[/dim]")
            case "/clear":
                self.console.clear()
            case "/new":
                self.thread_id = uuid.uuid4().hex
                self.runtime.session.set_thread_id(self.thread_id)
                self.console.print("  [dim]已开启新会话[/dim]")
            case "/resume":
                new_tid = cmd_resume(self.console, self.runtime.session, self.runtime.graph)
                if new_tid:
                    self.thread_id = new_tid
                    self.runtime.session.set_thread_id(self.thread_id)
                    self._resume_pending_interrupts({"configurable": {"thread_id": self.thread_id}})
            case "/context":
                cmd_context(self.console, self.runtime.context_manager, parts[1:])
            case "/memory":
                cmd_memory(self.console, self.runtime.context_manager, parts[1:])
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
            ("/resume", "浏览并恢复历史会话"),
            ("/context show", "显示当前已加载的上下文"),
            ("/context reload", "重新加载上下文文件"),
            ("/memory list", "列出所有已保存的记忆"),
            ("/memory add <fact>", "添加一条记忆"),
            ("/memory remove <n>", "删除第 n 条记忆 (从 1 开始)"),
            ("/exit, /q", "退出"),
        ]
        for name, desc in cmds:
            self.console.print(
                f"    [{PROMPT_STYLE}]{name:<24}[/{PROMPT_STYLE}] [dim]{desc}[/dim]"
            )
        self.console.print()

    # ── 上下文状态 ─────────────────────────────────────────────

    def _context_status(self) -> str:
        """返回上下文占比文本，如 '42%' 或空字符串。"""
        last = self.runtime.session.stats.last_input_tokens
        if last <= 0:
            return ""
        pct = min(last / self._token_limit * 100, 100)
        return f"{pct:.0f}%"

    # ── 渲染辅助 ─────────────────────────────────────────────────

    def _render_user_input(self, user_input: str) -> None:
        """用灰色背景重新渲染用户输入行"""
        sys.stdout.write("\x1b[A\x1b[2K\r")
        sys.stdout.flush()
        line = Text(no_wrap=True)
        content = f"{PROMPT_SYMBOL} {user_input}"
        line.append(ljust_cols(content, self.console.width), style=BG_USER)
        self.console.print(line)
