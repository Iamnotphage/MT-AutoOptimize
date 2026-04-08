from pathlib import Path
from types import SimpleNamespace

from rich.console import Console

from cli.commands import resume as resume_mod
from core.session import SessionRecorder


class _FakeGraph:
    def __init__(self, snapshot):
        self._snapshot = snapshot
        self.update_called = False
        self.update_args = None

    def get_state(self, config):
        return self._snapshot

    def update_state(self, config, values, as_node=None, task_id=None):
        self.update_called = True
        self.update_args = {
            "config": config,
            "values": values,
            "as_node": as_node,
            "task_id": task_id,
        }
        merged = dict(getattr(self._snapshot, "values", {}) or {})
        merged.update(values or {})
        self._snapshot = SimpleNamespace(values=merged, next=())


def _make_recorder(tmp_path: Path) -> tuple[SessionRecorder, Path]:
    config = {
        "file_names": ["CONTEXT.md"],
        "global_dir": str(tmp_path / "global"),
        "compression_threshold": 0.50,
        "compression_preserve_ratio": 0.30,
        "token_limit": 65536,
    }
    workspace = tmp_path / "project"
    workspace.mkdir(parents=True, exist_ok=True)
    recorder = SessionRecorder(working_directory=str(workspace), config=config)
    recorder.set_thread_id("thread-restore")
    recorder.record({"type": "transcript_message", "role": "user", "content": "hello"})
    recorder.record({"type": "transcript_message", "role": "assistant", "content": "world"})
    filepath = recorder.flush()
    return recorder, filepath


def test_cmd_resume_restores_existing_checkpoint(monkeypatch, tmp_path):
    recorder, filepath = _make_recorder(tmp_path)
    console = Console(record=True, width=100)
    graph = _FakeGraph(SimpleNamespace(
        values={"message": recorder.build_resume_messages(filepath)},
        next=(),
    ))

    monkeypatch.setattr(resume_mod, "_session_picker", lambda sessions: sessions[0])
    monkeypatch.setattr(resume_mod, "_render_resumed_history", lambda console, records: None)

    thread_id = resume_mod.cmd_resume(console, recorder, graph)

    assert thread_id == "thread-restore"
    assert graph.update_called is False
    assert recorder._resumed_from == filepath
    assert recorder.stats.last_input_tokens > 0


def test_cmd_resume_requires_persisted_checkpoint(monkeypatch, tmp_path):
    recorder, _filepath = _make_recorder(tmp_path)
    console = Console(record=True, width=100)
    graph = _FakeGraph(SimpleNamespace(values={}, next=()))

    monkeypatch.setattr(resume_mod, "_session_picker", lambda sessions: sessions[0])
    monkeypatch.setattr(resume_mod, "_render_resumed_history", lambda console, records: None)

    thread_id = resume_mod.cmd_resume(console, recorder, graph)

    assert thread_id is None


def test_cmd_resume_marks_interrupted_tool_execution(monkeypatch, tmp_path):
    recorder, filepath = _make_recorder(tmp_path)
    console = Console(record=True, width=100)
    graph = _FakeGraph(SimpleNamespace(
        values={
            "message": recorder.build_resume_messages(filepath),
            "pending_tool_calls": [{
                "call_id": "call_1",
                "tool_name": "read_file",
                "arguments": {"path": "a.py"},
                "status": "pending",
                "result": None,
                "error_msg": None,
            }],
        },
        next=("tool_execution",),
    ))

    monkeypatch.setattr(resume_mod, "_session_picker", lambda sessions: sessions[0])
    monkeypatch.setattr(resume_mod, "_render_resumed_history", lambda console, records: None)

    thread_id = resume_mod.cmd_resume(console, recorder, graph)

    assert thread_id == "thread-restore"
    assert graph.update_called is True
    assert graph.update_args["as_node"] == "observation"
    assert graph.update_args["values"]["pending_tool_calls"] == []
    assert graph.update_args["values"]["should_continue"] is False
    assert len(graph.update_args["values"]["message"]) == 1


def test_cmd_resume_rejects_inconsistent_awaiting_approval(monkeypatch, tmp_path):
    recorder, filepath = _make_recorder(tmp_path)
    console = Console(record=True, width=100)
    graph = _FakeGraph(SimpleNamespace(
        values={
            "message": recorder.build_resume_messages(filepath),
            "pending_tool_calls": [{
                "call_id": "call_1",
                "tool_name": "write_file",
                "arguments": {"file_path": "a.py"},
                "status": "awaiting_approval",
                "result": None,
                "error_msg": None,
            }],
        },
        next=("human_approval",),
        tasks=(),
    ))

    monkeypatch.setattr(resume_mod, "_session_picker", lambda sessions: sessions[0])
    monkeypatch.setattr(resume_mod, "_render_resumed_history", lambda console, records: None)

    thread_id = resume_mod.cmd_resume(console, recorder, graph)

    assert thread_id is None


def test_cmd_resume_allows_reapproval_when_interrupt_present(monkeypatch, tmp_path):
    recorder, filepath = _make_recorder(tmp_path)
    console = Console(record=True, width=100)
    graph = _FakeGraph(SimpleNamespace(
        values={
            "message": recorder.build_resume_messages(filepath),
            "pending_tool_calls": [{
                "call_id": "call_1",
                "tool_name": "write_file",
                "arguments": {"file_path": "a.py"},
                "status": "awaiting_approval",
                "result": None,
                "error_msg": None,
            }],
        },
        next=("human_approval",),
        tasks=[SimpleNamespace(
            interrupts=[SimpleNamespace(value=[{
                "call_id": "call_1",
                "tool_name": "write_file",
                "arguments": {"file_path": "a.py"},
                "risk_level": "medium",
            }])]
        )],
    ))

    monkeypatch.setattr(resume_mod, "_session_picker", lambda sessions: sessions[0])
    monkeypatch.setattr(resume_mod, "_render_resumed_history", lambda console, records: None)

    thread_id = resume_mod.cmd_resume(console, recorder, graph)

    assert thread_id == "thread-restore"


def test_cmd_resume_warns_when_checkpoint_and_transcript_diverge(monkeypatch, tmp_path):
    recorder, filepath = _make_recorder(tmp_path)
    console = Console(record=True, width=100)
    graph = _FakeGraph(SimpleNamespace(
        values={
            "message": recorder.build_resume_messages(filepath) + recorder.build_resume_messages(filepath),
        },
        next=(),
        tasks=(),
    ))

    monkeypatch.setattr(resume_mod, "_session_picker", lambda sessions: sessions[0])
    monkeypatch.setattr(resume_mod, "_render_resumed_history", lambda console, records: None)

    thread_id = resume_mod.cmd_resume(console, recorder, graph)

    assert thread_id == "thread-restore"
    assert "历史长度不一致" in console.export_text()
