import pytest
from unittest.mock import MagicMock

from langchain_core.messages import AIMessageChunk

from core.event_bus import EventBus


@pytest.fixture
def event_bus():
    """每个测试独立的 EventBus 实例"""
    return EventBus()


@pytest.fixture
def mock_llm_text():
    """模拟 LLM 返回纯文本 (无 tool_calls)"""
    llm = MagicMock()
    chunks = [
        AIMessageChunk(content="你好"),
        AIMessageChunk(content="，我是 Agent"),
    ]
    llm.bind_tools.return_value = llm
    llm.stream.return_value = iter(chunks)
    return llm


@pytest.fixture
def mock_llm_tool_call():
    """模拟 LLM 返回 tool_calls"""
    llm = MagicMock()
    chunks = [
        AIMessageChunk(
            content="",
            tool_call_chunks=[{
                "name": "read_file",
                "args": '{"path": "test.c"}',
                "id": "call_123",
                "index": 0,
            }],
        ),
    ]
    llm.bind_tools.return_value = llm
    llm.stream.return_value = iter(chunks)
    return llm
