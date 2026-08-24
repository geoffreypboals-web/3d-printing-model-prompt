"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/src/threedprompt/config.py
Description: Single source of truth for all environment-driven
    configuration. Every environment-specific value (LLM backend, binary
    paths, storage location, timeouts) is read here from env vars so the
    rest of the codebase never branches on environment directly.
Inputs: Environment variables (see .env.example at
    /home/user/3d-printing-model-prompt/.env.example for the full list
    and defaults).
Outputs: A module-level `settings` Settings instance used by the rest of
    the app.
Troubleshooting:
    - If the service starts but calls to the LLM fail, check LLM_PROVIDER
      and, for Ollama, that OLLAMA_HOST is reachable from inside the
      container (use the Docker service name, not "localhost", when
      running via docker-compose).
    - If OpenSCAD/Blender generation fails with "binary not found", check
      OPENSCAD_BINARY / BLENDER_BINARY match what's actually on PATH
      inside the container (see the Dockerfile).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env_bool(name: str, default: bool) -> bool:
    """Read an environment variable as a boolean, accepting 1/true/yes."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    """Read an environment variable as an int, falling back to default on empty/invalid values."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    """Read an environment variable as a float, falling back to default on empty/invalid values."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass
class Settings:
    """
    Typed snapshot of the service's configuration, populated once from
    environment variables at import time. Not frozen: tests deliberately
    mutate the shared `settings` instance's fields (e.g. output_dir) to
    redirect the app at a temp directory without needing every module to
    re-import a patched object.
    """

    # --- LLM backend (rule 6: Ollama-first, swappable) ---
    llm_provider: str = field(default_factory=lambda: os.environ.get("LLM_PROVIDER", "ollama").lower())
    ollama_host: str = field(default_factory=lambda: os.environ.get("OLLAMA_HOST", "http://localhost:11434"))
    ollama_model: str = field(default_factory=lambda: os.environ.get("OLLAMA_MODEL", "llama3.1"))
    anthropic_api_key: str = field(default_factory=lambda: os.environ.get("ANTHROPIC_API_KEY", ""))
    anthropic_model: str = field(default_factory=lambda: os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5"))
    llm_timeout_seconds: float = field(default_factory=lambda: _env_float("LLM_TIMEOUT_SECONDS", 60.0))

    # --- Classifier ---
    classifier_confidence_threshold: float = field(
        default_factory=lambda: _env_float("CLASSIFIER_CONFIDENCE_THRESHOLD", 0.7)
    )

    # --- External CAD tool binaries ---
    openscad_binary: str = field(default_factory=lambda: os.environ.get("OPENSCAD_BINARY", "openscad"))
    blender_binary: str = field(default_factory=lambda: os.environ.get("BLENDER_BINARY", "blender"))
    cad_subprocess_timeout_seconds: int = field(default_factory=lambda: _env_int("CAD_SUBPROCESS_TIMEOUT_SECONDS", 180))

    # --- Storage ---
    output_dir: str = field(default_factory=lambda: os.environ.get("OUTPUT_DIR", "./output"))

    # --- Request limits (rule 9/10: bounded input, no runaway spend) ---
    max_prompt_length: int = field(default_factory=lambda: _env_int("MAX_PROMPT_LENGTH", 2000))
    max_upload_bytes: int = field(default_factory=lambda: _env_int("MAX_UPLOAD_BYTES", 50 * 1024 * 1024))
    max_wall_thickness_mm: float = field(default_factory=lambda: _env_float("MAX_WALL_THICKNESS_MM", 20.0))

    # --- Server ---
    host: str = field(default_factory=lambda: os.environ.get("HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: _env_int("PORT", 8000))
    log_level: str = field(default_factory=lambda: os.environ.get("LOG_LEVEL", "INFO").upper())
    cache_enabled: bool = field(default_factory=lambda: _env_bool("CACHE_ENABLED", True))


settings = Settings()
