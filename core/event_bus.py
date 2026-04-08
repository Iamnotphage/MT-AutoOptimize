import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

logger = logging.getLogger(__name__)


class EventType(Enum):
    """事件类型"""
    # 流式输出
    CONTENT = "content"
    THOUGHT = "thought"
    TOOL_CALL_REQUEST = "tool_call_request"

    # 工具执行
    TOOL_STATE_UPDATE = "tool_state_update"
    TOOL_LIVE_OUTPUT = "tool_live_output"
    TOOL_CALL_COMPLETE = "tool_call_complete"
    ALL_TOOLS_COMPLETE = "all_tools_complete"

    # 权限确认
    APPROVAL_REQUEST = "approval_request"
    APPROVAL_RESPONSE = "approval_response"

    # 会话控制
    TURN_START = "turn_start"
    TURN_END = "turn_end"
    SESSION_END = "session_end"
    ERROR = "error"
    CONTEXT_COMPRESSED = "context_compressed"
    TRANSCRIPT_MESSAGE = "transcript_message"


@dataclass
class AgentEvent:
    type: EventType
    data: Any
    turn: int = 0
    timestamp: float = field(default_factory=time.time)


class EventBus:
    """
    同步事件总线 — 层间通信核心

    Usage::
    
        bus = EventBus()
        bus.subscribe(EventType.CONTENT, lambda e: print(e.data))
        bus.emit(AgentEvent(type=EventType.CONTENT, data={"text": "hello"}))
    """

    def __init__(self) -> None:
        # EventType -> list[callback]; None key = wildcard (subscribe_all)
        self._subscribers: dict[EventType | None, list[Callable[[AgentEvent], None]]] = defaultdict(list)

    def subscribe(self, event_type: EventType, callback: Callable[[AgentEvent], None]) -> None:
        self._subscribers[event_type].append(callback)

    def subscribe_all(self, callback: Callable[[AgentEvent], None]) -> None:
        """订阅所有事件类型"""
        self._subscribers[None].append(callback)

    def unsubscribe(self, event_type: EventType, callback: Callable[[AgentEvent], None]) -> None:
        subs = self._subscribers.get(event_type, [])
        if callback in subs:
            subs.remove(callback)

    def emit(self, event: AgentEvent) -> None:
        for cb in self._subscribers.get(event.type, []):
            try:
                cb(event)
            except Exception:
                logger.exception("EventBus subscriber error on %s", event.type)
        # wildcard subscribers
        for cb in self._subscribers.get(None, []):
            try:
                cb(event)
            except Exception:
                logger.exception("EventBus wildcard subscriber error")
