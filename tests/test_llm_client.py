"""Tests for src/llm_client.py: provider selection and each backend's
golden path plus its failure modes (unreachable, bad response, missing
credentials)."""
import httpx
import pytest

from src.config import Settings
from src.llm_client import AnthropicClient, LLMError, OllamaClient, get_client


def _settings(**overrides) -> Settings:
    base = {
        "llm_provider": "ollama", "ollama_base_url": "http://ollama:11434", "ollama_model": "llama3.1",
        "anthropic_api_key": None, "anthropic_model": "claude-sonnet-5",
        "max_prompt_chars": 4000, "max_model_size_mm": 250.0, "request_timeout_seconds": 5.0,
    }
    base.update(overrides)
    return Settings(**base)


def test_get_client_defaults_to_ollama():
    client = get_client(_settings())
    assert isinstance(client, OllamaClient)


def test_get_client_returns_anthropic_when_configured():
    client = get_client(_settings(llm_provider="anthropic", anthropic_api_key="sk-test"))
    assert isinstance(client, AnthropicClient)


def test_get_client_rejects_anthropic_without_api_key():
    with pytest.raises(LLMError, match="ANTHROPIC_API_KEY"):
        get_client(_settings(llm_provider="anthropic", anthropic_api_key=None))


def test_get_client_rejects_unknown_provider():
    with pytest.raises(LLMError, match="Unknown LLM_PROVIDER"):
        get_client(_settings(llm_provider="not-a-real-provider"))


async def test_ollama_client_golden_path(monkeypatch):
    class FakeResponse:
        status_code = 200
        def json(self):
            return {"response": "a printable hook design"}

    async def fake_post(self, url, json=None):
        assert url == "http://ollama:11434/api/generate"
        return FakeResponse()

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    result = await OllamaClient("http://ollama:11434", "llama3.1", 5.0).generate("a hook")
    assert result == "a printable hook design"


async def test_ollama_client_unreachable_raises_llm_error(monkeypatch):
    async def raise_connect_error(self, url, json=None):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx.AsyncClient, "post", raise_connect_error)
    with pytest.raises(LLMError, match="Unable to reach Ollama"):
        await OllamaClient("http://ollama:11434", "llama3.1", 5.0).generate("a hook")


async def test_ollama_client_non_200_raises_llm_error(monkeypatch):
    class FakeResponse:
        status_code = 500
        text = "internal error"

    async def fake_post(self, url, json=None):
        return FakeResponse()

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    with pytest.raises(LLMError, match="500"):
        await OllamaClient("http://ollama:11434", "llama3.1", 5.0).generate("a hook")


async def test_ollama_client_empty_response_raises_llm_error(monkeypatch):
    class FakeResponse:
        status_code = 200
        def json(self):
            return {"response": ""}

    async def fake_post(self, url, json=None):
        return FakeResponse()

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    with pytest.raises(LLMError, match="empty"):
        await OllamaClient("http://ollama:11434", "llama3.1", 5.0).generate("a hook")


async def test_anthropic_client_golden_path(monkeypatch):
    class FakeResponse:
        status_code = 200
        def json(self):
            return {"content": [{"type": "text", "text": "a printable hook design"}]}

    async def fake_post(self, url, headers=None, json=None):
        assert headers["x-api-key"] == "sk-test"
        return FakeResponse()

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    result = await AnthropicClient("sk-test", "claude-sonnet-5", 5.0).generate("a hook")
    assert result == "a printable hook design"


async def test_anthropic_client_non_200_raises_llm_error(monkeypatch):
    class FakeResponse:
        status_code = 401
        text = "unauthorized"

    async def fake_post(self, url, headers=None, json=None):
        return FakeResponse()

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    with pytest.raises(LLMError, match="401"):
        await AnthropicClient("sk-bad", "claude-sonnet-5", 5.0).generate("a hook")
