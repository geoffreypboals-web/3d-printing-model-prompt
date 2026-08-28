# 3D Printing Model Prompt

Turns a plain-language description ("a wall-mounted hook for a bike
helmet") into a structured, printer-aware prompt, then asks a configured
LLM backend to generate a physically printable design description from it
-- one that respects your printer's actual build volume instead of
suggesting something that won't fit.

## What it does

- **`src/prompt_builder.py`** validates and formats the request into a
  structured prompt (rejects empty input, oversized input, and a
  non-positive printer size, each with a specific, actionable reason).
- **`src/llm_client.py`** sends that prompt to the configured backend and
  returns its generated text. Provider-agnostic by design (see "LLM
  backend" below).
- A minimal web UI (`static/`) and a JSON API (`POST /api/generate`) --
  use whichever fits your workflow.

## Quick start

```powershell
cd D:\3d-printing-model-prompt; python launch.py up; python launch.py open
```
```bash
cd ~/3d-printing-model-prompt && python3 launch.py up && python3 launch.py open
```

This builds and starts the container (`docker compose up -d --build` under
the hood), resolving a free host port the same way every other project in
this suite does (default `8800`; `python launch.py status` shows the real
resolved port if that one was taken). No `.env` file is required for local
Ollama-backed use -- every setting has a working default (see
`.env.example`).

```powershell
cd D:\3d-printing-model-prompt; python launch.py down
```
```bash
cd ~/3d-printing-model-prompt && python3 launch.py down
```

Stops the container. Never passes `-v`/`--volumes`, so nothing here
currently holds persistent state to lose (this project has no database or
generated-file storage yet -- see "Backups" below if that changes).

## Docker (manual, without launch.py)

```bash
docker build -t 3d-printing-model-prompt .
docker compose up -d --build
```

## LLM backend: Ollama by default, no ongoing cost

Every LLM call goes through `src/llm_client.py`'s `get_client()` -- the
runtime backend is a config choice (`LLM_PROVIDER` in `.env`), never
hardcoded at a call site:

| `LLM_PROVIDER` | Backend | Cost | Requires |
|---|---|---|---|
| `ollama` (default) | Local Ollama instance | None | Ollama running and reachable (`ollama serve`, or its own Docker container) |
| `anthropic` | Anthropic's Messages API | Per-request, metered | `ANTHROPIC_API_KEY` set in `.env` |

Ollama is the default specifically to avoid locking this project's normal,
interactive, unbounded-call-count usage into a paid API (see
`docs/adr/0001-ollama-first-llm-backend.md` for the full reasoning).
Switching to Anthropic is a one-line `.env` change -- `get_client()` raises
immediately if `ANTHROPIC_API_KEY` is missing rather than silently falling
back to Ollama, so a misconfigured paid backend never masquerades as a
working one.

Ollama's general-purpose models are meaningfully weaker than a frontier
hosted model at nuanced generation; that tradeoff is accepted for this
project's task (see the ADR). If a specific generation genuinely needs
higher quality, switch `LLM_PROVIDER` for that session rather than making
Anthropic the default.

## Configuration

All environment-specific values come from environment variables -- see
`.env.example` for the full list with defaults. Copy it to `.env` (git-
ignored) only if you need to override something; local Ollama-backed
development needs no `.env` at all.

## SuiteControl integration

This project follows the same `launch.py up`/`open`/`status`/`down`/
`relocate` contract every SuiteControl-managed project uses, and is
registered in `D:\SuiteControl\projects.py` as a `web-launch` project --
Start/Stop/Open Web UI on its dashboard card work exactly like any sibling
project's. It also runs standalone with no dependency on SuiteControl being
installed at all (see `src/port_registry.py`'s own docstring for why that
module is a deliberate copy, not a shared import).

## Testing

```bash
python3 -m pip install -r requirements.txt
python3 -m pytest -q
```

Covers the golden path and edge cases (empty/oversized input, unreachable
backend, missing credentials, non-200 responses) for every module, per
CLAUDE.md rule 11.

## Linting

```bash
python3 -m pip install ruff
ruff check .
```

Rule set pinned in `ruff.toml` so it runs identically for anyone (CLAUDE.md
rule 14). CI (`.github/workflows/ci.yml`) runs lint, tests, and a Docker
build on every push.

## Dependencies & licensing

Every dependency (`requirements.txt`) is MIT/BSD/Apache-2.0 licensed --
no copyleft, no paid/proprietary licensing involved:

| Package | License |
|---|---|
| fastapi, uvicorn, httpx, pydantic, pytest, pytest-asyncio | MIT |

## Project status & license

MIT-licensed (`LICENSE`) -- the default per CLAUDE.md rule 19, open source
from the start, no contributor process set up yet (add `CONTRIBUTING.md`
once this is ready for outside contributions).

## Data privacy

This project doesn't collect or store any personal data -- a request's
text and the generated result exist only for the duration of that HTTP
request (no database, no logging of prompt content beyond the process's
own stdout, no telemetry).

## Backups

No persistent state to back up yet -- if this project later gains a
database or generated-file storage, document the backup/restore procedure
here before that data becomes something that can be lost.
