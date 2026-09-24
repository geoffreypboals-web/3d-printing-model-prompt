# Backlog

Actionable backlog in `Ready to implement` / `Needs scoping first` /
`Known issues` format. Started 2026-09-23 by the fleet doc review (this repo
had no Backlog.md yet).

## Ready to implement

*(Nothing yet beyond the review findings below.)*

## Known issues / tech debt

### Doc review findings (2026-09-23)

Verification-only fleet review: docs were checked against the current code,
compose files, launchers, `pytest --collect-only` counts, and the suite rules
in `D:\ARCHITECTURE.md` ("Rules for adding a new project", rules 3/8/9/10)
and the global CLAUDE.md. Nothing was changed except this list. Severity:
**High** = a reader following the docs hits a real failure; **Med** = stale
or wrong fact; **Low** = hygiene. Suite-wide items (shared across repos) live
in `SuiteControl/Backlog.md`.

- **High** `.env.example:14` ships `OLLAMA_HOST=http://localhost:11434` uncommented, and its comment (lines 11-13) says localhost is right for "a native Ollama on your host reached from Docker". Inside the container localhost is the container itself; `docker-compose.yml`'s own header (lines 14-20) says Docker needs `http://ollama:11434` or `http://host.docker.internal:11434`. Because compose reads OLLAMA_HOST only from `.env`, following README's `cp .env.example .env && docker compose up --build` gives an app that can't reach Ollama. (This machine's `.env` was hand-fixed, so it doesn't show locally.) Ship it commented out, per the global `.env` rule.
- **Med** README command blocks use bare newlines and a Linux-only path (rules 8/9): `README.md:227`, `:244`, `:258`, `:272` (`cd /home/user/3d-printing-model-prompt` followed by newline-separated commands; no Windows block).
- **Med** No launcher and no port management: `docker-compose.yml` hardcodes `8000:8000` and `11434:11434` (no `${VAR:-default}`), and there's no `launch.py` / `Launch.bat` / `launch.sh` / `.desktop`. The bundled `ollama` service's 11434 collides with the host's shared Ollama on 11434 (ARCHITECTURE port registry).
- **Med** Not in `D:\README.md` or `D:\ARCHITECTURE.md` (inventory or port registry), even though 3dPrinterWorkshopManager depends on it via `MODEL_PROMPT_SERVICE_URL` (port 8000).
- **Med** Local checkout is on `feature/freecad-backend` (3 ahead / 4 behind `origin/main`). This review read that branch's docs; re-check against `main`.
- **Low** `README.md:332` and `CLAUDE.md` reference `CONTRIBUTING.md`, which doesn't exist (README says "yet"). The repo is also missing ARCHITECTURE.md / GettingStarted.md / HELP.md from the suite doc set.
- **Low** This is the only **public** repo under the account (`gh repo list`); `D:\README.md` says every repo is private.
