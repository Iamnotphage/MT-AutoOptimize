from unittest.mock import patch, MagicMock

import pytest

from core.llm import create_chat_model

MOCK_CLS = "core.llm.ChatOpenAI"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """确保每个测试无残留环境变量"""
    for suffix in ("API_KEY", "BASE_URL", "MODEL_NAME"):
        monkeypatch.delenv(f"LLM_{suffix}", raising=False)


class TestCreateChatModel:

    @patch(MOCK_CLS)
    def test_from_config_dict(self, mock_cls):
        cfg = {"api_key": "sk-test", "base_url": "https://example.com", "model": "gpt-4"}
        create_chat_model(cfg)

        mock_cls.assert_called_once_with(
            api_key="sk-test",
            base_url="https://example.com",
            model="gpt-4",
            streaming=True,
            temperature=0.0,
        )

    @patch(MOCK_CLS)
    def test_base_url_empty_becomes_none(self, mock_cls):
        cfg = {"api_key": "sk-x", "model": "m"}
        create_chat_model(cfg)

        _, kw = mock_cls.call_args
        assert kw["base_url"] is None

    @patch(MOCK_CLS)
    def test_custom_temperature_and_streaming(self, mock_cls):
        cfg = {"api_key": "sk-x", "model": "m"}
        create_chat_model(cfg, streaming=False, temperature=0.7)

        _, kw = mock_cls.call_args
        assert kw["streaming"] is False
        assert kw["temperature"] == 0.7

    @patch(MOCK_CLS)
    def test_kwargs_passthrough(self, mock_cls):
        cfg = {"api_key": "sk-x", "model": "m"}
        create_chat_model(cfg, max_tokens=4096, timeout=30)

        _, kw = mock_cls.call_args
        assert kw["max_tokens"] == 4096
        assert kw["timeout"] == 30


class TestValidation:

    def test_missing_api_key_raises(self):
        with pytest.raises(ValueError, match="API Key"):
            create_chat_model({"model": "m"})

    def test_missing_model_raises(self):
        with pytest.raises(ValueError, match="model"):
            create_chat_model({"api_key": "sk-x"})

    def test_none_config_and_no_env_raises(self):
        with pytest.raises(ValueError):
            create_chat_model()

    @patch(MOCK_CLS)
    def test_whitespace_env_ignored(self, mock_cls, monkeypatch):
        monkeypatch.setenv("LLM_API_KEY", "   ")
        cfg = {"api_key": "sk-fallback", "model": "m"}

        create_chat_model(cfg)

        _, kw = mock_cls.call_args
        assert kw["api_key"] == "sk-fallback"

