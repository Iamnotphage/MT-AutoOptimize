from core.event_bus import EventBus, EventType
from core.nodes.tool_routing import (
    create_tool_routing_node,
    needs_approval,
)
from tools.policy import DEFAULT_TOOL_RISK


def _make_tc(tool_name: str, call_id: str = "call_1") -> dict:
    """构造一个最小 ToolCallInfo dict"""
    return {
        "call_id": call_id,
        "tool_name": tool_name,
        "arguments": {},
        "status": "pending",
        "result": None,
        "error_msg": None,
    }


class TestToolRoutingNode:

    def test_low_risk_auto_approved(self, event_bus):
        """LOW 风险工具 → 保持 pending (自动放行)"""
        node = create_tool_routing_node(event_bus)
        state = {"pending_tool_calls": [_make_tc("read_file")], "turn_count": 1}

        result = node(state)

        assert result["pending_tool_calls"][0]["status"] == "pending"
        assert result["needs_human_approval"] is False
        assert result["approval_requests"] == []

    def test_medium_risk_needs_approval(self, event_bus):
        """MEDIUM 风险工具 → awaiting_approval"""
        node = create_tool_routing_node(event_bus)
        state = {"pending_tool_calls": [_make_tc("write_file")], "turn_count": 1}

        result = node(state)

        assert result["pending_tool_calls"][0]["status"] == "awaiting_approval"
        assert result["needs_human_approval"] is True
        assert len(result["approval_requests"]) == 1
        assert result["approval_requests"][0]["risk_level"] == "medium"

    def test_high_risk_needs_approval(self, event_bus):
        """HIGH 风险工具 → awaiting_approval"""
        node = create_tool_routing_node(event_bus)
        state = {"pending_tool_calls": [_make_tc("run_command")], "turn_count": 1}

        result = node(state)

        assert result["pending_tool_calls"][0]["status"] == "awaiting_approval"
        assert result["needs_human_approval"] is True
        assert result["approval_requests"][0]["risk_level"] == "high"

    def test_unknown_tool_defaults_medium(self, event_bus):
        """未注册工具 → 默认 medium → awaiting_approval"""
        node = create_tool_routing_node(event_bus)
        state = {"pending_tool_calls": [_make_tc("some_unknown_tool")], "turn_count": 0}

        result = node(state)

        assert result["pending_tool_calls"][0]["status"] == "awaiting_approval"
        assert result["needs_human_approval"] is True

    def test_mixed_risk_levels(self, event_bus):
        """混合风险: low + high → 只有 high 需要审批"""
        node = create_tool_routing_node(event_bus)
        state = {
            "pending_tool_calls": [
                _make_tc("read_file", "call_1"),
                _make_tc("run_command", "call_2"),
            ],
            "turn_count": 0,
        }

        result = node(state)

        assert result["pending_tool_calls"][0]["status"] == "pending"
        assert result["pending_tool_calls"][1]["status"] == "awaiting_approval"
        assert result["needs_human_approval"] is True
        assert len(result["approval_requests"]) == 1
        assert result["approval_requests"][0]["call_id"] == "call_2"

    def test_all_low_no_approval(self, event_bus):
        """全部 LOW → 无需审批"""
        node = create_tool_routing_node(event_bus)
        state = {
            "pending_tool_calls": [
                _make_tc("read_file", "call_1"),
                _make_tc("glob", "call_2"),
            ],
            "turn_count": 0,
        }

        result = node(state)

        assert result["needs_human_approval"] is False
        assert all(tc["status"] == "pending" for tc in result["pending_tool_calls"])

    def test_empty_pending(self, event_bus):
        """空 pending_tool_calls → 无操作"""
        node = create_tool_routing_node(event_bus)
        state = {"pending_tool_calls": [], "turn_count": 0}

        result = node(state)

        assert result["pending_tool_calls"] == []
        assert result["needs_human_approval"] is False

    def test_custom_risk_map_override(self, event_bus):
        """自定义 risk_map 覆盖默认映射"""
        node = create_tool_routing_node(
            event_bus,
            tool_risk_map={"read_file": "high"},  # 把 read_file 提升为 high
        )
        state = {"pending_tool_calls": [_make_tc("read_file")], "turn_count": 0}

        result = node(state)

        assert result["pending_tool_calls"][0]["status"] == "awaiting_approval"
        assert result["approval_requests"][0]["risk_level"] == "high"

    def test_approval_request_events(self, event_bus):
        """需要审批时发送 APPROVAL_REQUEST 事件"""
        received = []
        event_bus.subscribe(EventType.APPROVAL_REQUEST, lambda e: received.append(e))

        node = create_tool_routing_node(event_bus)
        state = {"pending_tool_calls": [_make_tc("ssh_command")], "turn_count": 3}

        node(state)

        assert len(received) == 1
        assert received[0].data["tool_name"] == "ssh_command"
        assert received[0].turn == 3

    def test_no_event_for_low_risk(self, event_bus):
        """LOW 风险不发送 APPROVAL_REQUEST 事件"""
        received = []
        event_bus.subscribe(EventType.APPROVAL_REQUEST, lambda e: received.append(e))

        node = create_tool_routing_node(event_bus)
        state = {"pending_tool_calls": [_make_tc("read_file")], "turn_count": 0}

        node(state)

        assert received == []


class TestNeedsApproval:

    def test_needs_approval_true(self):
        assert needs_approval({"needs_human_approval": True}) == "needs_approval"

    def test_needs_approval_false(self):
        assert needs_approval({"needs_human_approval": False}) == "approved"

    def test_missing_key(self):
        assert needs_approval({}) == "approved"
