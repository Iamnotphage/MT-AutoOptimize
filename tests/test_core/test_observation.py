from langchain_core.messages import ToolMessage

from core.event_bus import EventType
from core.nodes.observation import (
    create_observation_node,
    should_continue_loop,
    _build_tool_messages,
)


def _make_tc(
    call_id: str = "call_1",
    tool_name: str = "read_file",
    status: str = "success",
    result: str | None = "file content",
    error_msg: str | None = None,
) -> dict:
    return {
        "call_id": call_id,
        "tool_name": tool_name,
        "arguments": {},
        "status": status,
        "result": result,
        "error_msg": error_msg,
    }


class TestObservationNode:

    def test_success_to_tool_message(self, event_bus):
        """success 的工具结果转为 ToolMessage"""
        node = create_observation_node(event_bus)
        state = {
            "completed_tool_calls": [_make_tc(result="hello world")],
            "turn_count": 1,
            "max_turns": 25,
        }

        result = node(state)

        assert len(result["message"]) == 1
        msg = result["message"][0]
        assert isinstance(msg, ToolMessage)
        assert msg.content == "hello world"
        assert msg.tool_call_id == "call_1"

    def test_error_to_tool_message(self, event_bus):
        """error 的工具结果包含错误信息"""
        node = create_observation_node(event_bus)
        state = {
            "completed_tool_calls": [
                _make_tc(status="error", result=None, error_msg="file not found"),
            ],
            "turn_count": 1,
            "max_turns": 25,
        }

        result = node(state)

        assert "file not found" in result["message"][0].content

    def test_cancelled_to_tool_message(self, event_bus):
        """cancelled 的工具调用生成拒绝提示"""
        node = create_observation_node(event_bus)
        state = {
            "completed_tool_calls": [_make_tc(status="cancelled", result=None)],
            "turn_count": 1,
            "max_turns": 25,
        }

        result = node(state)

        assert "拒绝" in result["message"][0].content

    def test_multiple_results(self, event_bus):
        """多个工具结果全部转为 ToolMessage"""
        node = create_observation_node(event_bus)
        state = {
            "completed_tool_calls": [
                _make_tc("call_1", result="r1"),
                _make_tc("call_2", result="r2"),
            ],
            "turn_count": 1,
            "max_turns": 25,
        }

        result = node(state)

        assert len(result["message"]) == 2

    def test_clears_tool_calls(self, event_bus):
        """执行后清空 pending 和 completed"""
        node = create_observation_node(event_bus)
        state = {
            "completed_tool_calls": [_make_tc()],
            "turn_count": 1,
            "max_turns": 25,
        }

        result = node(state)

        assert result["pending_tool_calls"] == []
        assert result["completed_tool_calls"] == []

    def test_should_continue_within_max(self, event_bus):
        """未超 max_turns → should_continue=True"""
        node = create_observation_node(event_bus)
        state = {
            "completed_tool_calls": [_make_tc()],
            "turn_count": 3,
            "max_turns": 25,
        }

        result = node(state)

        assert result["should_continue"] is True

    def test_should_stop_at_max_turns(self, event_bus):
        """达到 max_turns → should_continue=False"""
        node = create_observation_node(event_bus)
        state = {
            "completed_tool_calls": [_make_tc()],
            "turn_count": 10,
            "max_turns": 10,
        }

        result = node(state)

        assert result["should_continue"] is False

    def test_default_max_turns(self, event_bus):
        """未设置 max_turns → 使用默认值 25"""
        node = create_observation_node(event_bus)
        state = {
            "completed_tool_calls": [_make_tc()],
            "turn_count": 5,
        }

        result = node(state)

        assert result["should_continue"] is True

    def test_turn_end_event(self, event_bus):
        """发送 TURN_END 事件"""
        received = []
        event_bus.subscribe(EventType.TURN_END, lambda e: received.append(e))

        node = create_observation_node(event_bus)
        state = {
            "completed_tool_calls": [_make_tc()],
            "turn_count": 2,
            "max_turns": 25,
        }

        node(state)

        assert len(received) == 1
        assert received[0].data["turn"] == 2
        assert received[0].data["tool_count"] == 1
        assert received[0].data["should_continue"] is True

    def test_empty_completed(self, event_bus):
        """空 completed_tool_calls → 无 ToolMessage"""
        node = create_observation_node(event_bus)
        state = {
            "completed_tool_calls": [],
            "turn_count": 1,
            "max_turns": 25,
        }

        result = node(state)

        assert result["message"] == []


class TestShouldContinueLoop:

    def test_continue(self):
        assert should_continue_loop({"should_continue": True}) == "continue"

    def test_stop(self):
        assert should_continue_loop({"should_continue": False}) == "final_answer"

    def test_missing_key(self):
        assert should_continue_loop({}) == "final_answer"


class TestBuildToolMessages:

    def test_success(self):
        msgs = _build_tool_messages([_make_tc(result="ok")])
        assert len(msgs) == 1
        assert msgs[0].content == "ok"

    def test_error(self):
        msgs = _build_tool_messages([_make_tc(status="error", error_msg="boom")])
        assert "boom" in msgs[0].content

    def test_cancelled(self):
        msgs = _build_tool_messages([_make_tc(status="cancelled")])
        assert "拒绝" in msgs[0].content
