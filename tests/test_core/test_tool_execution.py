from unittest.mock import MagicMock

from core.event_bus import EventType
from core.nodes.tool_execution import create_tool_execution_node


def _make_tc(
    tool_name: str,
    call_id: str = "call_1",
    status: str = "pending",
    arguments: dict | None = None,
) -> dict:
    return {
        "call_id": call_id,
        "tool_name": tool_name,
        "arguments": arguments or {},
        "status": status,
        "result": None,
        "error_msg": None,
    }


class TestToolExecutionNode:

    def test_success(self, event_bus):
        """工具执行成功 → status=success, result 有值"""
        executor = MagicMock(return_value="file content here")
        node = create_tool_execution_node(event_bus, executor)
        state = {
            "pending_tool_calls": [_make_tc("read_file", arguments={"path": "a.c"})],
            "turn_count": 1,
        }

        result = node(state)

        assert result["pending_tool_calls"] == []
        assert len(result["completed_tool_calls"]) == 1
        tc = result["completed_tool_calls"][0]
        assert tc["status"] == "success"
        assert tc["result"] == "file content here"
        executor.assert_called_once_with("read_file", {"path": "a.c"})

    def test_error(self, event_bus):
        """工具执行异常 → status=error, error_msg 有值"""
        executor = MagicMock(side_effect=FileNotFoundError("not found"))
        node = create_tool_execution_node(event_bus, executor)
        state = {
            "pending_tool_calls": [_make_tc("read_file")],
            "turn_count": 1,
        }

        result = node(state)

        tc = result["completed_tool_calls"][0]
        assert tc["status"] == "error"
        assert "not found" in tc["error_msg"]

    def test_skips_cancelled(self, event_bus):
        """cancelled 的工具调用直接跳过, 不执行"""
        executor = MagicMock()
        node = create_tool_execution_node(event_bus, executor)
        state = {
            "pending_tool_calls": [_make_tc("run_command", status="cancelled")],
            "turn_count": 0,
        }

        result = node(state)

        executor.assert_not_called()
        assert result["completed_tool_calls"][0]["status"] == "cancelled"

    def test_multiple_tools(self, event_bus):
        """多个工具顺序执行"""
        executor = MagicMock(side_effect=["result_1", "result_2"])
        node = create_tool_execution_node(event_bus, executor)
        state = {
            "pending_tool_calls": [
                _make_tc("read_file", "call_1"),
                _make_tc("glob_search", "call_2"),
            ],
            "turn_count": 0,
        }

        result = node(state)

        assert len(result["completed_tool_calls"]) == 2
        assert result["completed_tool_calls"][0]["result"] == "result_1"
        assert result["completed_tool_calls"][1]["result"] == "result_2"
        assert executor.call_count == 2

    def test_mixed_success_and_error(self, event_bus):
        """一个成功一个失败"""
        executor = MagicMock(side_effect=["ok", RuntimeError("boom")])
        node = create_tool_execution_node(event_bus, executor)
        state = {
            "pending_tool_calls": [
                _make_tc("read_file", "call_1"),
                _make_tc("run_command", "call_2"),
            ],
            "turn_count": 0,
        }

        result = node(state)

        statuses = {tc["call_id"]: tc["status"] for tc in result["completed_tool_calls"]}
        assert statuses["call_1"] == "success"
        assert statuses["call_2"] == "error"

    def test_mixed_pending_and_cancelled(self, event_bus):
        """pending 执行, cancelled 跳过"""
        executor = MagicMock(return_value="done")
        node = create_tool_execution_node(event_bus, executor)
        state = {
            "pending_tool_calls": [
                _make_tc("read_file", "call_1", status="pending"),
                _make_tc("ssh_command", "call_2", status="cancelled"),
            ],
            "turn_count": 0,
        }

        result = node(state)

        executor.assert_called_once()
        statuses = {tc["call_id"]: tc["status"] for tc in result["completed_tool_calls"]}
        assert statuses["call_1"] == "success"
        assert statuses["call_2"] == "cancelled"

    def test_clears_pending(self, event_bus):
        """执行后 pending_tool_calls 清空"""
        executor = MagicMock(return_value="ok")
        node = create_tool_execution_node(event_bus, executor)
        state = {
            "pending_tool_calls": [_make_tc("read_file")],
            "turn_count": 0,
        }

        result = node(state)

        assert result["pending_tool_calls"] == []

    def test_empty_pending(self, event_bus):
        """空列表 → 无执行"""
        executor = MagicMock()
        node = create_tool_execution_node(event_bus, executor)
        state = {"pending_tool_calls": [], "turn_count": 0}

        result = node(state)

        executor.assert_not_called()
        assert result["completed_tool_calls"] == []

    def test_state_update_events(self, event_bus):
        """执行过程发送 TOOL_STATE_UPDATE → TOOL_CALL_COMPLETE → ALL_TOOLS_COMPLETE"""
        received = []
        event_bus.subscribe_all(lambda e: received.append(e))

        executor = MagicMock(return_value="ok")
        node = create_tool_execution_node(event_bus, executor)
        state = {
            "pending_tool_calls": [_make_tc("read_file")],
            "turn_count": 1,
        }

        node(state)

        types = [e.type for e in received]
        assert EventType.TOOL_STATE_UPDATE in types
        assert EventType.TOOL_CALL_COMPLETE in types
        assert EventType.ALL_TOOLS_COMPLETE in types
        # 顺序: STATE_UPDATE 在 CALL_COMPLETE 之前
        idx_update = types.index(EventType.TOOL_STATE_UPDATE)
        idx_complete = types.index(EventType.TOOL_CALL_COMPLETE)
        idx_all = types.index(EventType.ALL_TOOLS_COMPLETE)
        assert idx_update < idx_complete < idx_all

