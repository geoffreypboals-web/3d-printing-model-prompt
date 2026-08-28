# Changelog

Format loosely follows [Keep a Changelog](https://keepachangelog.com/);
versions follow semantic versioning (CLAUDE.md rule 16) once this project
has real usage to version against.

## [0.1.0] - 2026-08-28

### Added

- Initial working implementation: `src/prompt_builder.py` (validated,
  printer-aware prompt construction), `src/llm_client.py` (Ollama-default,
  Anthropic-optional backend abstraction), `app.py` (FastAPI web app + JSON
  API), a minimal static web UI, and `launch.py` (Docker-based, suite port-
  registry-compatible launcher).
- Dockerfile, docker-compose.yml, `.env.example`.
- Test suite (pytest) covering the golden path and edge cases for every
  module; CI (lint + test + Docker build) on every push.
- `docs/adr/0001-ollama-first-llm-backend.md`, `TROUBLESHOOTING.md`,
  `LICENSE` (MIT).
