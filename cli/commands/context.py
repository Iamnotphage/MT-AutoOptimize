"""/context 命令处理"""

from __future__ import annotations

from rich.console import Console

from core.context import ContextManager


def cmd_context(console: Console, cm: ContextManager, args: list[str]) -> None:
    sub = args[0] if args else "show"

    if sub == "show":
        s = cm.stats
        console.print()
        console.print("  [bold]Context 状态[/bold]")
        console.print(f"    已加载文件: {s['loaded_files']}")
        console.print(f"    记忆条数:   {s['memories_count']}")
        console.print(
            f"    全局 context: {s['global_context_tokens']} tokens "
            f"({s['global_context_chars']} chars)"
        )
        console.print(
            f"    项目 context: {s['project_context_tokens']} tokens "
            f"({s['project_context_chars']} chars)"
        )
        if cm.loaded_files:
            console.print("    文件列表:")
            for f in cm.loaded_files:
                console.print(f"      [dim]{f}[/dim]")
        console.print()
    elif sub == "reload":
        cm.reload()
        s = cm.stats
        console.print(
            f"  [green]✓[/green] 已重新加载 "
            f"({s['loaded_files']} 文件, {s['memories_count']} 条记忆)"
        )
    else:
        console.print(f"  [red]未知子命令:[/red] /context {sub}")
        console.print("  [dim]用法: /context show | /context reload[/dim]")
