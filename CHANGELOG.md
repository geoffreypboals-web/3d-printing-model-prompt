# Changelog

All notable changes to this project are documented here. Format loosely
follows [Keep a Changelog](https://keepachangelog.com/), versioning follows
[SemVer](https://semver.org/) once there's a tagged release.

## [Unreleased]

### Added (FreeCAD backend: wall-thickness, STEP, solid healing)

- FreeCAD (`freecad-python3` apt package, headless via `freecadcmd`) added
  as a third CAD backend alongside OpenSCAD/Blender - Blender remains the
  default/fallback for organic and mesh-only models; FreeCAD is tried
  first only where it has a real advantage (solid B-rep operations).
- `thickness.mesh_shell()` now tries FreeCAD's Part Thickness first for
  `.stl` input, falling back to Blender's Solidify automatically if
  FreeCAD is unavailable or the input isn't solid enough - no API change,
  existing `/thicken` endpoints are unaffected.
- `POST /step/upload`, `POST /models/{model_id}/export-step` - convert a
  STEP solid to/from this service's mesh pipeline (a best-effort B-rep
  wrap of the mesh, not true reverse-engineered parametric CAD).
- `POST /models/{model_id}/repair-solid` - heals malformed B-rep topology
  via OCCT's ShapeFix, a different class of repair than `/repair`'s
  Blender-based open-boundary hole filling.
- `GET /health` now reports `freecad_available` (doesn't affect
  `status`/degraded - FreeCAD absence only disables its own endpoints).
- `docs/adr/0005-freecad-third-cad-backend.md` records the decision and
  the real semantic limitations found during development (Part Thickness
  needs an opening face, unlike Blender's Solidify; `Part.export()` on a
  bare shape silently omits all solid geometry - use `shape.exportStep()`;
  STEP import only works via `Part.read()`, not `Part.insert`/`Import.*`).

### Added (watertight analysis/repair)

- `POST /watertight/upload`, `POST /models/{model_id}/analyze`,
  `POST /models/{model_id}/repair`, `GET /models/{model_id}/viewer.glb` -
  finds boundary-edge holes and inverted-normal ("wrinkle") defects on a
  mesh via headless Blender, classifies each hole as a likely intentional
  opening (a cup's mouth, an open box top, an open base underside) vs. a
  likely unintentional defect via a pure-Python geometric heuristic
  (`src/threedprompt/hole_classifier.py`), and lets the caller close a
  chosen subset and re-check watertightness.
- Browser 3D viewer at `/watertight.html` (three.js, vendored locally -
  see `src/threedprompt/static/vendor/three/`) - upload a model, see every
  flagged hole as a color-coded clickable marker on the actual mesh, pick
  which to close, download the repaired file.
- `docs/adr/0004-watertight-hole-detection-and-repair.md` records the
  design (heuristic vs. ML classification, why hole ids are positional,
  the Blender/glTF axis-conversion and OrbitControls-singularity bugs
  found and fixed during development).

### Changed

- Default `MAX_UPLOAD_BYTES` raised from 50MB to 300MB - dense/scanned
  STL meshes routinely exceeded the old default. Still configurable via
  `.env` for files larger than that.
- `POST /thicken` (arbitrary file upload) now returns the thickened STL
  file directly instead of JSON, so uploading and getting the modified
  file back is one round trip - usable directly from Swagger UI's
  "Execute" -> "Download file". The new model_id and method are now
  carried as `X-Model-Id` / `X-Thicken-Method` response headers instead of
  JSON fields. `POST /models/{model_id}/thicken` is unchanged (still JSON).

### Added

- Minimal browser UI served at `/` (`src/threedprompt/static/index.html`) -
  generate a model from a prompt, or pick a local STL/OBJ file, increase
  its wall thickness, and download the result, all from a real file picker
  without needing curl or the Swagger docs.
- Initial service: `POST /generate` classifies a prompt (hybrid
  heuristic/LLM router) and generates a model via OpenSCAD (simple/
  parametric parts) or headless Blender (complex/organic shapes).
- `POST /models/{model_id}/thicken` and `POST /thicken` to increase wall
  thickness, preferring OpenSCAD-source regeneration and falling back to a
  Blender Solidify mesh shell.
- `GET /health` dependency status check; `GET /models/{model_id}/download`.
- LLM backend abstraction supporting local Ollama (default, no-cost) and the
  Claude API (opt-in, paid).
- Docker/Compose setup bundling OpenSCAD, Blender, and a local Ollama
  service.
- Test suite covering the classifier, both generation backends, both
  thickening strategies, storage, and the HTTP API (all CAD/LLM calls
  mocked).
