# 3d-printing-model-prompt — Requirements

**Purpose of this document:** a living reference for what this project is,
what's built, and what's still open — so a future AI session (or human)
doesn't have to re-derive context from scratch. Update it whenever a
requirement is completed or newly scoped. Last checked against the code:
2026-09-27.

## What this project is

A Dockerized FastAPI service for 3D-printing model work: prompt-to-STL
generation (OpenSCAD for mechanical parts, headless Blender for organic
shapes, hybrid heuristic/LLM routing), wall thickening, watertight
analysis/repair, and casting-mold generation. The LLM sits behind
`src/threedprompt/llm_client.py` — Ollama by default (free, local), Claude
API opt-in (`LLM_PROVIDER=claude`). Sibling suite projects
(e.g. 3dPrinterWorkshopManager) can call it over HTTP.

## Completed requirements

- [x] `POST /generate` with hybrid classifier (`classifier.py`), built-in
  OpenSCAD templates, LLM-authored OpenSCAD and `bpy` scripts
  (`openscad_generator.py`, `blender_generator.py`) — ADRs 0001, 0002.
- [x] Wall thickening: OpenSCAD source regeneration, Blender Solidify
  fallback, optional QuadriFlow retopology (`quad_target_faces`) —
  ADR 0003.
- [x] Watertight hole detection/classification/repair plus flipped-normal
  detection and a three.js viewer (`watertight.py`, `hole_classifier.py`,
  `static/watertight.html`) — ADR 0004.
- [x] Rhino `.3dm` input via a vendored MIT `import_3dm` reader.
- [x] Mold generation, four modes, auto-repair before generation,
  draft/undercut check, configurable parting axis/offset, cavity volume +
  mass estimate, geometry-aware vent placement (`mold.py`,
  `draft_analysis.py`) — ADRs 0005–0011.
- [x] Browser UI at `/`, interactive API docs at `/docs`, `GET /health`.
- [x] Dockerfile (non-root user, OpenSCAD + Blender + rhino3dm) and
  `docker-compose.yml` with a bundled Ollama service.
- [x] Request caps (`MAX_PROMPT_LENGTH`, `MAX_UPLOAD_BYTES`,
  `MAX_WALL_THICKNESS_MM`, `MAX_MOLD_DIMENSION_MM`), CAD subprocess
  timeout, response caching (`CACHE_ENABLED`).
- [x] Tests (pytest, CAD/LLM mocked; Blender integration tests
  auto-skip), CI workflow (ruff, black, pytest, Docker build), MIT
  `LICENSE`, `CHANGELOG.md`, `TROUBLESHOOTING.md`.

## Open development tasks

1. **CI lint is red on `main`** — ruff/black scan the vendored
   `rhino3dm_reader`. Fix exists on the unmerged
   `chore/ci-vendor-lint-and-archive-tools` branch; merge it.
2. **Unmerged `feature/freecad-backend` branch** (3 commits, incl.
   `POST /tags/suggest`) — review and merge or close.
3. Browser UI file pickers don't accept `.3dm` yet (API does).
4. `.scratch_moldtest/` debug scripts are committed at the repo root —
   decide whether to keep them (and give them file headers) or remove
   and `.gitignore` them.
5. No `CONTRIBUTING.md` yet — deliberately deferred until the project is
   opened to outside contributors (rule 19).
6. Organic-shape quality depends on the LLM; a local Ollama model gives
   cruder Blender geometry than a hosted model (documented tradeoff, see
   README "Known limitation").

See `Project-Audit-Report.md` for the dated audit log (its 2026-08-17
entry predates any code) and `CHANGELOG.md` for shipped detail.
