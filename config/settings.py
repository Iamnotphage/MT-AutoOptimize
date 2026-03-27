"""Configuration management — load from environment variables"""

import os
from typing import TypedDict

try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv()
except ImportError:
    pass


class LLMConfig(TypedDict):
    api_key: str
    base_url: str
    model: str


def load_llm_config() -> LLMConfig:
    """Load LLM configuration from environment variables.

    Expected environment variables:
    - LLM_API_KEY: API key for LLM service
    - LLM_BASE_URL: Base URL for LLM service
    - MODEL_NAME: Model name to use
    """
    return LLMConfig(
        api_key=os.environ.get("LLM_API_KEY", "").strip(),
        base_url=os.environ.get("LLM_BASE_URL", "").strip(),
        model=os.environ.get("MODEL_NAME", "").strip(),
    )


