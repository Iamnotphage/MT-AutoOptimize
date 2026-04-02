"""
Context & Memory 管理器

职责:
  - 分层加载 CONTEXT.md（全局 Tier 1 → 项目 Tier 2）
  - 管理持久化 Memory（全局 CONTEXT.md 的 ## Agent Memories 区域）
  - 构建注入 system prompt / session_context 的上下文字符串
  - 会话统计收集（token、duration、tool calls）
  - 会话历史持久化（JSONL）
  - 维护内部缓存，避免重复 IO

设计原则（参考 gemini-cli）:
  - ContextManager 位于 Core 层，不依赖 CLI 或 Tools
  - 缓存在自身实例中，不放入 AgentState（不参与 checkpoint 序列化）
  - Context（用户手写指令）与 Memory（Agent 生成 facts）严格分离
  - Memory 存储在全局 CONTEXT.md 的专用 section 中（人类可编辑）
  - 分层注入: Tier 1 → system instruction, Tier 2 → session_context
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# gemini-cli 风格: Agent 记忆写入的 section 标题
MEMORY_SECTION_HEADER = "## Agent Memories"


# ---------------------------------------------------------------------------
# Token 估算 (参考 gemini-cli tokenCalculation.ts)
# ---------------------------------------------------------------------------

def estimate_tokens(text: str) -> int:
    """
    启发式 token 估算，不引入 tokenizer 依赖。

    - ASCII: ~4 字符/token
    - 非 ASCII (CJK): ~1.3 token/字符
    """
    ascii_chars = sum(1 for c in text if ord(c) < 128)
    non_ascii_chars = len(text) - ascii_chars
    return int(ascii_chars / 4 + non_ascii_chars * 1.3)


# ---------------------------------------------------------------------------
# Session Stats (参考 gemini-cli SessionMetrics)
# ---------------------------------------------------------------------------

@dataclass
class SessionStats:
    """会话期间的实时统计，退出时渲染到 CLI。"""

    session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    start_time: float = field(default_factory=time.time)
    model: str = ""

    # Token 统计
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_tokens: int = 0

    # 轮次统计
    turn_count: int = 0
    prompt_count: int = 0

    # 工具统计
    tool_calls_total: int = 0
    tool_calls_success: int = 0
    tool_calls_failed: int = 0
    tool_calls_by_name: dict[str, int] = field(default_factory=dict)

    @property
    def duration_seconds(self) -> float:
        return time.time() - self.start_time

    def record_llm_usage(self, input_tokens: int, output_tokens: int, model: str = "") -> None:
        """记录一次 LLM 调用的 token 用量。"""
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.total_tokens += input_tokens + output_tokens
        self.turn_count += 1
        if model:
            self.model = model

    def record_tool_call(self, tool_name: str, success: bool) -> None:
        """记录一次工具调用。"""
        self.tool_calls_total += 1
        if success:
            self.tool_calls_success += 1
        else:
            self.tool_calls_failed += 1
        self.tool_calls_by_name[tool_name] = self.tool_calls_by_name.get(tool_name, 0) + 1

    def to_dict(self) -> dict[str, Any]:
        """序列化为 dict（用于 session_end 记录）。"""
        return {
            "session_id": self.session_id,
            "model": self.model,
            "duration_ms": int(self.duration_seconds * 1000),
            "turns": self.turn_count,
            "prompts": self.prompt_count,
            "tokens": {
                "input": self.total_input_tokens,
                "output": self.total_output_tokens,
                "total": self.total_tokens,
            },
            "tools": {
                "total": self.tool_calls_total,
                "success": self.tool_calls_success,
                "failed": self.tool_calls_failed,
                "by_name": dict(self.tool_calls_by_name),
            },
        }


# ---------------------------------------------------------------------------
# ContextManager
# ---------------------------------------------------------------------------

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
        self._global_context: str = ""        # 全局 CONTEXT.md 全文（含 Memories）
        self._project_context: str = ""       # 项目 CONTEXT.md
        self._loaded_files: list[str] = []
        self._last_loaded_at: float = 0.0

        # Session
        self.session_stats = SessionStats()
        self._session_records: list[dict] = []

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
        # 清理: 移除换行、首尾空白、开头的 dash
        sanitized = re.sub(r"[\r\n]+", " ", fact).strip().lstrip("- ")
        if not sanitized:
            return

        global_path = self._get_global_context_path()
        global_path.parent.mkdir(parents=True, exist_ok=True)

        content = self._read_file_safe(global_path)
        new_content = self._append_memory_to_content(content, sanitized)

        global_path.write_text(new_content, encoding="utf-8")

        # 刷新缓存
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
    # 公开 API — Session History 持久化
    # ------------------------------------------------------------------

    def record_message(self, record: dict) -> None:
        """记录一条会话消息（追加到内存缓冲区）。"""
        if "timestamp" not in record:
            record["timestamp"] = int(time.time() * 1000)
        self._session_records.append(record)

    def flush_session(self) -> Path | None:
        """
        将会话记录写入磁盘 JSONL 文件。

        在会话结束（REPL quit）时调用。
        返回写入的文件路径，如果无记录则返回 None。
        """
        if not self._session_records:
            return None

        # session_start 记录
        start_record = {
            "type": "session_start",
            "sessionId": self.session_stats.session_id,
            "project": str(self._working_dir),
            "model": self.session_stats.model,
            "timestamp": int(self.session_stats.start_time * 1000),
        }

        # session_end 记录
        end_record = {
            "type": "session_end",
            "sessionId": self.session_stats.session_id,
            "stats": self.session_stats.to_dict(),
            "timestamp": int(time.time() * 1000),
        }

        # 写入文件
        history_dir = self._get_history_dir()
        history_dir.mkdir(parents=True, exist_ok=True)

        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        sid = self.session_stats.session_id
        filepath = history_dir / f"session-{ts}-{sid}.jsonl"

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(json.dumps(start_record, ensure_ascii=False) + "\n")
            for record in self._session_records:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            f.write(json.dumps(end_record, ensure_ascii=False) + "\n")

        logger.info("Session history saved to %s (%d records)", filepath, len(self._session_records))
        return filepath

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
        """加载全局 CONTEXT.md（~/.mtagent/CONTEXT.md）。"""
        filepath = self._get_global_context_path()
        content = self._read_file_safe(filepath)
        if content:
            self._loaded_files.append(str(filepath))
        return content

    def _load_project_context(self) -> str:
        """加载项目根目录的 CONTEXT.md。"""
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
        """从 CONTEXT.md 内容中解析 ## Agent Memories 下的列表项。"""
        if MEMORY_SECTION_HEADER not in content:
            return []

        # 找到 section 开始位置
        idx = content.index(MEMORY_SECTION_HEADER)
        section_content = content[idx + len(MEMORY_SECTION_HEADER):]

        # 截取到下一个 ## 或文件末尾
        next_section = re.search(r"\n## ", section_content)
        if next_section:
            section_content = section_content[:next_section.start()]

        # 解析列表项
        memories = []
        for line in section_content.splitlines():
            line = line.strip()
            if line.startswith("- "):
                memories.append(line[2:].strip())

        return memories

    @staticmethod
    def _append_memory_to_content(content: str, fact: str) -> str:
        """将 fact 追加到 content 的 ## Agent Memories 区域。"""
        new_item = f"- {fact}"

        if MEMORY_SECTION_HEADER not in content:
            # 文件中没有该 section，在末尾创建
            separator = "\n\n" if content.strip() else ""
            return content.rstrip() + separator + f"{MEMORY_SECTION_HEADER}\n\n{new_item}\n"

        # 找到 section 末尾（下一个 ## 或文件末尾），在其前面插入
        idx = content.index(MEMORY_SECTION_HEADER)
        after_header = content[idx + len(MEMORY_SECTION_HEADER):]

        next_section = re.search(r"\n## ", after_header)
        if next_section:
            insert_pos = idx + len(MEMORY_SECTION_HEADER) + next_section.start()
            return content[:insert_pos].rstrip() + "\n" + new_item + "\n" + content[insert_pos:]
        else:
            return content.rstrip() + "\n" + new_item + "\n"

    def _rewrite_memories(self, memories: list[str]) -> None:
        """用新的 memories 列表重写全局 CONTEXT.md 的 ## Agent Memories 区域。"""
        global_path = self._get_global_context_path()
        content = self._read_file_safe(global_path)

        if MEMORY_SECTION_HEADER not in content:
            return

        # 找到 section 范围
        idx = content.index(MEMORY_SECTION_HEADER)
        after_header = content[idx + len(MEMORY_SECTION_HEADER):]

        next_section = re.search(r"\n## ", after_header)
        if next_section:
            before = content[:idx + len(MEMORY_SECTION_HEADER)]
            after = content[idx + len(MEMORY_SECTION_HEADER) + next_section.start():]
        else:
            before = content[:idx + len(MEMORY_SECTION_HEADER)]
            after = ""

        # 重建 section
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
        """全局 CONTEXT.md 路径。"""
        global_dir = Path(self._config.get("global_dir", "~/.mtagent")).expanduser()
        # 取 file_names 中的第一个作为全局文件名
        file_names = self._config.get("file_names", ["CONTEXT.md"])
        return global_dir / file_names[0]

    def _get_history_dir(self) -> Path:
        """会话历史目录: ~/.mtagent/history/{projectHash}/"""
        global_dir = Path(self._config.get("global_dir", "~/.mtagent")).expanduser()
        project_hash = hashlib.md5(str(self._working_dir).encode()).hexdigest()[:10]
        return global_dir / "history" / project_hash

    # ------------------------------------------------------------------
    # 内部实现 — IO 辅助
    # ------------------------------------------------------------------

    @staticmethod
    def _read_file_safe(filepath: Path) -> str:
        """安全读取文件，不存在时返回空字符串。"""
        try:
            if filepath.is_file():
                return filepath.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError) as e:
            logger.warning("Failed to read %s: %s", filepath, e)
        return ""
