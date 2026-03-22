from unittest.mock import MagicMock

from langchain_core.messages import AIMessageChunk, HumanMessage

from core.event_bus import EventType
from core.nodes.reasoning import create_reasoning_node, should_use_tools


class TestReasoningNode:
    """reasoning 节点测试"""

    def test_pure_text_response(self, event_bus, mock_llm_text):
        """LLM 返回纯文本 → pending_tool_calls 为空"""
        node = create_reasoning_node(mock_llm_text, event_bus)
        state = {
            "message": [HumanMessage(content="你好")],
            "turn_count": 0,
        }

        result = node(state)

        assert result["turn_count"] == 1
        assert result["pending_tool_calls"] == []
        assert "你好" in result["message"][0].content

    def test_tool_call_response(self, event_bus, mock_llm_tool_call):
        """LLM 返回 tool_calls → pending_tool_calls 非空"""
        node = create_reasoning_node(mock_llm_tool_call, event_bus)
        state = {
            "message": [HumanMessage(content="读取 test.c")],
            "turn_count": 0,
        }

        result = node(state)

        assert result["turn_count"] == 1
        assert len(result["pending_tool_calls"]) == 1
        assert result["pending_tool_calls"][0]["tool_name"] == "read_file"
        assert result["pending_tool_calls"][0]["status"] == "pending"

    def test_turn_count_increments(self, event_bus, mock_llm_text):
        """turn_count 从任意值递增"""
        node = create_reasoning_node(mock_llm_text, event_bus)
        state = {"message": [HumanMessage(content="hi")], "turn_count": 5}

        result = node(state)
        assert result["turn_count"] == 6

    def test_content_events_emitted(self, event_bus, mock_llm_text):
        """流式过程中发送了 CONTENT 事件"""
        received = []
        event_bus.subscribe_all(lambda e: received.append(e))

        node = create_reasoning_node(mock_llm_text, event_bus)
        state = {"message": [HumanMessage(content="hi")], "turn_count": 0}
        node(state)

        content_events = [e for e in received if e.type == EventType.CONTENT]
        assert len(content_events) == 2
        assert content_events[0].data["text"] == "你好"
        assert content_events[1].data["text"] == "，我是 Agent"

    def test_tool_call_request_event(self, event_bus, mock_llm_tool_call):
        """tool_calls 触发 TOOL_CALL_REQUEST 事件"""
        received = []
        event_bus.subscribe(EventType.TOOL_CALL_REQUEST, lambda e: received.append(e))

        node = create_reasoning_node(mock_llm_tool_call, event_bus)
        state = {"message": [HumanMessage(content="hi")], "turn_count": 0}
        node(state)

        assert len(received) == 1
        assert received[0].data["tool_name"] == "read_file"
        assert received[0].data["call_id"] == "call_123"

    def test_turn_start_event(self, event_bus, mock_llm_text):
        """每轮结束后发送 TURN_START 事件"""
        received = []
        event_bus.subscribe(EventType.TURN_START, lambda e: received.append(e))

        node = create_reasoning_node(mock_llm_text, event_bus)
        state = {"message": [HumanMessage(content="hi")], "turn_count": 0}
        node(state)

        assert len(received) == 1
        assert received[0].data["turn"] == 1

    def test_llm_error_handled(self, event_bus):
        """LLM 调用失败 → 返回错误消息, 不抛异常"""
        llm = MagicMock()
        llm.bind_tools.return_value = llm
        llm.stream.side_effect = Exception("API timeout")

        node = create_reasoning_node(llm, event_bus)
        state = {"message": [HumanMessage(content="hi")], "turn_count": 0}

        result = node(state)

        assert result["pending_tool_calls"] == []
        assert result["turn_count"] == 1
        assert "LLM" in result["message"][0].content or "无响应" in result["message"][0].content

    def test_error_event_on_failure(self, event_bus):
        """LLM 失败时发送 ERROR 事件"""
        received = []
        event_bus.subscribe(EventType.ERROR, lambda e: received.append(e))

        llm = MagicMock()
        llm.bind_tools.return_value = llm
        llm.stream.side_effect = RuntimeError("connection refused")

        node = create_reasoning_node(llm, event_bus)
        state = {"message": [HumanMessage(content="hi")], "turn_count": 0}
        node(state)

        assert len(received) == 1
        assert "connection refused" in received[0].data["error"]

    def test_no_tool_schemas_skips_bind(self, event_bus):
        """tool_schemas=None 时不调用 bind_tools"""
        llm = MagicMock()
        llm.stream.return_value = iter([AIMessageChunk(content="ok")])

        create_reasoning_node(llm, event_bus, tool_schemas=None)

        llm.bind_tools.assert_not_called()

    def test_with_tool_schemas_calls_bind(self, event_bus):
        """传入 tool_schemas 时调用 bind_tools"""
        llm = MagicMock()
        llm.bind_tools.return_value = llm
        schemas = [{"type": "function", "function": {"name": "test", "parameters": {}}}]

        create_reasoning_node(llm, event_bus, tool_schemas=schemas)

        llm.bind_tools.assert_called_once_with(schemas)


class TestShouldUseTools:
    """条件路由函数测试"""

    def test_has_tools(self):
        state = {"pending_tool_calls": [{"tool_name": "read_file"}]}
        assert should_use_tools(state) == "use_tools"

    def test_empty_tools(self):
        assert should_use_tools({"pending_tool_calls": []}) == "final_answer"

    def test_missing_key(self):
        assert should_use_tools({}) == "final_answer"
