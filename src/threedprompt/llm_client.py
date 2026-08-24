"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/src/threedprompt/llm_client.py
Description: Thin abstraction over the LLM backend used for complexity
    classification and code generation, so call sites never know whether
    they're talking to a local Ollama model or the Claude API (rule 6).
    Provider selection is entirely config-driven via LLM_PROVIDER.
Inputs: config.settings (llm_provider, ollama_host, ollama_model,
    anthropic_api_key, anthropic_model, llm_timeout_seconds)
Outputs: get_llm_client() returns an LLMClient with a .generate(prompt,
    system) -> str method and a .is_reachable() health check.
Troubleshooting:
    - "LLM provider 'claude' requires ANTHROPIC_API_KEY": set it in .env
      or switch LLM_PROVIDER=ollama for a no-cost local backend.
    - Ollama connection errors: confirm `ollama serve` is running and
      OLLAMA_HOST points at it (use the docker-compose service name, not
      localhost, when this app runs in a container).
    - Any single LLM failure should never crash a request outright -
      callers (classifier, generators) are expected to catch LLMError and
      fall back to a heuristic/template path where one exists.
"""

from __future__ import annotations

import abc
import json

import requests

from threedprompt.config import settings
from threedprompt.logging_config import get_logger

logger = get_logger(__name__)


class LLMError(RuntimeError):
    """Raised when an LLM call fails or the configured provider is misconfigured."""


class LLMClient(abc.ABC):
    """Common interface for any LLM backend used by this service."""

    @abc.abstractmethod
    def generate(self, prompt: str, system: str | None = None) -> str:
        """Send prompt (+ optional system instruction) to the LLM and return its raw text response."""

    @abc.abstractmethod
    def is_reachable(self) -> bool:
        """Best-effort check of whether this backend can currently serve a request."""


class OllamaClient(LLMClient):
    """No-cost local inference backend via the Ollama HTTP API."""

    def __init__(self, host: str, model: str, timeout_seconds: float) -> None:
        self._host = host.rstrip("/")
        self._model = model
        self._timeout = timeout_seconds

    def generate(self, prompt: str, system: str | None = None) -> str:
        payload = {"model": self._model, "prompt": prompt, "stream": False}
        if system:
            payload["system"] = system
        try:
            resp = requests.post(f"{self._host}/api/generate", json=payload, timeout=self._timeout)
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise LLMError(f"Ollama request failed: {exc}") from exc
        try:
            data = resp.json()
        except json.JSONDecodeError as exc:
            raise LLMError(f"Ollama returned non-JSON response: {exc}") from exc
        response_text = data.get("response")
        if not response_text:
            raise LLMError("Ollama response missing 'response' field")
        return response_text

    def is_reachable(self) -> bool:
        try:
            resp = requests.get(f"{self._host}/api/tags", timeout=5)
            return resp.ok
        except requests.RequestException:
            return False


class ClaudeClient(LLMClient):
    """Paid API backend via Anthropic's Claude models - opt-in per rule 6."""

    def __init__(self, api_key: str, model: str, timeout_seconds: float) -> None:
        if not api_key:
            raise LLMError("LLM provider 'claude' requires ANTHROPIC_API_KEY to be set")
        try:
            import anthropic
        except ImportError as exc:
            raise LLMError(
                "LLM_PROVIDER=claude requires the 'anthropic' package; install it or switch to LLM_PROVIDER=ollama"
            ) from exc
        self._client = anthropic.Anthropic(api_key=api_key, timeout=timeout_seconds)
        self._model = model

    def generate(self, prompt: str, system: str | None = None) -> str:
        try:
            message = self._client.messages.create(
                model=self._model,
                max_tokens=4096,
                system=system or "",
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as exc:  # anthropic raises its own exception hierarchy
            raise LLMError(f"Claude request failed: {exc}") from exc
        text_blocks = [block.text for block in message.content if getattr(block, "type", None) == "text"]
        if not text_blocks:
            raise LLMError("Claude response contained no text content")
        return "".join(text_blocks)

    def is_reachable(self) -> bool:
        try:
            self._client.models.list(limit=1)
            return True
        except Exception:  # noqa: BLE001 - health check, any failure means "not reachable"
            return False


def get_llm_client() -> LLMClient:
    """Build the configured LLM client. Ollama is the default, no-cost backend (rule 6)."""
    provider = settings.llm_provider
    if provider == "ollama":
        return OllamaClient(settings.ollama_host, settings.ollama_model, settings.llm_timeout_seconds)
    if provider == "claude":
        return ClaudeClient(settings.anthropic_api_key, settings.anthropic_model, settings.llm_timeout_seconds)
    raise LLMError(f"Unknown LLM_PROVIDER '{provider}'; expected 'ollama' or 'claude'")
