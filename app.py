"""MT-AutoOptimize — 交互式 CLI 入口"""

from __future__ import annotations

import logging
import os

from rich.console import Console

from cli.banner import render_banner
from cli.repl import Repl

VERSION = "0.1.0"


class App:
    """MT-AutoOptimize 交互式 CLI"""

    def __init__(self) -> None:
        self.console = Console()

    def show_welcome(self) -> None:
        self.console.print()
        render_banner(self.console)
        self.console.print()
        self.console.print(f"  [bold]MT-AutoOptimize[/bold]  [dim]v{VERSION}[/dim]")
        self.console.print("  [dim]MT-3000 AI Coding Agent  ·  分析 → 优化 → 编译[/dim]")
        self.console.print()

    def run(self) -> None:
        from core.agent import create_agent_runtime

        self.show_welcome()
        repl: Repl | None = None

        try:
            runtime = create_agent_runtime()
        except Exception as e:
            self.console.print(f"  [red]Agent 初始化失败:[/red] {e}")
            self.console.print("  [dim]请检查 config.json 或环境变量配置[/dim]\n")
            return

        self.console.print(f"  [dim]工作目录  {runtime.workspace}[/dim]")
        self.console.print(f"  [dim]已注册工具  {', '.join(runtime.registry.names)}[/dim]")

        # Context & Memory 加载信息
        cm = runtime.context_manager
        ctx_stats = cm.stats
        ctx_parts = []
        if ctx_stats["loaded_files"] > 0:
            ctx_parts.append(f"{ctx_stats['loaded_files']} 个上下文文件")
        if ctx_stats["memories_count"] > 0:
            ctx_parts.append(f"{ctx_stats['memories_count']} 条记忆")
        if ctx_parts:
            self.console.print(f"  [dim]已加载  {', '.join(ctx_parts)}[/dim]")
        else:
            self.console.print(f"  [dim]提示  编辑 ~/.mtagent/CONTEXT.md 或 ./CONTEXT.md 添加项目指令[/dim]")

        self.console.print()
        self.console.print("  [dim]输入自然语言描述需求，或输入 /help 查看帮助[/dim]")
        self.console.print()

        repl = Repl(self.console, runtime=runtime)
        try:
            repl.run()
        finally:
            repl.close()


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "WARNING").upper(),
        format="%(name)s %(levelname)s: %(message)s",
    )
    app = App()
    try:
        app.run()
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
