# Changelog

All notable changes to this project are documented here. Format loosely
follows [Keep a Changelog](https://keepachangelog.com/), versioning follows
[SemVer](https://semver.org/) once there's a tagged release.

## [Unreleased]

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
