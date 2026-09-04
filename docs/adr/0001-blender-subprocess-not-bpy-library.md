# 0001: Invoke Blender as a subprocess, not as a `bpy` library import

Date: 2026-09-04

## Context

The watertight-analysis-and-repair feature needs real mesh-processing
capability (boundary-edge/hole detection, normal-consistency checks,
hole filling) that isn't worth reimplementing from scratch. Blender
ships exactly this via its Python API (`bpy`/`bmesh`). There are two ways
to use it from this project:

1. **`import bpy` directly** in the project's own Python process — either
   by running everything *inside* Blender's bundled Python interpreter,
   or via the `bpy` PyPI package (Blender Foundation's officially
   published pip-installable build of the same API) for some Blender
   versions.
2. **Shell out to the standalone `blender` executable** in
   `--background --python <script>` mode, as a subprocess, and exchange
   data as files (the mesh in, JSON reports and repaired meshes out).

## Decision

Use option 2: `src/mesh_repair/service.py` invokes `blender` as a
subprocess; the actual `bpy`/`bmesh` code lives only in
`src/mesh_repair/blender_scripts/*.py`, which are never imported by the
rest of the project and only run inside Blender's own interpreter.

## Why

- **Licensing (CLAUDE.md rule 7).** Blender is GPL-2.0-or-later.
  Importing `bpy` as a library links GPL code into this project's own
  process, which is the situation GPL's copyleft is most concerned with
  for a combined work. Invoking the standalone `blender` binary as an
  arms-length external tool (the same pattern as shelling out to
  `ffmpeg` or `imagemagick`) keeps this project's own code under its own
  license; only the Blender *binary itself* carries GPL obligations, and
  it's never bundled into this project's source — see the licensing
  table in `README.md`.
- **Environment reality, confirmed directly.** The distro `blender`
  package (`apt-get install blender`) links against the *system*
  Python rather than bundling its own — its own glTF export addon
  fails with `ModuleNotFoundError: No module named 'numpy'` until
  `python3-numpy` is installed alongside it (see `TROUBLESHOOTING.md`).
  Running Blender as the standalone executable sidesteps having to
  reconcile *this project's* Python/dependency environment with
  whichever Python a given Blender build happens to use — the two stay
  fully separate.
- **Isolation.** A crash or hang inside Blender's mesh processing (both
  are possible with malformed input geometry) can't take down the API
  process; it shows up as a subprocess timeout/non-zero exit that
  `service.py` turns into a `MeshRepairError`, per CLAUDE.md rule 13.
- **Simplicity of the public API.** Callers of `analyze_mesh()`/
  `repair_mesh()` never need Blender importable in their own Python
  environment (only the `blender` executable needs to exist on the
  machine/container) — one less constraint on how this package gets
  used or tested (`tests/test_classifier.py` needs no Blender at all).

## Trade-offs accepted

- **Per-call startup cost.** Launching Blender fresh for every
  analyze/repair call costs roughly a second of startup overhead versus
  a long-lived in-process Blender session. Not a concern at this
  project's scale (interactive, one-model-at-a-time use); worth
  revisiting if this ever needs to process a high-throughput batch queue.
- **Data exchange via files/JSON**, not native Python objects, between
  the subprocess and the caller. `service.py` and
  `blender_scripts/analyze_watertight.py` hand-keep the JSON shape in
  sync with `models.py`'s dataclasses rather than sharing a generated
  schema — documented as a specific troubleshooting trap in both
  files' header comments.

## Related decision: hole classification is a hand-written heuristic, not a model call

`classifier.py` decides "intentional opening vs. likely defect" from
plain geometric features (size relative to the model, position relative
to the bounding box, planarity, boundary raggedness) rather than any
ML/LLM call. This was a deliberate choice, not just the simplest option:
it's free to run (no cost per CLAUDE.md rule 10, no Ollama/API backend
needed per rule 6), fully deterministic and unit-testable without any
model or network dependency (`tests/test_classifier.py` runs in
milliseconds), and its reasoning is directly inspectable/tunable in code
rather than opaque. The trade-off is accuracy on unusual shapes the
heuristic wasn't tuned for — mitigated by always showing the
classification's confidence and reason in the viewer rather than
auto-deciding, and by keeping the "close" action user-driven per the
original design ask, rather than trying to make the tool always right.

## Related decision: vendor three.js locally instead of a CDN `<script>` tag

`viewer/vendor/three/` holds a locally-vendored copy of the exact
three.js files the viewer imports (r0.160.0, MIT-licensed — see
`viewer/vendor/README.md`), rather than an import map pointing at
`cdn.jsdelivr.net` or similar. This was originally going to be a CDN
import map (simpler to keep updated); it was changed after discovering,
during development, that this project's target deployment (Docker,
CLAUDE.md rule 4) shouldn't assume the container can reach an external
CDN at runtime — an air-gapped or firewalled deployment would leave the
viewer's 3D canvas silently blank with no model-side error to explain
why. Vendoring trades a small amount of repo size (~840KB) and a manual
update step (documented in `viewer/vendor/README.md`) for the viewer
working the same way regardless of the deployment's network policy.
