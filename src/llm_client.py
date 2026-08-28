"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/src/llm_client.py
Description: Provider-agnostic LLM client abstraction (CLAUDE.md rule 6).
    Every call site goes through get_client() + generate() -- swapping the
    runtime backend between the no-cost local Ollama default and a paid
    API is a config change (LLM_PROVIDER env var), never a call-site
    rewrite. Ollama is the default specifically so running this project
    day-to-day carries no per-request API cost; a paid provider is opt-in,
    never the default.
Inputs: src/config.py's Settings (provider selection, model name, base
    URL/API key, timeout), a prompt string from src/prompt_builder.py.
Outputs: The backend's generated text, or raises LLMError with a reason a
    user can act on -- never a raw stack trace.
Troubleshooting:
    - "Unable to reach Ollama": confirm Ollama is running (`ollama serve`
      or its Docker container) and OLLAMA_BASE_URL is reachable from
      inside this container (host.docker.internal, not localhost).
    - Switching to Anthropic and nothing happens: check
      ANTHROPIC_API_KEY is actually set -- get_client() raises
      immediately rather than silently falling back to Ollama, so a
      misconfigured paid backend never masquerades as a working one.
"""
import httpx

from .config import Settings


class LLMError(Exception):
    """Raised for any backend failure -- unreachable host, bad response,
    missing credentials -- always with a human-readable reason."""


class LLMClient:
    """Base interface every provider implements: one method, one job."""

    async def generate(self, prompt_text: str) -> str:
        """Sends prompt_text to the backend and returns its generated
        text, or raises LLMError."""
        raise NotImplementedError


class OllamaClient(LLMClient):
    """The default, no-cost backend -- talks to a local (or LAN/Docker-
    host) Ollama instance's /api/generate endpoint."""

    def __init__(self, base_url: str, model: str, timeout_seconds: float):
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout_seconds

    async def generate(self, prompt_text: str) -> str:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    f"{self._base_url}/api/generate",
                    json={"model": self._model, "prompt": prompt_text, "stream": False},
                )
        except httpx.HTTPError as exc:
            raise LLMError(f"Unable to reach Ollama at {self._base_url}: {exc}") from exc
        if response.status_code != 200:
            raise LLMError(f"Ollama request failed ({response.status_code}): {response.text[:300]}")
        body = response.json()
        text = body.get("response")
        if not text:
            raise LLMError("Ollama returned an empty response.")
        return text


class AnthropicClient(LLMClient):
    """Paid-API backend -- only ever used when LLM_PROVIDER=anthropic is
    set explicitly (see get_client()), never the default. Uses the plain
    Messages API over HTTP rather than adding the anthropic SDK as a
    dependency for this one call site."""

    _API_URL = "https://api.anthropic.com/v1/messages"

    def __init__(self, api_key: str, model: str, timeout_seconds: float):
        self._api_key = api_key
        self._model = model
        self._timeout = timeout_seconds

    async def generate(self, prompt_text: str) -> str:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    self._API_URL,
                    headers={
                        "x-api-key": self._api_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": self._model,
                        "max_tokens": 1024,
                        "messages": [{"role": "user", "content": prompt_text}],
                    },
                )
        except httpx.HTTPError as exc:
            raise LLMError(f"Unable to reach the Anthropic API: {exc}") from exc
        if response.status_code != 200:
            raise LLMError(f"Anthropic request failed ({response.status_code}): {response.text[:300]}")
        body = response.json()
        blocks = body.get("content") or []
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        if not text:
            raise LLMError("Anthropic returned an empty response.")
        return text


def get_client(settings: Settings) -> LLMClient:
    """The one place provider selection happens -- every call site imports
    this, never a concrete provider class directly, so adding a third
    backend later never touches app.py."""
    if settings.llm_provider == "ollama":
        return OllamaClient(settings.ollama_base_url, settings.ollama_model, settings.request_timeout_seconds)
    if settings.llm_provider == "anthropic":
        if not settings.anthropic_api_key:
            raise LLMError("LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY isn't set -- add it to .env.")
        return AnthropicClient(settings.anthropic_api_key, settings.anthropic_model, settings.request_timeout_seconds)
    raise LLMError(f"Unknown LLM_PROVIDER {settings.llm_provider!r} -- use 'ollama' or 'anthropic'.")
