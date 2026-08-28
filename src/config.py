"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/src/config.py
Description: Single source of truth for this project's configuration --
    every environment-specific value (LLM backend selection, model name,
    printer size defaults, request limits) comes from an environment
    variable read here, never hardcoded or scattered as `if env == "prod"`
    branches elsewhere in the codebase (per this repo's CLAUDE.md rule 22).
Inputs: Environment variables -- see .env.example for the full documented
    list and default values.
Outputs: A validated, immutable Settings instance other modules read from.
Troubleshooting:
    - If the LLM backend silently uses the wrong provider, confirm
      LLM_PROVIDER is actually set in .env -- `docker compose` only reads
      .env from the project root automatically, not a nested folder.
    - If Ollama calls fail from inside the container but work fine from
      the host shell, check OLLAMA_BASE_URL: it must be
      http://host.docker.internal:11434 on Docker Desktop (Windows/Mac),
      not localhost/127.0.0.1 -- that resolves to the container itself,
      not the host running Ollama.
"""
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    """Immutable process-wide configuration. frozen=True so nothing
    downstream can mutate a shared instance and create a "works on my
    machine" bug from state that silently diverged mid-run."""

    llm_provider: str
    ollama_base_url: str
    ollama_model: str
    anthropic_api_key: str | None
    anthropic_model: str
    max_prompt_chars: int
    max_model_size_mm: float
    request_timeout_seconds: float


def load_settings() -> Settings:
    """Reads current environment variables into a fresh Settings instance.
    Deliberately not cached in a module-level singleton -- callers that
    need a fixed value for the life of a request call this once and pass
    the result along, and tests can monkeypatch os.environ and call this
    again without needing to reload the module."""
    return Settings(
        llm_provider=os.environ.get("LLM_PROVIDER", "ollama").strip().lower(),
        ollama_base_url=os.environ.get("OLLAMA_BASE_URL", "http://host.docker.internal:11434"),
        ollama_model=os.environ.get("OLLAMA_MODEL", "llama3.1"),
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY") or None,
        anthropic_model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5"),
        max_prompt_chars=int(os.environ.get("MAX_PROMPT_CHARS", "4000")),
        max_model_size_mm=float(os.environ.get("MAX_MODEL_SIZE_MM", "250")),
        request_timeout_seconds=float(os.environ.get("REQUEST_TIMEOUT_SECONDS", "60")),
    )
