"""
Context & Memory 管理器

职责:
  - 分层加载 CONTEXT.md（全局 Tier 1 → 项目 Tier 2）
  - 管理持久化 Memory（全局 CONTEXT.md 的 ## Agent Memories 区域）
  - 构建注入 system prompt / session_context 的上下文字符串
  - 维护内部缓存，避免重复 IO

设计原则（参考 gemini-cli）:
  - ContextManager 位于 Core 层，不依赖 CLI 或 Tools
  - 缓存在自身实例中，不放入 AgentState（不参与 checkpoint 序列化）
  - Context（用户手写指令）与 Memory（Agent 生成 facts）严格分离
  - Memory 存储在全局 CONTEXT.md 的专用 section 中（人类可编辑）
  - 分层注入: Tier 1 → system instruction, Tier 2 → session_context
"""

from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from core.utils.tokens import estimate_tokens

logger = logging.getLogger(__name__)

# gemini-cli 风格: Agent 记忆写入的 section 标题
MEMORY_SECTION_HEADER = "## Agent Memories"


class ContextManager:
    """
    Context & Memory 的统一管理入口。

    Usage::

        from config.settings import CONTEXT
        cm = ContextManager(working_directory="/path/to/project", config=CONTEXT)
        cm.load()
        system_ctx = cm.build_system_context()       # Tier 1 → system instruction
        session_ctx = cm.build_session_context()     # Tier 2 → 首条消息
        cm.save_memory("用户偏好 AM 模式")
    """

    def __init__(self, working_directory: str, config: dict[str, Any]) -> None:
        self._working_dir = Path(working_directory).resolve()
        self._config = config

        # 内部缓存
        self._global_context: str = ""
        self._project_context: str = ""
        self._loaded_files: list[str] = []
        self._last_loaded_at: float = 0.0

    # ------------------------------------------------------------------
    # 公开 API — 加载
    # ------------------------------------------------------------------

    def ensure_global_setup(self) -> bool:
        """
        确保全局目录和 CONTEXT.md 骨架存在。

        首次运行时自动创建 ~/.mtagent/ 和空的 CONTEXT.md。
        返回 True 表示是首次创建。
        """
        global_path = self._get_global_context_path()
        created = False

        if not global_path.parent.exists():
            global_path.parent.mkdir(parents=True, exist_ok=True)
            created = True

        if not global_path.exists():
            global_path.write_text(
                "# Global Context\n\n"
                "<!-- 在此编写全局指令，所有项目共享。Agent 每次启动时自动加载。 -->\n"
                "<!-- 例如: 编码规范、常用工具链配置、个人偏好等 -->\n",
                encoding="utf-8",
            )
            created = True

        return created

    def load(self) -> None:
        """启动时一次性加载全部 context 和 memory。"""
        self.ensure_global_setup()
        self._loaded_files.clear()
        self._global_context = self._load_global_context()
        self._project_context = self._load_project_context()
        self._last_loaded_at = time.time()

        logger.info(
            "ContextManager loaded: %d files, %d memories",
            len(self._loaded_files),
            len(self.get_memories()),
        )

    def reload(self) -> None:
        """强制刷新（供 /context reload 命令调用）。"""
        self.load()

    # ------------------------------------------------------------------
    # 公开 API — 构建 Prompt 上下文
    # ------------------------------------------------------------------

    def build_system_context(self) -> str:
        """
        构建 Tier 1 上下文，注入到 system instruction 中。

        包含: 全局 CONTEXT.md 内容（含 Agent Memories）。
        """
        return self._global_context

    def build_session_context(self) -> str:
        """
        构建 Tier 2 上下文，注入到首条 user message 的 <session_context> 中。

        包含: 日期、OS、工作目录、项目 CONTEXT.md 内容。
        """
        parts = [
            f"Today's date: {datetime.now().strftime('%Y-%m-%d')}",
            f"OS: {os.uname().sysname.lower()}",
            f"Working directory: {self._working_dir}",
        ]
        if self._project_context:
            parts.append(f"Project context:\n{self._project_context}")

        return "<session_context>\n" + "\n".join(parts) + "\n</session_context>"

    # ------------------------------------------------------------------
    # 公开 API — Memory CRUD
    # ------------------------------------------------------------------

    def save_memory(self, fact: str) -> None:
        """
        持久化一条 fact 到全局 CONTEXT.md 的 ## Agent Memories 区域。

        参考 gemini-cli 的 memoryTool.ts: 追加为 Markdown 列表项。
        """
        sanitized = re.sub(r"[\r\n]+", " ", fact).strip().lstrip("- ")
        if not sanitized:
            return

        global_path = self._get_global_context_path()
        global_path.parent.mkdir(parents=True, exist_ok=True)

        content = self._read_file_safe(global_path)
        new_content = self._append_memory_to_content(content, sanitized)

        global_path.write_text(new_content, encoding="utf-8")
        self._global_context = new_content
        logger.info("Saved memory: %s", sanitized[:60])

    def get_memories(self) -> list[str]:
        """从全局 CONTEXT.md 中解析出 Agent Memories 列表。"""
        return self._parse_memories(self._global_context)

    def remove_memory(self, index: int) -> bool:
        """按索引删除一条 memory（从 0 开始）。"""
        memories = self.get_memories()
        if index < 0 or index >= len(memories):
            return False

        memories.pop(index)
        self._rewrite_memories(memories)
        return True

    # ------------------------------------------------------------------
    # 公开 API — 状态查询
    # ------------------------------------------------------------------

    @property
    def loaded_files(self) -> list[str]:
        """已加载的文件路径列表（调试用）。"""
        return list(self._loaded_files)

    @property
    def stats(self) -> dict[str, Any]:
        """当前加载状态摘要。"""
        return {
            "loaded_files": len(self._loaded_files),
            "memories_count": len(self.get_memories()),
            "global_context_chars": len(self._global_context),
            "project_context_chars": len(self._project_context),
            "global_context_tokens": estimate_tokens(self._global_context),
            "project_context_tokens": estimate_tokens(self._project_context),
            "last_loaded_at": self._last_loaded_at,
        }

    # ------------------------------------------------------------------
    # 内部实现 — 文件加载
    # ------------------------------------------------------------------

    def _load_global_context(self) -> str:
        filepath = self._get_global_context_path()
        content = self._read_file_safe(filepath)
        if content:
            self._loaded_files.append(str(filepath))
        return content

    def _load_project_context(self) -> str:
        file_names: list[str] = self._config.get("file_names", ["CONTEXT.md"])
        parts: list[str] = []
        for name in file_names:
            filepath = self._working_dir / name
            content = self._read_file_safe(filepath)
            if content:
                parts.append(content)
                self._loaded_files.append(str(filepath))
        return "\n\n".join(parts)

    # ------------------------------------------------------------------
    # 内部实现 — Memory 解析与写入
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_memories(content: str) -> list[str]:
        if MEMORY_SECTION_HEADER not in content:
            return []
        idx = content.index(MEMORY_SECTION_HEADER)
        section_content = content[idx + len(MEMORY_SECTION_HEADER):]
        next_section = re.search(r"\n## ", section_content)
        if next_section:
            section_content = section_content[:next_section.start()]
        memories = []
        for line in section_content.splitlines():
            line = line.strip()
            if line.startswith("- "):
                memories.append(line[2:].strip())
        return memories

    @staticmethod
    def _append_memory_to_content(content: str, fact: str) -> str:
        new_item = f"- {fact}"
        if MEMORY_SECTION_HEADER not in content:
            separator = "\n\n" if content.strip() else ""
            return content.rstrip() + separator + f"{MEMORY_SECTION_HEADER}\n\n{new_item}\n"
        idx = content.index(MEMORY_SECTION_HEADER)
        after_header = content[idx + len(MEMORY_SECTION_HEADER):]
        next_section = re.search(r"\n## ", after_header)
        if next_section:
            insert_pos = idx + len(MEMORY_SECTION_HEADER) + next_section.start()
            return content[:insert_pos].rstrip() + "\n" + new_item + "\n" + content[insert_pos:]
        else:
            return content.rstrip() + "\n" + new_item + "\n"

    def _rewrite_memories(self, memories: list[str]) -> None:
        global_path = self._get_global_context_path()
        content = self._read_file_safe(global_path)
        if MEMORY_SECTION_HEADER not in content:
            return
        idx = content.index(MEMORY_SECTION_HEADER)
        after_header = content[idx + len(MEMORY_SECTION_HEADER):]
        next_section = re.search(r"\n## ", after_header)
        if next_section:
            before = content[:idx + len(MEMORY_SECTION_HEADER)]
            after = content[idx + len(MEMORY_SECTION_HEADER) + next_section.start():]
        else:
            before = content[:idx + len(MEMORY_SECTION_HEADER)]
            after = ""
        if memories:
            items = "\n".join(f"- {m}" for m in memories)
            new_section = f"\n\n{items}\n"
        else:
            new_section = "\n"
        new_content = before + new_section + after
        global_path.write_text(new_content, encoding="utf-8")
        self._global_context = new_content

    # ------------------------------------------------------------------
    # 内部实现 — 路径辅助
    # ------------------------------------------------------------------

    def _get_global_context_path(self) -> Path:
        global_dir = Path(self._config.get("global_dir", "~/.mtagent")).expanduser()
        file_names = self._config.get("file_names", ["CONTEXT.md"])
        return global_dir / file_names[0]

    # ------------------------------------------------------------------
    # 内部实现 — IO 辅助
    # ------------------------------------------------------------------

    @staticmethod
    def _read_file_safe(filepath: Path) -> str:
        try:
            if filepath.is_file():
                return filepath.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError) as e:
            logger.warning("Failed to read %s: %s", filepath, e)
        return ""
