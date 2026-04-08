"""tests/test_core/test_context.py — ContextManager 单元测试"""

import json
import os
import time
from pathlib import Path

import pytest

from core.context import ContextManager, MEMORY_SECTION_HEADER
from core.session import SessionStats, SessionRecorder
from core.utils.tokens import estimate_tokens
from config.settings import CONTEXT as DEFAULT_CONFIG


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_workspace(tmp_path):
    """创建一个临时工作目录。"""
    return tmp_path / "project"


@pytest.fixture
def tmp_global_dir(tmp_path):
    """创建一个临时全局目录（替代 ~/.mtagent）。"""
    d = tmp_path / "global"
    d.mkdir()
    return d


@pytest.fixture
def config(tmp_global_dir):
    """构建测试用 config，指向临时目录。"""
    return {
        "file_names": ["CONTEXT.md"],
        "global_dir": str(tmp_global_dir),
        "compression_threshold": 0.50,
        "compression_preserve_ratio": 0.30,
        "token_limit": 65536,
    }


@pytest.fixture
def cm(tmp_workspace, config):
    """创建 ContextManager 实例。"""
    tmp_workspace.mkdir(parents=True, exist_ok=True)
    return ContextManager(working_directory=str(tmp_workspace), config=config)


# ---------------------------------------------------------------------------
# estimate_tokens
# ---------------------------------------------------------------------------

class TestEstimateTokens:
    def test_ascii_text(self):
        # 12 ASCII chars → ~3 tokens
        assert estimate_tokens("hello world!") == 3

    def test_cjk_text(self):
        # 4 CJK chars → ~5 tokens (4 * 1.3 = 5.2 → 5)
        assert estimate_tokens("你好世界") == 5

    def test_mixed_text(self):
        result = estimate_tokens("hello 你好")
        # "hello " = 6 ASCII → 1.5, "你好" = 2 CJK → 2.6, total ≈ 4
        assert result > 0

    def test_empty_string(self):
        assert estimate_tokens("") == 0


# ---------------------------------------------------------------------------
# SessionStats
# ---------------------------------------------------------------------------

class TestSessionStats:
    def test_initial_state(self):
        ss = SessionStats()
        assert ss.total_tokens == 0
        assert ss.turn_count == 0
        assert ss.tool_calls_total == 0

    def test_record_llm_usage(self):
        ss = SessionStats()
        ss.record_llm_usage(100, 50, "deepseek-chat")
        ss.record_llm_usage(200, 80)
        assert ss.total_input_tokens == 300
        assert ss.total_output_tokens == 130
        assert ss.total_tokens == 430
        assert ss.turn_count == 2
        assert ss.model == "deepseek-chat"

    def test_record_tool_call(self):
        ss = SessionStats()
        ss.record_tool_call("read_file", True)
        ss.record_tool_call("read_file", True)
        ss.record_tool_call("write_file", False)
        assert ss.tool_calls_total == 3
        assert ss.tool_calls_success == 2
        assert ss.tool_calls_failed == 1
        assert ss.tool_calls_by_name == {"read_file": 2, "write_file": 1}

    def test_to_dict(self):
        ss = SessionStats()
        ss.record_llm_usage(100, 50, "test-model")
        d = ss.to_dict()
        assert d["model"] == "test-model"
        assert d["tokens"]["input"] == 100
        assert d["tokens"]["output"] == 50
        assert d["tokens"]["total"] == 150
        assert d["turns"] == 1

    def test_duration(self):
        ss = SessionStats()
        ss.start_time = time.time() - 10  # 10 秒前
        assert ss.duration_seconds >= 9.0


# ---------------------------------------------------------------------------
# ContextManager — 加载
# ---------------------------------------------------------------------------

class TestContextManagerLoad:
    def test_load_empty(self, cm, config):
        """无 CONTEXT.md 时正常加载，自动创建全局骨架。"""
        cm.load()
        # ensure_global_setup 自动创建了骨架文件
        global_file = Path(config["global_dir"]) / "CONTEXT.md"
        assert global_file.exists()
        assert "Global Context" in cm.build_system_context()

    def test_load_global_context(self, cm, config):
        """加载全局 CONTEXT.md。"""
        global_dir = Path(config["global_dir"])
        (global_dir / "CONTEXT.md").write_text("全局指令内容", encoding="utf-8")

        cm.load()
        assert "全局指令内容" in cm.build_system_context()
        assert len(cm.loaded_files) == 1

    def test_load_project_context(self, cm, tmp_workspace):
        """加载项目 CONTEXT.md。"""
        (tmp_workspace / "CONTEXT.md").write_text("项目指令内容", encoding="utf-8")

        cm.load()
        session_ctx = cm.build_session_context()
        assert "项目指令内容" in session_ctx

    def test_load_both_tiers(self, cm, config, tmp_workspace):
        """同时加载 Tier 1 和 Tier 2。"""
        global_dir = Path(config["global_dir"])
        (global_dir / "CONTEXT.md").write_text("全局指令", encoding="utf-8")
        (tmp_workspace / "CONTEXT.md").write_text("项目指令", encoding="utf-8")

        cm.load()
        assert "全局指令" in cm.build_system_context()
        assert "项目指令" in cm.build_session_context()
        assert len(cm.loaded_files) == 2

    def test_reload(self, cm, config):
        """reload 重新读取文件。"""
        global_dir = Path(config["global_dir"])
        global_file = global_dir / "CONTEXT.md"

        global_file.write_text("v1", encoding="utf-8")
        cm.load()
        assert "v1" in cm.build_system_context()

        global_file.write_text("v2", encoding="utf-8")
        cm.reload()
        assert "v2" in cm.build_system_context()


# ---------------------------------------------------------------------------
# ContextManager — build_session_context
# ---------------------------------------------------------------------------

class TestBuildSessionContext:
    def test_contains_metadata(self, cm):
        cm.load()
        ctx = cm.build_session_context()
        assert "<session_context>" in ctx
        assert "</session_context>" in ctx
        assert "Today's date:" in ctx
        assert "OS:" in ctx
        assert "Working directory:" in ctx


# ---------------------------------------------------------------------------
# ContextManager — Memory CRUD
# ---------------------------------------------------------------------------

class TestMemoryCRUD:
    def test_save_memory_creates_section(self, cm, config):
        """首次保存 memory 时创建 ## Agent Memories section。"""
        global_dir = Path(config["global_dir"])
        (global_dir / "CONTEXT.md").write_text("全局指令", encoding="utf-8")
        cm.load()

        cm.save_memory("用户偏好 AM 模式")
        memories = cm.get_memories()
        assert len(memories) == 1
        assert "用户偏好 AM 模式" in memories[0]

        # 验证文件内容
        content = (global_dir / "CONTEXT.md").read_text(encoding="utf-8")
        assert MEMORY_SECTION_HEADER in content
        assert "- 用户偏好 AM 模式" in content
        assert "全局指令" in content  # 原有内容不丢失

    def test_save_multiple_memories(self, cm, config):
        """保存多条 memory。"""
        global_dir = Path(config["global_dir"])
        (global_dir / "CONTEXT.md").write_text("", encoding="utf-8")
        cm.load()

        cm.save_memory("fact 1")
        cm.save_memory("fact 2")
        cm.save_memory("fact 3")
        assert len(cm.get_memories()) == 3

    def test_save_memory_to_nonexistent_file(self, cm, config):
        """全局 CONTEXT.md 不存在时自动创建。"""
        cm.load()
        cm.save_memory("new fact")
        assert len(cm.get_memories()) == 1

        content = (Path(config["global_dir"]) / "CONTEXT.md").read_text(encoding="utf-8")
        assert "- new fact" in content

    def test_save_memory_sanitizes_input(self, cm, config):
        """移除换行和前导 dash。"""
        (Path(config["global_dir"]) / "CONTEXT.md").write_text("", encoding="utf-8")
        cm.load()

        cm.save_memory("- multi\nline\nfact")
        memories = cm.get_memories()
        assert len(memories) == 1
        assert "\n" not in memories[0]
        assert not memories[0].startswith("- ")

    def test_save_empty_memory_ignored(self, cm, config):
        """空内容不保存。"""
        (Path(config["global_dir"]) / "CONTEXT.md").write_text("", encoding="utf-8")
        cm.load()
        cm.save_memory("")
        cm.save_memory("   ")
        assert len(cm.get_memories()) == 0

    def test_remove_memory(self, cm, config):
        """按索引删除 memory。"""
        (Path(config["global_dir"]) / "CONTEXT.md").write_text("", encoding="utf-8")
        cm.load()

        cm.save_memory("keep this")
        cm.save_memory("remove this")
        cm.save_memory("keep this too")

        result = cm.remove_memory(1)
        assert result is True
        memories = cm.get_memories()
        assert len(memories) == 2
        assert "remove this" not in memories

    def test_remove_memory_invalid_index(self, cm, config):
        """无效索引返回 False。"""
        (Path(config["global_dir"]) / "CONTEXT.md").write_text("", encoding="utf-8")
        cm.load()
        cm.save_memory("only one")
        assert cm.remove_memory(5) is False
        assert cm.remove_memory(-1) is False

    def test_memory_persists_with_existing_content(self, cm, config):
        """已有内容和其他 section 不被破坏。"""
        global_dir = Path(config["global_dir"])
        (global_dir / "CONTEXT.md").write_text(
            "# My Config\n\nSome instructions\n\n## Other Section\n\nOther content\n",
            encoding="utf-8",
        )
        cm.load()
        cm.save_memory("a fact")

        content = (global_dir / "CONTEXT.md").read_text(encoding="utf-8")
        assert "# My Config" in content
        assert "Some instructions" in content
        assert "## Other Section" in content
        assert "Other content" in content
        assert "- a fact" in content


# ---------------------------------------------------------------------------
# SessionRecorder — 会话历史
# ---------------------------------------------------------------------------

@pytest.fixture
def recorder(tmp_workspace, config):
    """创建 SessionRecorder 实例。"""
    tmp_workspace.mkdir(parents=True, exist_ok=True)
    return SessionRecorder(working_directory=str(tmp_workspace), config=config)


class TestSessionHistory:
    def test_record_and_flush(self, recorder):
        """记录消息并 flush 到磁盘。"""
        recorder.set_thread_id("thread-123")
        recorder.record({"type": "transcript_message", "role": "user", "content": "hello"})
        recorder.record({"type": "transcript_message", "role": "assistant", "content": "hi"})

        filepath = recorder.flush()
        assert filepath is not None
        assert filepath.exists()
        assert filepath.suffix == ".jsonl"

        lines = filepath.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 4  # session_start + 2 messages + session_end

        start = json.loads(lines[0])
        assert start["type"] == "session_start"
        assert "sessionId" in start
        assert start["threadId"] == "thread-123"

        end = json.loads(lines[-1])
        assert end["type"] == "session_end"
        assert "stats" in end
        assert end["threadId"] == "thread-123"

    def test_flush_empty_returns_none(self, recorder):
        """没有记录时返回 None。"""
        assert recorder.flush() is None

    def test_records_have_timestamp(self, recorder):
        """自动添加 timestamp。"""
        recorder.record({"type": "transcript_message", "role": "user", "content": "test"})
        assert "timestamp" in recorder._records[0]


# ---------------------------------------------------------------------------
# ContextManager — stats 属性
# ---------------------------------------------------------------------------

class TestContextManagerStats:
    def test_stats_structure(self, cm, config):
        """stats 返回正确结构。"""
        global_dir = Path(config["global_dir"])
        (global_dir / "CONTEXT.md").write_text("some content", encoding="utf-8")
        cm.load()

        s = cm.stats
        assert "loaded_files" in s
        assert "memories_count" in s
        assert "global_context_tokens" in s
        assert "project_context_tokens" in s
        assert s["loaded_files"] == 1


# ---------------------------------------------------------------------------
# ContextManager — ensure_global_setup
# ---------------------------------------------------------------------------

class TestEnsureGlobalSetup:
    def test_creates_dir_and_file(self, cm, config):
        """首次运行时创建目录和骨架文件。"""
        import shutil
        global_dir = Path(config["global_dir"])
        if global_dir.exists():
            shutil.rmtree(global_dir)

        created = cm.ensure_global_setup()
        assert created is True
        assert (global_dir / "CONTEXT.md").exists()

    def test_idempotent(self, cm, config):
        """已存在时不重复创建。"""
        global_dir = Path(config["global_dir"])
        (global_dir / "CONTEXT.md").write_text("custom content", encoding="utf-8")

        created = cm.ensure_global_setup()
        assert created is False
        # 不覆盖已有内容
        assert (global_dir / "CONTEXT.md").read_text() == "custom content"


# ---------------------------------------------------------------------------
# SaveMemoryTool
# ---------------------------------------------------------------------------

class TestSaveMemoryTool:
    def test_save_via_tool(self, cm, config):
        """通过 SaveMemoryTool 保存记忆。"""
        import asyncio
        from tools.agent_ops.memory import SaveMemoryTool

        (Path(config["global_dir"]) / "CONTEXT.md").write_text("", encoding="utf-8")
        cm.load()

        tool = SaveMemoryTool(save_fn=cm.save_memory)
        result = asyncio.run(tool.execute(fact="用户偏好 AM 模式"))

        assert result.success
        assert "已保存" in result.output
        assert len(cm.get_memories()) == 1

    def test_empty_fact_rejected(self):
        """空 fact 被拒绝。"""
        import asyncio
        from tools.agent_ops.memory import SaveMemoryTool

        tool = SaveMemoryTool(save_fn=lambda f: None)
        result = asyncio.run(tool.execute(fact=""))

        assert not result.success
        assert "不能为空" in result.error

    def test_schema_shape(self):
        """Tool schema 格式正确。"""
        from tools.agent_ops.memory import SaveMemoryTool

        tool = SaveMemoryTool(save_fn=lambda f: None)
        schema = tool.schema
        assert schema["function"]["name"] == "save_memory"
        assert "fact" in schema["function"]["parameters"]["properties"]


# ---------------------------------------------------------------------------
# SessionRecorder — Session 列表和加载
# ---------------------------------------------------------------------------

class TestSessionListAndLoad:
    def test_list_sessions_empty(self, recorder):
        """无历史会话时返回空列表。"""
        assert recorder.list_sessions() == []

    def test_list_sessions_after_flush(self, recorder):
        """flush 后能列出该会话。"""
        recorder.set_thread_id("thread-1")
        recorder.record({"type": "transcript_message", "role": "user", "content": "hello"})
        recorder.record({"type": "transcript_message", "role": "assistant", "content": "hi there"})
        recorder.flush()

        sessions = recorder.list_sessions()
        assert len(sessions) == 1
        assert sessions[0]["first_user_message"] == "hello"
        assert sessions[0]["message_count"] == 2
        assert sessions[0]["session_id"] == recorder.stats.session_id
        assert sessions[0]["thread_id"] == "thread-1"

    def test_list_sessions_sorted_descending(self, tmp_workspace, config):
        """多个会话按时间倒序排列。"""
        tmp_workspace.mkdir(parents=True, exist_ok=True)

        r1 = SessionRecorder(working_directory=str(tmp_workspace), config=config)
        r1.set_thread_id("thread-a")
        r1.record({"type": "transcript_message", "role": "user", "content": "first session"})
        r1.flush()

        import time as _time
        _time.sleep(0.05)

        r2 = SessionRecorder(working_directory=str(tmp_workspace), config=config)
        r2.set_thread_id("thread-b")
        r2.record({"type": "transcript_message", "role": "user", "content": "second session"})
        r2.flush()

        sessions = r2.list_sessions()
        assert len(sessions) == 2
        assert sessions[0]["first_user_message"] == "second session"
        assert sessions[1]["first_user_message"] == "first session"

    def test_load_session(self, recorder):
        """加载会话返回正确的消息记录。"""
        recorder.record({"type": "transcript_message", "role": "user", "content": "what is 1+1"})
        recorder.record({"type": "transcript_message", "role": "assistant", "content": "2"})
        recorder.record({"type": "tool_call", "toolName": "calc", "status": "success"})
        filepath = recorder.flush()

        records = recorder.load_session(filepath)
        assert len(records) == 3
        assert records[0]["type"] == "transcript_message"
        assert records[1]["type"] == "transcript_message"
        assert records[2]["type"] == "tool_call"

    def test_load_session_skips_metadata(self, recorder):
        """加载时跳过 session_start 和 session_end 记录。"""
        recorder.record({"type": "transcript_message", "role": "user", "content": "hi"})
        filepath = recorder.flush()

        records = recorder.load_session(filepath)
        types = [r["type"] for r in records]
        assert "session_start" not in types
        assert "session_end" not in types

    def test_list_sessions_skips_empty(self, recorder):
        """没有用户消息的会话不出现在列表中。"""
        recorder.record({"type": "transcript_message", "role": "assistant", "content": "hello"})
        recorder.flush()

        sessions = recorder.list_sessions()
        assert len(sessions) == 0

    def test_resume_merges_and_deletes_old(self, tmp_workspace, config):
        """resume 后 flush 应合并旧消息并删除旧文件。"""
        tmp_workspace.mkdir(parents=True, exist_ok=True)

        # 第一次会话: [A, B]
        r1 = SessionRecorder(working_directory=str(tmp_workspace), config=config)
        r1.record({"type": "transcript_message", "role": "user", "content": "A"})
        r1.record({"type": "transcript_message", "role": "assistant", "content": "B"})
        old_path = r1.flush()
        assert old_path.exists()

        # 第二次会话: resume 后新增 [C, D]
        r2 = SessionRecorder(working_directory=str(tmp_workspace), config=config)
        r2._resumed_from = old_path
        r2.record({"type": "transcript_message", "role": "user", "content": "C"})
        r2.record({"type": "transcript_message", "role": "assistant", "content": "D"})

        import time as _time
        _time.sleep(0.05)

        new_path = r2.flush()

        assert not old_path.exists()

        records = r2.load_session(new_path)
        assert len(records) == 4
        displays = [r.get("content") for r in records]
        assert displays == ["A", "B", "C", "D"]

        sessions = r2.list_sessions()
        assert len(sessions) == 1

    def test_build_resume_messages_uses_last_compression_snapshot(self, recorder):
        """resume 只恢复最后一条 compression 摘要及其后的消息。"""
        recorder.record({"type": "transcript_message", "role": "user", "content": "A"})
        recorder.record({"type": "transcript_message", "role": "assistant", "content": "B"})
        recorder.record({"type": "compression", "summary": "S1"})
        recorder.record({"type": "transcript_message", "role": "user", "content": "C"})
        recorder.record({"type": "transcript_message", "role": "assistant", "content": "D"})
        recorder.record({"type": "compression", "summary": "S2"})
        recorder.record({"type": "transcript_message", "role": "user", "content": "E"})
        recorder.record({"type": "transcript_message", "role": "assistant", "content": "F"})
        filepath = recorder.flush()

        messages = recorder.build_resume_messages(filepath)

        assert len(messages) == 3
        assert "conversation_history_summary" in messages[0].content
        assert "S2" in messages[0].content
        assert messages[1].content == "E"
        assert messages[2].content == "F"

    def test_build_resume_messages_prefers_canonical_transcript(self, recorder):
        """若存在 canonical transcript，应恢复 assistant tool_calls 和 ToolMessage。"""
        recorder.record({
            "type": "transcript_message",
            "role": "user",
            "content": "read file",
        })
        recorder.record({
            "type": "transcript_message",
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "name": "read_file",
                "args": {"path": "a.py"},
                "id": "call_1",
                "type": "tool_call",
            }],
        })
        recorder.record({
            "type": "transcript_message",
            "role": "tool",
            "content": "file content",
            "tool_call_id": "call_1",
            "name": "read_file",
        })
        filepath = recorder.flush()

        messages = recorder.build_resume_messages(filepath)

        assert len(messages) == 3
        assert messages[0].content == "read file"
        assert messages[1].tool_calls[0]["name"] == "read_file"
        assert messages[2].tool_call_id == "call_1"
        assert messages[2].content == "file content"

    def test_estimate_messages_tokens_returns_positive_value(self, recorder):
        """估算 resume 消息 token 数，用于初始 context 占比。"""
        from langchain_core.messages import AIMessage, HumanMessage

        tokens = recorder.estimate_messages_tokens([
            HumanMessage(content="hello"),
            AIMessage(content="world"),
        ])

        assert tokens > 0
