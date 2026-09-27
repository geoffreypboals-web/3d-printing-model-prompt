# 3d-printing-model-prompt — Getting Started

Short quick start. [README.md](README.md) is the full reference (every
endpoint, mold modes, LLM backends, licensing).

## What it does

A FastAPI service (`src/threedprompt/`) that:

- turns a text prompt into a printable STL (`POST /generate`) — OpenSCAD
  for simple/mechanical parts, headless Blender for organic shapes, chosen
  by a keyword heuristic with an LLM fallback;
- increases wall thickness of a generated or uploaded model
  (`/models/{id}/thicken`, `POST /thicken`);
- checks a model for holes/flipped normals and repairs chosen holes
  (`/watertight/upload`, `/models/{id}/analyze`, `/models/{id}/repair`,
  browser viewer at `/watertight.html`);
- builds casting molds in four modes (`silicone_block`, `direct_cast`,
  `form_fitting`, `hollow_cast`) plus a draft/undercut check
  (`/models/{id}/mold`, `POST /mold`, `/models/{id}/mold/draft-check`).

Uploads accept `.stl`, `.obj` and Rhino `.3dm` (the watertight upload also
takes `.ply .glb .gltf .fbx`).

## Prerequisites

- Docker Desktop (Windows/Mac) or Docker Engine + Compose plugin (Linux).
  The image bundles OpenSCAD and Blender.
- An LLM backend for `/generate`: the bundled Ollama container (default,
  free) or `LLM_PROVIDER=claude` with `ANTHROPIC_API_KEY` (paid). Thicken,
  watertight and mold features don't use the LLM.

## Quick start (Docker)

Linux/Mac:

```bash
cd /home/user/3d-printing-model-prompt && cp .env.example .env
# edit .env: OLLAMA_HOST=http://ollama:11434
cd /home/user/3d-printing-model-prompt && docker compose up --build
cd /home/user/3d-printing-model-prompt && docker compose exec ollama ollama pull llama3.1
```

Windows (PowerShell), from wherever you cloned the repo:

```powershell
cd <path-to>\3d-printing-model-prompt; copy .env.example .env
# edit .env: OLLAMA_HOST=http://ollama:11434
cd <path-to>\3d-printing-model-prompt; docker compose up --build
```

Then open `http://localhost:8000/` (browser UI), `/watertight.html`
(hole viewer) or `/docs` (interactive API). `GET /health` reports whether
OpenSCAD, Blender and the LLM are reachable.

Already running Ollama natively? Set
`OLLAMA_HOST=http://host.docker.internal:11434` and start only the app:
`docker compose up --build --no-deps app`.

## Tests and lint

```bash
cd /home/user/3d-printing-model-prompt && pip install -r requirements-dev.txt && pip install -e . && pytest -q
cd /home/user/3d-printing-model-prompt && ruff check src tests && black --check src tests
```

CAD/LLM calls are mocked; the `*_integration` tests only run when a real
`blender` is on `PATH`. Note: ruff/black currently fail on `main` because
they scan the vendored `.3dm` reader — see README "Running locally".

## Troubleshooting

See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) (missing numpy in Blender,
Ollama port conflicts, viewer marker offsets, backups of `OUTPUT_DIR`).
