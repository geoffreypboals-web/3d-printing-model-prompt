# 3D Printing Model Prompt

A toolkit for preparing 3D models for printing. The first feature in this
repository is **watertight analysis & repair**: point it at a mesh (an STL
export from a generator, a downloaded model, whatever), and it uses
[Blender](https://www.blender.org/) headless to find places where the
surface isn't actually a sealed solid — small gaps left by misaligned
vertices, and patches of inverted-normal geometry — before you find out the
hard way in a slicer or on the printer.

It doesn't just flag every open edge, though: a model can *legitimately*
have openings (the mouth of a cup, the open top of a box, the open
underside of a stand/base), and those should stay open. So each detected
hole gets classified — **likely a deliberate opening** vs. **likely an
unintentional defect** — with a plain-language reason, and a browser
viewer lets you look at the flagged regions on the actual model, in 3D,
and decide which ones to close.

## How it works

```
mesh file (.stl/.obj/.ply/.glb/.gltf/.fbx)
        │
        ▼
 src/mesh_repair/service.py  ──▶  blender --background --python
        │                          blender_scripts/analyze_watertight.py
        │                          (finds boundary-edge "holes" + inverted-
        │                           normal islands via bmesh)
        ▼
 src/mesh_repair/classifier.py   (pure-Python heuristics: size relative to
        │                         the model, position relative to the
        │                         bounding box, planarity, boundary
        │                         "raggedness" → intentional / defect /
        │                         ambiguous, with a confidence + reason)
        ▼
 viewer/index.html (three.js)    (color-coded markers on the actual mesh;
        │                         click one, or its row in the side panel,
        │                         to mark it "close this")
        ▼
 src/mesh_repair/service.py  ──▶  blender --background --python
                                   blender_scripts/close_holes.py
                                   (fills only the chosen holes, recalculates
                                   normals across the whole shell, re-checks
                                   watertightness, exports the result)
```

`src/mesh_repair/api.py` (FastAPI) wires this into a small local web app —
upload a model, see the flagged regions, click to choose, download the
repaired file.

## Quick start (Docker)

```bash
docker build -t mesh-repair .
docker run --rm -p 8000:8000 mesh-repair
```

Then open http://localhost:8000 in a browser, upload a mesh, and click
**Upload & Analyze**.

> The Docker build itself wasn't runnable in the sandbox this was
> developed in (no Docker daemon access there), though every command it
> runs (`apt-get install blender python3-numpy`, `pip install -r
> requirements.txt`) was verified directly on the same Debian-family base
> image. Build and smoke-test it before relying on it in production.

## Quick start (without Docker)

Requires Python 3.11+ and [Blender](https://www.blender.org/download/)
(this project shells out to the `blender` binary — see
[docs/adr/0001-blender-subprocess-not-bpy-library.md](docs/adr/0001-blender-subprocess-not-bpy-library.md)
for why). On Debian/Ubuntu:

```bash
sudo apt-get install blender python3-numpy   # numpy: see TROUBLESHOOTING.md
pip install -r requirements.txt
PYTHONPATH=src uvicorn mesh_repair.api:app --host 0.0.0.0 --port 8000
```

Open http://localhost:8000.

### Using it as a library instead of the web app

```python
import sys
sys.path.insert(0, "src")
from mesh_repair import analyze_mesh, repair_mesh

report = analyze_mesh("trophy.stl")
print(report.is_watertight, len(report.holes))
for hole in report.holes:
    print(hole.id, hole.classification.value, hole.confidence, hole.reason)

# Close only the ones classified as defects, leave the rest alone:
defect_ids = [h.id for h in report.holes if h.classification.value == "likely_defect"]
result = repair_mesh("trophy.stl", defect_ids, "trophy_repaired.stl")
print(result.is_watertight)
```

## Configuration

| Env var                 | Default            | Purpose                                                   |
|--------------------------|---------------------|-------------------------------------------------------------|
| `BLENDER_BINARY`         | `blender` (on PATH) | Path to the Blender executable                              |
| `MESH_REPAIR_DATA_DIR`   | `data/uploads`      | Where uploaded meshes / generated GLB previews are stored    |

No secrets/API keys are involved — everything runs locally against the
Blender binary on the machine/container.

## AI/LLM usage

None, by design. Hole classification is deterministic geometry (size
relative to the model, position relative to the bounding box, planarity,
boundary shape) rather than a model call — it's free to run, doesn't need
Ollama or any other backend, and its reasoning is inspectable in
`src/mesh_repair/classifier.py` instead of opaque. If a future feature in
this project genuinely needs an LLM at runtime, follow CLAUDE.md rule 6:
put it behind a provider abstraction with Ollama as the no-cost default.

## Licensing

This project is intended as open source; **`LICENSE` is MIT** (the default
per CLAUDE.md rule 19 — flagging it here for confirmation, since anything
other than MIT needs an explicit decision).

Runtime dependencies and their licenses:

| Dependency | License | Notes |
|---|---|---|
| [FastAPI](https://fastapi.tiangolo.com/) | MIT | API framework |
| [Uvicorn](https://www.uvicorn.org/) | BSD-3-Clause | ASGI server |
| [python-multipart](https://github.com/Kludex/python-multipart) | Apache-2.0 | File upload parsing |
| [three.js](https://threejs.org/) r0.160.0 (vendored in `viewer/vendor/`) | MIT | 3D viewer |
| [Blender](https://www.blender.org/) | **GPL-2.0-or-later** | **Not linked into this project** — invoked as an external subprocess (like calling out to ffmpeg), so this project's own code stays under its own license. Blender itself must be separately installed by whoever runs this project (the Dockerfile does this via `apt-get`), and if you redistribute the *container image*, you're redistributing GPL software and should keep that license's obligations (source availability, etc.) in mind for the Blender component specifically. |

No paid/proprietary services are used or required.

## Project layout

```
src/mesh_repair/
  models.py                 # WatertightReport/Hole/RepairResult dataclasses
  classifier.py              # pure-Python "intentional opening vs defect" heuristics
  service.py                 # subprocess wrapper around the Blender scripts (the public API)
  api.py                      # FastAPI app backing the viewer
  blender_scripts/
    _shared.py                # import/join/hole-grouping logic shared by both scripts below
    analyze_watertight.py      # runs inside Blender: finds holes + inverted-normal islands
    close_holes.py             # runs inside Blender: caps chosen holes, re-checks watertightness
viewer/
  index.html                  # three.js viewer (upload, analyze, click-to-close, download)
  vendor/three/                # locally-vendored three.js (see viewer/vendor/README.md)
tests/
  test_classifier.py           # pure-Python unit tests (no Blender needed)
  test_service_integration.py  # end-to-end tests against a real Blender install
docs/adr/                     # architecture decision records
```

## Development

```bash
pip install -r requirements.txt -r requirements-dev.txt
ruff check src/ tests/
ruff format src/ tests/
pytest tests/ -v
```

The Blender-dependent tests in `tests/test_service_integration.py` skip
themselves automatically if no `blender` binary is on `PATH`.

See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for known issues and their
fixes, and [docs/adr/](docs/adr/) for why this project is built the way it
is.

## What's not built yet

This repository's coding standards (`CLAUDE.md`) call for a
`CONTRIBUTING.md` once the project is ready for outside contributions —
it isn't yet, so that file doesn't exist. A CI pipeline
(`.github/workflows/ci.yml`) runs lint + tests on every push.
