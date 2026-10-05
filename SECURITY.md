# Security policy

## Reporting a vulnerability

Please do not open a public issue for a vulnerability. Use GitHub's private
reporting (the repository's **Security** tab -> **Report a vulnerability**)
if it is enabled, or contact the maintainer privately through their GitHub
profile (`geoffreypboals-web`). Include the affected endpoint or file, steps
to reproduce, and the version/commit. There is no formal SLA; this is a
small project and reports are handled on a best-effort basis.

## Supported versions

Only the current `main` branch. There are no tagged releases.

## Where secrets live

- `ANTHROPIC_API_KEY` (only if `LLM_PROVIDER=claude`) is read from the
  environment (`src/threedprompt/config.py`), normally from a local `.env`
  next to `docker-compose.yml`. `.env` is git-ignored; `.env.example`
  contains placeholders only. Never commit `.env`, `output/`, or uploaded
  models.
- On lambda02 the deployed `.env` is a local file on that host, not in git.
- gitleaks runs in CI on the full history and as an optional pre-commit hook
  (`.gitleaks.toml`, `.pre-commit-config.yaml`). A key that was ever
  committed must be rotated, not just deleted.
- The CadQuery child process is started with an empty environment and its
  error text is redacted, so API keys are not exposed to LLM-written code.

## Threat notes for this app

- **No authentication, no TLS, no CORS configuration.** Anyone who can reach
  port 8000 can generate, upload, download and modify models and
  burn LLM/CPU time. Run it on a trusted network or behind a reverse proxy
  that adds auth; do not expose it to the internet as shipped. Model ids are
  random UUIDs but are the only access control.
- **Executes LLM-written code.** `/generate` runs LLM-authored OpenSCAD,
  Blender `bpy` Python, and CadQuery Python. Only the CadQuery path is
  sandboxed (Landlock file-write/read allow-lists, seccomp blocking
  `socket()`, an audit hook, rlimits, empty environment) and it refuses to run
  without Landlock. The Blender `bpy` scripts run in a headless Blender
  subprocess **without** that sandbox, as the container's non-root user, so
  treat prompts as untrusted input and keep the container unprivileged.
  Prompt text is also length-limited (`MAX_PROMPT_LENGTH`).
- **File uploads.** Meshes are parsed by Blender and FreeCAD subprocesses; malformed files can crash those subprocesses (surfaced as 4xx/5xx).
  Uploads are capped by `MAX_UPLOAD_BYTES` (300 MB default, buffered in
  memory) and `MAX_AUDIO_UPLOAD_BYTES`; extensions are allow-listed.
- **Storage.** Everything lives under `OUTPUT_DIR` with no quota or expiry;
  disk can fill up.
- **Container.** The image runs as non-root `appuser` (uid 1000) and bakes no
  secrets. The default compose file also publishes the bundled Ollama on host
  port 11434 without authentication.
- **Dependencies** are pinned in `requirements.txt` and audited by pip-audit
  in CI.
