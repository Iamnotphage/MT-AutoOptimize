"""
会话管理 — 统计、录制、持久化、列表、加载

职责:
  - SessionStats: 会话期间实时统计（token、轮次、工具调用）
  - SessionRecorder: 消息录制、JSONL 持久化、历史会话列表与加载

设计原则:
  - 位于 Core 层，不依赖 CLI 或 Tools
  - 与 ContextManager 解耦，各自独立初始化
"""

from __future__ import annotations

import hashlib
import json
import logging
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from core.compressor import ContextCompressor
from core.utils.tokens import estimate_tokens

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def format_relative_time(timestamp_ms: int) -> str:
    """将毫秒时间戳转为相对时间描述（如 '2 hours ago'）。"""
    if not timestamp_ms:
        return "unknown"
    diff = time.time() - timestamp_ms / 1000
    if diff < 60:
        return "just now"
    if diff < 3600:
        m = int(diff / 60)
        return f"{m} min{'s' if m > 1 else ''} ago"
    if diff < 86400:
        h = int(diff / 3600)
        return f"{h} hour{'s' if h > 1 else ''} ago"
    d = int(diff / 86400)
    if d == 1:
        return "1 day ago"
    return f"{d} days ago"


def format_file_size(size_bytes: int) -> str:
    """格式化文件大小。"""
    if size_bytes < 1024:
        return f"{size_bytes}B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f}KB"
    return f"{size_bytes / (1024 * 1024):.1f}MB"


# ---------------------------------------------------------------------------
# SessionStats
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
    last_input_tokens: int = 0  # 最近一次 LLM 调用的 input tokens（即上下文占用）

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
        self.last_input_tokens = input_tokens
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
# SessionRecorder
# ---------------------------------------------------------------------------

# 可渲染的记录类型（load_session 时过滤用）
_RENDERABLE_TYPES = {"user", "assistant", "thought", "tool_request", "tool_complete", "tool_diff", "tool_call"}


class SessionRecorder:
    """
    会话录制与历史管理。

    Usage::

        recorder = SessionRecorder(working_directory="/path/to/project", config=CONTEXT_CONFIG)
        recorder.record({"type": "user", "display": "hello"})
        recorder.flush()  # 退出时写入 JSONL
        sessions = recorder.list_sessions()
    """

    def __init__(self, working_directory: str, config: dict[str, Any]) -> None:
        self._working_dir = Path(working_directory).resolve()
        self._config = config

        self.stats = SessionStats()
        self._records: list[dict] = []
        self._resumed_from: Path | None = None

    # ------------------------------------------------------------------
    # 录制
    # ------------------------------------------------------------------

    def record(self, record: dict) -> None:
        """记录一条会话消息（追加到内存缓冲区）。"""
        if "timestamp" not in record:
            record["timestamp"] = int(time.time() * 1000)
        self._records.append(record)

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------

    def flush(self) -> Path | None:
        """
        将会话记录写入磁盘 JSONL 文件。

        如果是 resume 的会话，合并旧消息并删除旧文件。
        返回写入的文件路径，如果无记录则返回 None。
        """
        if not self._records:
            return None

        # resume: 合并旧消息
        all_records: list[dict] = []
        if self._resumed_from and self._resumed_from.is_file():
            all_records.extend(self.load_raw_session(self._resumed_from))
        all_records.extend(self._records)

        start_record = {
            "type": "session_start",
            "sessionId": self.stats.session_id,
            "project": str(self._working_dir),
            "model": self.stats.model,
            "branch": self._get_git_branch(),
            "timestamp": int(self.stats.start_time * 1000),
        }

        end_record = {
            "type": "session_end",
            "sessionId": self.stats.session_id,
            "stats": self.stats.to_dict(),
            "timestamp": int(time.time() * 1000),
        }

        history_dir = self._get_history_dir()
        history_dir.mkdir(parents=True, exist_ok=True)

        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        sid = self.stats.session_id
        filepath = history_dir / f"session-{ts}-{sid}.jsonl"

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(json.dumps(start_record, ensure_ascii=False) + "\n")
            for rec in all_records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.write(json.dumps(end_record, ensure_ascii=False) + "\n")

        # 删除旧文件
        if self._resumed_from and self._resumed_from.is_file() and self._resumed_from != filepath:
            try:
                self._resumed_from.unlink()
                logger.info("Deleted old session file: %s", self._resumed_from)
            except OSError as e:
                logger.warning("Failed to delete old session file: %s", e)

        logger.info("Session saved to %s (%d records)", filepath, len(self._records))
        return filepath

    # ------------------------------------------------------------------
    # 历史查询
    # ------------------------------------------------------------------

    def list_sessions(self) -> list[dict[str, Any]]:
        """列出当前项目的所有历史会话（按时间倒序）。"""
        history_dir = self._get_history_dir()
        if not history_dir.is_dir():
            return []

        sessions: list[dict[str, Any]] = []
        for filepath in sorted(
            history_dir.glob("session-*.jsonl"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        ):
            try:
                info = self._parse_session_file(filepath)
                if info:
                    sessions.append(info)
            except Exception as e:
                logger.warning("Skipping corrupt session file %s: %s", filepath, e)
        return sessions

    def load_session(self, filepath: Path) -> list[dict]:
        """加载指定会话文件的渲染记录（不含 session_start/session_end）。"""
        return [record for record in self.load_raw_session(filepath) if record.get("type") in _RENDERABLE_TYPES]

    def load_raw_session(self, filepath: Path) -> list[dict]:
        """加载指定会话文件中的全部业务记录（不含 session_start/session_end）。"""
        records: list[dict] = []
        for line in filepath.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                if record.get("type") not in {"session_start", "session_end"}:
                    records.append(record)
            except json.JSONDecodeError:
                continue
        return records

    def build_resume_messages(self, filepath: Path) -> list[BaseMessage]:
        """从会话文件重建 resume 所需消息，只保留最后一次压缩摘要及其后的消息。"""
        records = self.load_raw_session(filepath)

        last_compression_idx = -1
        summary_text = ""
        for idx, record in enumerate(records):
            if record.get("type") == "compression":
                last_compression_idx = idx
                summary_text = str(record.get("summary", "")).strip()

        resumed: list[BaseMessage] = []
        if summary_text:
            resumed.append(ContextCompressor.build_summary_message(summary_text))

        start_idx = last_compression_idx + 1 if last_compression_idx >= 0 else 0
        for record in records[start_idx:]:
            rtype = record.get("type")
            if rtype == "user":
                content = record.get("display", record.get("content", ""))
                resumed.append(HumanMessage(content=content))
            elif rtype == "assistant":
                content = record.get("content", "")
                resumed.append(AIMessage(content=content))

        return resumed

    def estimate_messages_tokens(self, messages: list[BaseMessage]) -> int:
        """估算一组消息的 token 数，用于 resume 后上下文占比展示。"""
        total = 0
        for msg in messages:
            role = getattr(msg, "type", "")
            content = msg.content
            if isinstance(content, list):
                content = str(content)
            total += estimate_tokens(f"[{role}] {content}")
        return total

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_session_file(filepath: Path) -> dict[str, Any] | None:
        """解析 JSONL 会话文件，提取摘要信息。"""
        session_id = ""
        model = ""
        branch = ""
        timestamp = 0
        first_user_msg = ""
        message_count = 0

        for line in filepath.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue

            rtype = record.get("type", "")
            if rtype == "session_start":
                session_id = record.get("sessionId", "")
                model = record.get("model", "")
                branch = record.get("branch", "")
                timestamp = record.get("timestamp", 0)
            elif rtype == "user":
                message_count += 1
                if not first_user_msg:
                    display = record.get("display", record.get("content", ""))
                    first_user_msg = display[:80]
            elif rtype == "assistant":
                message_count += 1

        if not first_user_msg:
            return None

        try:
            file_size = filepath.stat().st_size
        except OSError:
            file_size = 0

        return {
            "session_id": session_id,
            "model": model,
            "branch": branch,
            "timestamp": timestamp,
            "first_user_message": first_user_msg,
            "message_count": message_count,
            "file_size": file_size,
            "filepath": filepath,
        }

    def _get_history_dir(self) -> Path:
        """会话历史目录: ~/.mtagent/history/{projectHash}/"""
        global_dir = Path(self._config.get("global_dir", "~/.mtagent")).expanduser()
        project_hash = hashlib.md5(str(self._working_dir).encode()).hexdigest()[:10]
        return global_dir / "history" / project_hash

    def _get_git_branch(self) -> str:
        """获取当前 git 分支名。"""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=self._working_dir,
                capture_output=True, text=True, timeout=3,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            pass
        return ""
