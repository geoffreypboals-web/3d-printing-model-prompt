# Troubleshooting

A living log of non-obvious issues and their fixes, per CLAUDE.md rule 5 --
add to this whenever a real bug/config problem gets solved here, so it
doesn't get re-debugged from scratch later.

## "Unable to reach Ollama at http://host.docker.internal:11434"

The container can't reach Ollama. Check, in order:

1. Is Ollama actually running? `ollama serve`, its Docker container, or the
   Ollama desktop app must be up on the host.
2. **Docker Desktop (Windows/Mac):** `host.docker.internal` should resolve
   automatically. Confirm from inside the container:
   `docker compose exec app python -c "import socket; print(socket.gethostbyname('host.docker.internal'))"`
3. **Native Docker Engine (Linux):** `host.docker.internal` only resolves
   because `docker-compose.yml` adds an explicit `extra_hosts:
   host.docker.internal:host-gateway` entry (Docker Engine 20.10+). On an
   older Engine, set `OLLAMA_BASE_URL` in `.env` to the host's real LAN IP
   instead.
4. If Ollama is itself running in Docker on the same machine, it's often
   simpler to put both containers on the same Docker network and use the
   Ollama container's service name as the host instead of
   `host.docker.internal`.

## `docker compose up` starts but `/health` never responds

Check `docker compose logs app` first -- a stack trace there means the
process crashed on startup (most likely a missing/invalid environment
variable). Every variable in `docker-compose.yml` has a `${VAR:-default}`
fallback, so a crash-on-startup here is unusual; if it happens, it's most
likely a genuinely malformed `.env` value (e.g. `MAX_PROMPT_CHARS` set to a
non-integer string).

## `launch.py up` reports a port other than 8800

Expected, not a bug -- something else already had 8800. `launch.py status`
always reports the real resolved port; `launch.py open` uses it
automatically. Run `python launch.py relocate` to force a fresh scan if the
resolved port seems stale.

## Windows: a script fails inside the container with a strange "not found" error

Almost always a CRLF line-ending issue in a file copied into the image --
`.gitattributes` at the repo root normalizes this for tracked files, but a
locally-created untracked script won't be covered. Re-save it with LF line
endings, or run `git add --renormalize .` after checking `.gitattributes`
covers its extension.
