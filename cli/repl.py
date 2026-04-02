"""MT-AutoOptimize — REPL 交互循环

通过 EventBus 订阅实时渲染 LLM 流式输出、工具调用和执行结果。
支持 LangGraph interrupt/resume 处理 human_approval 节点。
"""

from __future__ import annotations

import sys
import uuid
import unicodedata
from typing import TYPE_CHECKING, Any

from langgraph.types import Command
from rich.console import Console
from rich.text import Text

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
from prompt_toolkit.layout.containers import ConditionalContainer, HSplit, Window
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl

from core.event_bus import AgentEvent, EventType

if TYPE_CHECKING:
    from core.agent import AgentRuntime

PROMPT_STYLE = "#847ACE"
PROMPT_SYMBOL = "❯"
_RISK_STYLE = {"low": "green", "medium": "yellow", "high": "red bold"}
_TOOL_DISPLAY = {"write_file": "Write", "read_file": "Read", "ls": "Ls"}
_BG_USER = "on #252530"

_COMMANDS = [
    ("/clear",   "清屏"),
    ("/new",     "开启新会话 (清空对话历史)"),
    ("/context", "查看/刷新上下文 (show|reload)"),
    ("/memory",  "管理记忆 (list|add|remove)"),
    ("/help",    "显示帮助信息"),
    ("/version", "显示版本号"),
    ("/exit",    "退出"),
]


class Repl:
    """交互式 读取-执行-打印 循环"""

    def __init__(self, console: Console, runtime: AgentRuntime) -> None:
        self.console = console
        self.runtime = runtime
        self.running = True
        self.thread_id = uuid.uuid4().hex

        self._streaming = False
        self._last_tool_had_diff = False
        self._history = InMemoryHistory()

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

        # 记录用户消息
        cm = self.runtime.context_manager
        cm.session_stats.prompt_count += 1
        cm.record_message({
            "type": "user",
            "display": user_input,
        })

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

        # 记录助手响应（从 graph state 提取最后一条 AI 消息）
        try:
            snapshot = self.runtime.graph.get_state(config)
            messages = snapshot.values.get("message", [])
            for msg in reversed(messages):
                if hasattr(msg, "content") and getattr(msg, "type", None) == "ai":
                    cm.record_message({
                        "type": "assistant",
                        "content": msg.content[:500] if msg.content else "",
                        "model": cm.session_stats.model,
                    })
                    break
        except Exception:
            pass

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

    # ── 退出 & 会话统计 ───────────────────────────────────────────

    def _on_exit(self) -> None:
        """退出时: flush 会话历史 + 渲染统计 + 告别。"""
        cm = self.runtime.context_manager

        # Flush session history to disk
        filepath = cm.flush_session()
        if filepath:
            self.console.print(f"\n  [dim]会话已保存 → {filepath}[/dim]")

        # Render stats
        self._render_session_stats()

        self.console.print("  [dim]再见！[/dim]\n")

    def _render_session_stats(self) -> None:
        """渲染会话统计摘要。"""
        stats = self.runtime.context_manager.session_stats

        # 如果没有任何交互，跳过
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

    # ── REPL 命令 ────────────────────────────────────────────────

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
                from cli.app import VERSION
                self.console.print(f"  [dim]v{VERSION}[/dim]")
            case "/clear":
                self.console.clear()
            case "/new":
                self.thread_id = uuid.uuid4().hex
                self.console.print("  [dim]已开启新会话[/dim]")
            case "/context":
                self._cmd_context(parts[1:])
            case "/memory":
                self._cmd_memory(parts[1:])
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

    # ── /context 命令 ────────────────────────────────────────────

    def _cmd_context(self, args: list[str]) -> None:
        cm = self.runtime.context_manager
        sub = args[0] if args else "show"

        if sub == "show":
            s = cm.stats
            self.console.print()
            self.console.print(f"  [bold]Context 状态[/bold]")
            self.console.print(f"    已加载文件: {s['loaded_files']}")
            self.console.print(f"    记忆条数:   {s['memories_count']}")
            self.console.print(
                f"    全局 context: {s['global_context_tokens']} tokens "
                f"({s['global_context_chars']} chars)"
            )
            self.console.print(
                f"    项目 context: {s['project_context_tokens']} tokens "
                f"({s['project_context_chars']} chars)"
            )
            if cm.loaded_files:
                self.console.print(f"    文件列表:")
                for f in cm.loaded_files:
                    self.console.print(f"      [dim]{f}[/dim]")
            self.console.print()
        elif sub == "reload":
            cm.reload()
            s = cm.stats
            self.console.print(
                f"  [green]✓[/green] 已重新加载 "
                f"({s['loaded_files']} 文件, {s['memories_count']} 条记忆)"
            )
        else:
            self.console.print(f"  [red]未知子命令:[/red] /context {sub}")
            self.console.print("  [dim]用法: /context show | /context reload[/dim]")

    # ── /memory 命令 ─────────────────────────────────────────────

    def _cmd_memory(self, args: list[str]) -> None:
        cm = self.runtime.context_manager
        sub = args[0] if args else "list"

        if sub == "list":
            memories = cm.get_memories()
            if not memories:
                self.console.print("  [dim]暂无记忆。使用 /memory add <fact> 添加。[/dim]")
                return
            self.console.print()
            self.console.print(f"  [bold]Agent 记忆[/bold] ({len(memories)} 条)")
            self.console.print()
            for i, m in enumerate(memories, 1):
                self.console.print(f"    [dim]{i}.[/dim] {m}")
            self.console.print()

        elif sub == "add":
            fact = " ".join(args[1:]).strip() if len(args) > 1 else ""
            if not fact:
                self.console.print("  [red]用法:[/red] /memory add <要记住的内容>")
                return
            cm.save_memory(fact)
            self.console.print(f"  [green]✓[/green] 已保存记忆: {fact}")

        elif sub == "remove":
            if len(args) < 2:
                self.console.print("  [red]用法:[/red] /memory remove <序号>")
                return
            try:
                idx = int(args[1]) - 1  # 用户输入从 1 开始
            except ValueError:
                self.console.print("  [red]序号必须是数字[/red]")
                return
            if cm.remove_memory(idx):
                self.console.print(f"  [green]✓[/green] 已删除第 {idx + 1} 条记忆")
            else:
                self.console.print(f"  [red]✗[/red] 序号 {idx + 1} 不存在")

        else:
            self.console.print(f"  [red]未知子命令:[/red] /memory {sub}")
            self.console.print("  [dim]用法: /memory list | add <fact> | remove <n>[/dim]")

    # ── 渲染 ─────────────────────────────────────────────────────

    def _render_user_input(self, user_input: str) -> None:
        """用灰色背景重新渲染用户输入行，与 LLM 输出区分"""
        sys.stdout.write("\x1b[A\x1b[2K\r")
        sys.stdout.flush()
        line = Text(no_wrap=True)
        content = f"{PROMPT_SYMBOL} {user_input}"
        line.append(
            _ljust_cols(content, self.console.width),
            style=_BG_USER,
        )
        self.console.print(line)

    # ── 输入读取（带命令下拉补全）───────────────────────────────

    def _read_input(self) -> str:
        """构建 prompt_toolkit Application，实现命令下拉菜单。"""
        dropdown: dict = {"items": [], "selected": 0}

        def _refresh(text: str) -> None:
            if text.startswith("/"):
                prefix = text[1:].lower()
                dropdown["items"] = [
                    (cmd, desc) for cmd, desc in _COMMANDS
                    if cmd[1:].startswith(prefix)
                ]
            else:
                dropdown["items"] = []
            dropdown["selected"] = 0

        buf = Buffer(
            name="main",
            history=self._history,
            on_text_changed=lambda b: _refresh(b.text),
        )

        # 提示符前缀：首行显示 ❯，续行对齐缩进
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

        layout = Layout(
            HSplit([
                Window(
                    content=BufferControl(buffer=buf),
                    get_line_prefix=lambda lineno, wc: _pfx if lineno == 0 and wc == 0 else _pfx_wrap,
                    dont_extend_height=True,
                    wrap_lines=True,
                ),
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

    # ── 主循环 ───────────────────────────────────────────────────

    def run(self) -> None:
        while self.running:
            try:
                user_input = self._read_input()
            except EOFError:
                self._on_exit()
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
                if not self._handle_command(stripped):
                    break
                continue

            self._invoke_agent(stripped)


# ── 辅助函数 ─────────────────────────────────────────────────────


def _truncate(val: Any, maxlen: int = 60) -> str:
    s = str(val)
    return s if len(s) <= maxlen else s[:maxlen] + "…"


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