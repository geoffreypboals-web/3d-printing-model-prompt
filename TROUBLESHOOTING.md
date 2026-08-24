# Troubleshooting

Living log of non-obvious issues and their fixes for
`/home/user/3d-printing-model-prompt`. Add to this whenever a real bug or
config problem gets solved, so it doesn't get re-debugged from scratch.

## `POST /thicken` in Swagger UI doesn't seem to give me the modified file back

Fixed - in an earlier version `POST /thicken` returned JSON
(`{model_id, method, download_url}`) requiring a second manual call to
`GET /models/{model_id}/download` to actually get the file, which wasn't
obvious from the Swagger UI form. It now returns the thickened STL
directly as the response body (one round trip), with the new model_id and
method as `X-Model-Id` / `X-Thicken-Method` response headers if you need
them. In Swagger UI, "Execute" now gives a "Download file" link with the
result. Note this only applies to `POST /thicken` (arbitrary upload) -
`POST /models/{model_id}/thicken` (thickening a model this service already
generated) still returns JSON with a `download_url`, since that endpoint
is meant to chain with other calls that already deal in model_ids.

## `GET /health` reports `openscad_available: false` or `blender_available: false`

The binaries aren't on `PATH` inside the environment running the service.

- **Docker**: shouldn't happen - the Dockerfile installs both via `apt-get`.
  If it does, rebuild with `docker build --no-cache -t threedprompt .`
  (`/home/user/3d-printing-model-prompt/Dockerfile`).
- **Local (no Docker)**: install OpenSCAD and Blender yourself, or set
  `OPENSCAD_BINARY` / `BLENDER_BINARY` in `.env`
  (`/home/user/3d-printing-model-prompt/.env.example`) to their full paths.

## `GET /health` reports `llm_reachable: false`

- `LLM_PROVIDER=ollama` (default): the Ollama server isn't reachable at
  `OLLAMA_HOST`. Running via `docker compose`, use the service name
  (`http://ollama:11434`), not `localhost` - each container has its own
  network namespace. Confirm the model is pulled:
  `docker compose exec ollama ollama pull llama3.1`.
- `LLM_PROVIDER=claude`: `ANTHROPIC_API_KEY` is missing/invalid.

## `docker compose up` fails with "port is already allocated" on `11434`

Something on the host is already using Ollama's default port - almost
always a native (non-Docker) Ollama install already running. Two options:

1. **Use your native Ollama instead of the bundled container** (recommended
   if you already have models pulled there): set
   `OLLAMA_HOST=http://host.docker.internal:11434` in `.env`, then run
   `docker compose up --build --no-deps app` - `--no-deps` is required, or
   Compose starts the bundled `ollama` service anyway and you hit the same
   conflict. On native Linux Docker (not Docker Desktop),
   `host.docker.internal` needs the `extra_hosts: host-gateway` line already
   present in `docker-compose.yml`'s `app` service - Docker Desktop
   (Windows/Mac) resolves it automatically.
2. Or free the port: `docker ps -a | grep ollama` to find a stray container
   from a previous run and `docker stop <id>`, or stop the native Ollama
   service on the host.

**If you did set `OLLAMA_HOST` correctly but `/health` still reports
`llm_reachable: false`**: this was a real bug in an earlier version of
`docker-compose.yml` - it hardcoded `OLLAMA_HOST=http://ollama:11434` in
the `app` service's `environment:` block, which silently overrode whatever
was in `.env` (Compose's `environment:` always wins over `env_file:`).
Fixed by removing that override so `.env` is the single source of truth
(CLAUDE.md rule 22) - pull the latest `docker-compose.yml` if you still hit
this.

## `POST /generate` returns 503 with an OpenSCAD compiler error

The service already retries once, feeding the compiler error back to the LLM
to fix. A second failure means the LLM couldn't produce valid OpenSCAD for
that prompt - try rephrasing the prompt to be more specific about the shape
(dimensions, primitive composition), or, if it's a common part, consider
adding a deterministic template for it in
`src/threedprompt/openscad_generator.py` (see `_TEMPLATE_KEYWORDS`) so it
never needs the LLM at all.

## `POST /generate` for a complex/organic prompt produces a crude or wrong shape

Expected behavior, not a bug - see the "Known limitation" note in
`README.md`. There is no deterministic Blender template; result quality is
bounded by how well the configured LLM can write `bpy` code for that prompt.
Try a more capable model (either a larger local Ollama model, or
`LLM_PROVIDER=claude`) if quality matters more than cost for that request.

## `POST /models/{id}/thicken` falls back to mesh-shell when you expected regeneration

This is by design: regeneration from source only works when the model's
`spec.json` records `source_kind: "openscad_scad"` and a
`wall_thickness_param`, and that `.scad` file still exists on disk. A
Blender-sourced model, a hand-edited `.scad` file with a renamed thickness
variable, or a missing/moved source file all fall back to mesh-shelling
automatically (see `src/threedprompt/main.py`,
`thicken_existing_model`) - no error, just a different (still valid) method.

## Mesh-shell thickening produces a self-intersecting or broken mesh

Blender's Solidify modifier can misbehave on thin or non-manifold input
meshes. `mesh_shell()` (`src/threedprompt/thickness.py`) already enables
"even offset" and "quality normals" to reduce this, but a badly non-manifold
upload may need repair first (e.g. Blender's own 3D Print Toolbox /
"Make Manifold" add-on) before it can be shelled cleanly.

## Backups & data durability

`OUTPUT_DIR` (a plain directory, `./output` by default / a Docker named
volume in Compose) is the only place generated models live - there is no
database and currently no automated backup. If you need durability beyond
local disk, back up that directory/volume yourself
(`docker run --rm -v threedprompt_output:/data -v $(pwd):/backup alpine tar
czf /backup/output-backup.tar.gz -C /data .`) before any operation that
might disrupt the volume (host rebuild, `docker compose down -v`, etc.).
