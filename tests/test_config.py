"""Tests for src/config.py: env-var defaults and overrides."""
from src.config import load_settings


def test_defaults_when_no_env_vars_set(monkeypatch):
    for var in (
        "LLM_PROVIDER", "OLLAMA_BASE_URL", "OLLAMA_MODEL", "ANTHROPIC_API_KEY",
        "ANTHROPIC_MODEL", "MAX_PROMPT_CHARS", "MAX_MODEL_SIZE_MM", "REQUEST_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(var, raising=False)

    settings = load_settings()
    assert settings.llm_provider == "ollama"
    assert settings.ollama_model == "llama3.1"
    assert settings.anthropic_api_key is None
    assert settings.max_prompt_chars == 4000
    assert settings.max_model_size_mm == 250.0


def test_env_vars_override_defaults(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "Anthropic")
    monkeypatch.setenv("MAX_PROMPT_CHARS", "100")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    settings = load_settings()
    assert settings.llm_provider == "anthropic"
    assert settings.max_prompt_chars == 100
    assert settings.anthropic_api_key == "sk-test"


def test_empty_anthropic_key_is_treated_as_unset(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    settings = load_settings()
    assert settings.anthropic_api_key is None
