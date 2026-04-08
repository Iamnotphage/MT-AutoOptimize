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


# ---------------------------------------------------------------------------
# Context & Memory 配置
# ---------------------------------------------------------------------------
CONTEXT = {
    # 自动扫描的 context 文件名（项目根目录）
    "file_names": ["CONTEXT.md"],
    # 全局配置目录（存放全局 CONTEXT.md、history 等）
    "global_dir": os.path.expanduser("~/.mtagent"),
    # 压缩: 历史 token 占 context window 的百分比阈值 (参考 gemini-cli 50%)
    "compression_threshold": 0.50,
    # 压缩: 保留最近消息的比例 (参考 gemini-cli 30%)
    "compression_preserve_ratio": 0.30,
    # Token limit: 模型的 context window 大小（默认值，可被运行时覆盖）
    "token_limit": 131072,
}


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


