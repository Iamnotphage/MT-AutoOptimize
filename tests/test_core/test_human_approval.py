from unittest.mock import patch

from core.event_bus import EventType
from core.nodes.human_approval import create_human_approval_node, _parse_response


def _make_tc(tool_name: str, call_id: str, status: str = "awaiting_approval") -> dict:
    return {
        "call_id": call_id,
        "tool_name": tool_name,
        "arguments": {},
        "status": status,
        "result": None,
        "error_msg": None,
    }


class TestHumanApprovalNode:

    @patch(
        "core.nodes.human_approval.interrupt",
        return_value={"call_1": True, "call_2": True},
    )
    def test_approve_all(self, _mock_interrupt, event_bus):
        """逐条全部放行 → 所有 awaiting_approval 变为 pending"""
        node = create_human_approval_node(event_bus)
        state = {
            "pending_tool_calls": [
                _make_tc("write_file", "call_1"),
                _make_tc("run_command", "call_2"),
            ],
            "approval_requests": [
                {"call_id": "call_1", "tool_name": "write_file"},
                {"call_id": "call_2", "tool_name": "run_command"},
            ],
            "turn_count": 1,
        }

        result = node(state)

        assert all(tc["status"] == "pending" for tc in result["pending_tool_calls"])
        assert result["needs_human_approval"] is False
        assert result["approval_requests"] == []

    @patch(
        "core.nodes.human_approval.interrupt",
        return_value={"call_1": False},
    )
    def test_deny(self, _mock_interrupt, event_bus):
        """拒绝 → awaiting_approval 变为 cancelled"""
        node = create_human_approval_node(event_bus)
        state = {
            "pending_tool_calls": [_make_tc("run_command", "call_1")],
            "approval_requests": [{"call_id": "call_1", "tool_name": "run_command"}],
            "turn_count": 1,
        }

        result = node(state)

        assert result["pending_tool_calls"][0]["status"] == "cancelled"

    @patch(
        "core.nodes.human_approval.interrupt",
        return_value={"call_1": True, "call_2": False},
    )
    def test_mixed_decisions(self, _mock_interrupt, event_bus):
        """逐条决策: call_1 放行, call_2 拒绝"""
        node = create_human_approval_node(event_bus)
        state = {
            "pending_tool_calls": [
                _make_tc("write_file", "call_1"),
                _make_tc("ssh_command", "call_2"),
            ],
            "approval_requests": [
                {"call_id": "call_1", "tool_name": "write_file"},
                {"call_id": "call_2", "tool_name": "ssh_command"},
            ],
            "turn_count": 1,
        }

        result = node(state)

        statuses = {tc["call_id"]: tc["status"] for tc in result["pending_tool_calls"]}
        assert statuses["call_1"] == "pending"
        assert statuses["call_2"] == "cancelled"

    @patch(
        "core.nodes.human_approval.interrupt",
        return_value={"call_2": True},
    )
    def test_preserves_non_awaiting_calls(self, _mock_interrupt, event_bus):
        """已经是 pending 的工具调用不受影响"""
        node = create_human_approval_node(event_bus)
        state = {
            "pending_tool_calls": [
                _make_tc("read_file", "call_1", status="pending"),
                _make_tc("run_command", "call_2", status="awaiting_approval"),
            ],
            "approval_requests": [{"call_id": "call_2", "tool_name": "run_command"}],
            "turn_count": 1,
        }

        result = node(state)

        statuses = {tc["call_id"]: tc["status"] for tc in result["pending_tool_calls"]}
        assert statuses["call_1"] == "pending"
        assert statuses["call_2"] == "pending"

    @patch(
        "core.nodes.human_approval.interrupt",
        return_value={"call_1": True},
    )
    def test_approval_response_event(self, _mock_interrupt, event_bus):
        """发送 APPROVAL_RESPONSE 事件"""
        received = []
        event_bus.subscribe(EventType.APPROVAL_RESPONSE, lambda e: received.append(e))

        node = create_human_approval_node(event_bus)
        state = {
            "pending_tool_calls": [_make_tc("write_file", "call_1")],
            "approval_requests": [{"call_id": "call_1", "tool_name": "write_file"}],
            "turn_count": 2,
        }

        node(state)

        assert len(received) == 1
        assert received[0].data["decisions"]["call_1"] is True
        assert received[0].turn == 2

    def test_empty_requests_noop(self, event_bus):
        """无审批请求 → 直接返回空 dict"""
        node = create_human_approval_node(event_bus)
        state = {"approval_requests": [], "turn_count": 0}

        result = node(state)

        assert result == {}

    @patch("core.nodes.human_approval.interrupt", return_value="garbage")
    def test_invalid_response_denies_all(self, _mock_interrupt, event_bus):
        """非 dict 响应 → 兜底拒绝"""
        node = create_human_approval_node(event_bus)
        state = {
            "pending_tool_calls": [_make_tc("run_command", "call_1")],
            "approval_requests": [{"call_id": "call_1", "tool_name": "run_command"}],
            "turn_count": 0,
        }

        result = node(state)

        assert result["pending_tool_calls"][0]["status"] == "cancelled"

    @patch(
        "core.nodes.human_approval.interrupt",
        return_value={},
    )
    def test_missing_call_id_denies(self, _mock_interrupt, event_bus):
        """响应中缺少 call_id → 该工具视为拒绝"""
        node = create_human_approval_node(event_bus)
        state = {
            "pending_tool_calls": [_make_tc("run_command", "call_1")],
            "approval_requests": [{"call_id": "call_1", "tool_name": "run_command"}],
            "turn_count": 0,
        }

        result = node(state)

        assert result["pending_tool_calls"][0]["status"] == "cancelled"


class TestParseResponse:

    def test_approve(self):
        reqs = [{"call_id": "a"}, {"call_id": "b"}]
        assert _parse_response({"a": True, "b": True}, reqs) == {"a": True, "b": True}

    def test_deny(self):
        reqs = [{"call_id": "a"}]
        assert _parse_response({"a": False}, reqs) == {"a": False}

    def test_mixed(self):
        reqs = [{"call_id": "a"}, {"call_id": "b"}]
        assert _parse_response({"a": True, "b": False}, reqs) == {"a": True, "b": False}

    def test_non_dict_fallback(self):
        reqs = [{"call_id": "a"}]
        assert _parse_response("invalid", reqs) == {"a": False}
