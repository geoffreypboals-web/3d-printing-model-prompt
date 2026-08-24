# Changelog

All notable changes to this project are documented here. Format loosely
follows [Keep a Changelog](https://keepachangelog.com/), versioning follows
[SemVer](https://semver.org/) once there's a tagged release.

## [Unreleased]

### Added

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
