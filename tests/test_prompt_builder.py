"""Tests for src/prompt_builder.py: golden path plus validation edges."""
import pytest

from src.config import Settings
from src.prompt_builder import PromptValidationError, build_spec


def _settings(**overrides) -> Settings:
    base = {
        "llm_provider": "ollama", "ollama_base_url": "http://x", "ollama_model": "llama3.1",
        "anthropic_api_key": None, "anthropic_model": "claude-sonnet-5",
        "max_prompt_chars": 4000, "max_model_size_mm": 250.0, "request_timeout_seconds": 60.0,
    }
    base.update(overrides)
    return Settings(**base)


def test_golden_path_builds_a_prompt_containing_the_request():
    spec = build_spec("a wall-mounted hook for a bike helmet", _settings())
    assert "a wall-mounted hook for a bike helmet" in spec.prompt_text
    assert spec.max_size_mm == 250.0


def test_empty_input_is_rejected():
    with pytest.raises(PromptValidationError, match="can't be empty"):
        build_spec("   ", _settings())


def test_oversized_input_is_rejected():
    with pytest.raises(PromptValidationError, match="character limit"):
        build_spec("x" * 10, _settings(max_prompt_chars=5))


def test_per_request_printer_size_overrides_default():
    spec = build_spec("a small gear", _settings(max_model_size_mm=250.0), printer_max_size_mm=100.0)
    assert spec.max_size_mm == 100.0
    assert "100mm" in spec.prompt_text


def test_zero_printer_size_is_rejected():
    with pytest.raises(PromptValidationError, match="positive"):
        build_spec("a small gear", _settings(), printer_max_size_mm=0)


def test_negative_printer_size_is_rejected():
    with pytest.raises(PromptValidationError, match="positive"):
        build_spec("a small gear", _settings(), printer_max_size_mm=-10)


def test_input_is_stripped_of_surrounding_whitespace():
    spec = build_spec("  a small gear  \n", _settings())
    assert spec.raw_input == "a small gear"
