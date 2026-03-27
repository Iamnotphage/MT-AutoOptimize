"""LLM 客户端封装 — 统一 ChatOpenAI 创建入口

兼容所有 OpenAI API 后端 (DeepSeek / 通义千问 / OpenAI 等)。
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_openai import ChatOpenAI

logger = logging.getLogger(__name__)


def create_chat_model(
    llm_config: dict[str, Any] | None = None,
    *,
    streaming: bool = True,
    temperature: float = 0.0,
    **kwargs: Any,
) -> ChatOpenAI:
    """创建 ChatOpenAI 实例。

    从 llm_config 字典中读取 api_key / base_url / model。
    """
    cfg = llm_config or {}

    api_key  = str(cfg.get("api_key", "")).strip()
    base_url = str(cfg.get("base_url", "")).strip()
    model    = str(cfg.get("model", "")).strip()

    if not api_key:
        raise ValueError("Missing API Key — set LLM_API_KEY environment variable")
    if not model:
        raise ValueError("Missing model — set MODEL_NAME environment variable")

    logger.info("ChatOpenAI: model=%s, base_url=%s", model, base_url or "(default)")

    return ChatOpenAI(
        api_key=api_key,
        base_url=base_url or None,
        model=model,
        streaming=streaming,
        temperature=temperature,
        **kwargs,
    )

