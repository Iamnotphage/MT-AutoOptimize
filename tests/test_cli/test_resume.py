from pathlib import Path
from types import SimpleNamespace

from rich.console import Console

from cli.commands import resume as resume_mod
from core.session import SessionRecorder


class _FakeGraph:
    def __init__(self, snapshot):
        self._snapshot = snapshot
        self.update_called = False

    def get_state(self, config):
        return self._snapshot

    def update_state(self, config, values):
        self.update_called = True


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
