# Troubleshooting

Living log of non-obvious issues and their fixes for
`/home/user/3d-printing-model-prompt`. Add to this whenever a real bug or
config problem gets solved, so it doesn't get re-debugged from scratch.

## `POST /thicken` (or the browser UI's "Thicken & download") fails with "upload exceeds MAX_UPLOAD_BYTES"

The uploaded file is larger than the configured cap. The default was
raised from 50MB to 300MB (dense/scanned STL meshes routinely exceed
50MB), but any specific file can still be bigger than that. Set
`MAX_UPLOAD_BYTES` in `.env` (in bytes) to whatever comfortably covers
your files - e.g. `MAX_UPLOAD_BYTES=524288000` for 500MB - then restart:
`docker compose up --build --no-deps app` (or `docker compose up --build`
if using the bundled Ollama service). Note the whole upload is currently
buffered in memory before being written to disk, so keep this within your
container's available RAM rather than setting it arbitrarily high.

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

## `GET /health` reports `openscad_available: false`, `blender_available: false`, or `freecad_available: false`

The binaries aren't on `PATH` inside the environment running the service.
Unlike OpenSCAD/Blender, `freecad_available: false` does **not** degrade
`GET /health`'s overall `status` - Blender remains the required fallback
for wall-thickness shelling, so FreeCAD's absence only disables
`/step/upload`, `/models/{id}/export-step`, and `/models/{id}/repair-solid`
(each returns its own 503).

- **Docker**: shouldn't happen - the Dockerfile installs all three via
  `apt-get`. If it does, rebuild with
  `docker build --no-cache -t threedprompt .`
  (`/home/user/3d-printing-model-prompt/Dockerfile`).
- **Local (no Docker)**: install OpenSCAD/Blender/FreeCAD yourself, or set
  `OPENSCAD_BINARY` / `BLENDER_BINARY` / `FREECAD_BINARY` in `.env`
  (`/home/user/3d-printing-model-prompt/.env.example`) to their full
  paths. FreeCAD's apt package (`freecad-python3`) installs its headless
  CLI as `/usr/bin/freecadcmd`, not `/usr/bin/freecad` - that name is the
  full `freecad` GUI package, which this project deliberately doesn't
  install (much larger, and the headless CLI is all this service needs).

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

## `ModuleNotFoundError: No module named 'numpy'` from a watertight-check/repair call

**Symptom:** `POST /models/{model_id}/analyze` or `/repair` fails with a
`WatertightError` whose message is a Python traceback from
`io_scene_gltf2/blender/exp/gltf2_blender_gather_tree.py` ending in
`ModuleNotFoundError: No module named 'numpy'`.

**Cause:** the Debian/Ubuntu `apt` package for Blender links against the
*system* Python rather than bundling its own the way the official
blender.org downloads do. Its built-in glTF export addon (used for the
`viewer.glb` preview `watertight.py` produces alongside every analysis/
repair) imports numpy at export time. `thickness.py`'s mesh-shell path
never hits this because it only exports STL, never glTF.

**Fix:** install numpy for that same system Python:
`sudo apt-get install python3-numpy` (already in the Dockerfile). If
Blender was installed a different way (a blender.org tarball, snap,
flatpak), it likely bundles its own Python and this shouldn't apply.

## A hole id from an earlier `/analyze` response 404s or "not found"s on `/repair`

Hole ids are **positional**, not stable identifiers - they come from the
order `blender_scripts/_shared.group_boundary_edges()` encounters boundary
edges in bmesh's own edge index order for that specific file's state.
Closing some holes renumbers whatever's left. `main.py`'s `/repair` route
already re-analyzes after every repair so the browser viewer's cached ids
stay correct automatically; a script calling the API directly needs to
call `/analyze` again before reusing an id from an older response.

## A real intentional opening (cup mouth, open box top) gets flagged as a defect, or vice versa

`hole_classifier.py`'s classification is a heuristic, not a certainty -
see `docs/adr/0004-watertight-hole-detection-and-repair.md` for what it
weighs and why. Treat it as a strong hint, not a verdict, and use the
`/watertight.html` viewer to confirm before closing anything on a model
you care about - that's exactly why every hole's reason/confidence is
shown rather than auto-deciding.

## Markers in the watertight viewer appear in the wrong place, or nowhere near the model

The analysis JSON reports coordinates in the mesh's own space, but the
viewer's GLB preview is exported with Blender's default glTF "+Y up" axis
conversion. `static/watertight.html`'s `pointToGltf()`/`bboxToGltf()`
convert every point/bbox from the API before placing it in the scene - if
a future change adds a new place that consumes `hole.centroid`,
`island.centroid`, or `bounding_box` from the API, route it through those
same helpers first.

## `POST /models/{id}/export-step` or `/step/upload` fails with "Null input shape" / an OCCError

**If it happens on export** (mesh -> STEP): the source mesh probably
isn't a closed/manifold solid once loaded into FreeCAD - try
`/models/{id}/repair-solid` first, or fall back to a Blender-repaired
mesh via `/repair`, then retry the export.

**If it happens on import** (STEP -> mesh) and you're looking at a STEP
file produced by something other than this service's own
`/models/{id}/export-step`: check the file actually has a
`MANIFOLD_SOLID_BREP` entity in its `DATA;` section (open it as text - it's
plain ASCII). A real, non-empty solid export from FreeCAD/OCCT-based
tools always has one; a file with only header/placement entities and no
geometry will always fail to import, and that's a defect in how the file
was produced, not in this service's importer. This was a real bug found
during development: `Part.export([shape], path)` (the module-level
function, given a bare `Part.Shape`/`Solid` rather than a document
object) silently writes a STEP file missing all solid geometry - fixed by
switching to `shape.exportStep(path)` (the shape's own method) in
`freecad_scripts/step_export.py`. If you ever add a second STEP-export
call site, use `.exportStep()`, not `Part.export()`.

## FreeCAD subprocess calls always report exit code 0, even on failure

Not a bug - confirmed behavior of FreeCADCmd 1.0.0 (Debian's
`freecad-python3` package): the process exit code stays 0 regardless of
whether the script inside actually succeeded. `freecad_cad.py` never
trusts the exit code - it judges success purely by whether the script's
`--report` JSON file exists and has `"ok": true` (same pattern
`watertight.py` already uses for Blender's own less-reliable-than-you'd-
hope exit codes). If you add a new FreeCAD script, follow the same
pattern: write a JSON report, don't rely on `sys.exit()`/return code.

FreeCADCmd also prints an unrelated Python traceback to stderr on every
invocation, complaining it failed to auto-open one of the script's own
`--input`/`--output`/`--report` argument values as a FreeCAD document.
This is cosmetic - confirmed harmless, the actual script still runs to
completion regardless - and shows up in logs at `warning` level only
when the (irrelevant) exit code is nonzero; otherwise it's silent.

## A new FreeCAD script's `--input`/`--output` args come back wrong or missing

FreeCADCmd, unlike Blender, does **not** strip its own arguments before
handing off to the script - `sys.argv` inside a script invoked as
`freecadcmd script.py -- --input X --output Y` is still
`[freecadcmd_path, script_path, "--", "--input", "X", ...]`, not just
`["--input", "X", ...]` the way Blender's `--python script.py --`
convention leaves it. Every script under `freecad_scripts/` goes through
`_shared.parse_freecad_args()`, which slices past the literal `--`
itself before handing off to `argparse` - always route a new script's
argument parsing through that helper rather than reading `sys.argv`
directly.

## `POST /thumbnail` returns 422 for a `.3mf` or `.amf` file

Both are deliberately unsupported by this endpoint, not a bug.
`.3mf` files carry their own embedded slicer-preview PNG - extract that
directly (the farm-manager sibling repo's `libraryAssets.ts` already
does) rather than paying for a fresh Blender render of a re-triangulated
mesh. `.amf` has no importer in either Blender or FreeCAD's Mesh module,
so there's no path to a thumbnail for it at all today.

## `POST /thumbnail` on a `.step`/`.stp` file returns 503 even though `blender_available: true`

STEP thumbnails go through **both** backends: FreeCAD converts the file
to a mesh first (`freecad_cad.step_to_mesh()`), then Blender renders that
mesh. Check `GET /health`'s `freecad_available` field too - a STEP
thumbnail fails if either binary is missing, not just Blender.

## A thumbnail renders as a flat gray silhouette with no shading definition

Expected, not a bug - `render_thumbnail.py` uses Workbench's `'MATERIAL'`
color mode, which falls back to Blender's default gray when the mesh has
no material (most bare STL/STEP input). An OBJ with a `.mtl` beside it
picks up real colors instead. If you want every thumbnail to render
identically regardless of material, that's `'SINGLE'` color mode - not
currently exposed as an option, would need a new `--color-mode` arg on
the script if a caller wants it.

## Backups & data durability

`OUTPUT_DIR` (a plain directory, `./output` by default / a Docker named
volume in Compose) is the only place generated models live - there is no
database and currently no automated backup. If you need durability beyond
local disk, back up that directory/volume yourself
(`docker run --rm -v threedprompt_output:/data -v $(pwd):/backup alpine tar
czf /backup/output-backup.tar.gz -C /data .`) before any operation that
might disrupt the volume (host rebuild, `docker compose down -v`, etc.).
