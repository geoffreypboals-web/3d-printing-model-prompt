# Troubleshooting

Living log of non-obvious issues and their fixes for
`/home/user/3d-printing-model-prompt`. Add to this whenever a real bug or
config problem gets solved, so it doesn't get re-debugged from scratch.

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
