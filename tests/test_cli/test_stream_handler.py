from rich.console import Console

from cli.event_handlers.stream import StreamHandler
from core.event_bus import AgentEvent, EventBus, EventType
from core.session import SessionRecorder


def _make_session(tmp_path):
    config = {
        "file_names": ["CONTEXT.md"],
        "global_dir": str(tmp_path / "global"),
        "compression_threshold": 0.50,
        "compression_preserve_ratio": 0.30,
        "token_limit": 65536,
    }
    workspace = tmp_path / "project"
    workspace.mkdir(parents=True, exist_ok=True)
    return SessionRecorder(working_directory=str(workspace), config=config)


def test_stream_handler_records_approval_events(tmp_path):
    session = _make_session(tmp_path)
    bus = EventBus()
    StreamHandler(Console(record=True, width=100), bus, session)

    bus.emit(AgentEvent(
        type=EventType.APPROVAL_REQUEST,
        data={
            "call_id": "call_1",
            "tool_name": "write_file",
            "arguments": {"file_path": "a.py"},
            "risk_level": "medium",
        },
    ))
    bus.emit(AgentEvent(
        type=EventType.APPROVAL_RESPONSE,
        data={"decisions": {"call_1": True}},
    ))

    assert session._records[0]["type"] == "approval_request"
    assert session._records[0]["tool_name"] == "write_file"
    assert session._records[1]["type"] == "approval_decision"
    assert session._records[1]["decisions"]["call_1"] is True
