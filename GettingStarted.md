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
- converts STEP to/from meshes and heals B-rep solids via FreeCAD
  (`/step/upload`, `/models/{id}/export-step`, `/models/{id}/repair-solid`);
- renders PNG thumbnails (`POST /thumbnail`) and suggests library tags
  (`POST /tags/suggest`) for 3dPrinterWorkshopManager, plus local
  speech-to-text (`POST /transcribe`);
- builds casting molds in four modes (`silicone_block`, `direct_cast`,
  `form_fitting`, `hollow_cast`) plus a draft/undercut check
  (`/models/{id}/mold`, `POST /mold`, `/models/{id}/mold/draft-check`).

Uploads accept `.stl`, `.obj` and Rhino `.3dm` (the watertight upload also
takes `.ply .glb .gltf .fbx`).

## Prerequisites

- Docker Desktop (Windows/Mac) or Docker Engine + Compose plugin (Linux).
  The image bundles OpenSCAD, Blender and FreeCAD.
- An LLM backend for `/generate`: the bundled Ollama container (default,
  free) or `LLM_PROVIDER=claude` with `ANTHROPIC_API_KEY` (paid). Thicken,
  watertight and mold features don't use the LLM.

## Quick start (Docker)

Replace `<path-to>` with where you cloned the repo. Leave `OLLAMA_HOST`
unset in `.env` - `docker-compose.yml` then points the app at the bundled
`ollama` service (`http://ollama:11434`).

Linux/Mac:

```bash
cd <path-to>/3d-printing-model-prompt && cp .env.example .env && docker compose up --build -d && docker compose exec ollama ollama pull llama3.1
```

Windows (PowerShell):

```powershell
cd <path-to>d-printing-model-prompt; copy .env.example .env; docker compose up --build -d; docker compose exec ollama ollama pull llama3.1
```

Then open `http://localhost:8000/` (browser UI), `/watertight.html`
(hole viewer) or `/docs` (interactive API). `GET /health` reports whether
OpenSCAD, Blender, FreeCAD and the LLM are reachable.

Already running Ollama on the host? Set
`OLLAMA_HOST=http://host.docker.internal:11434` (or your own port) in `.env`
and start only the app (Linux/Mac, from the repo folder):
`cd <path-to>/3d-printing-model-prompt && docker compose up --build -d --no-deps app`.
Windows (PowerShell):
`cd <path-to>d-printing-model-prompt; docker compose up --build -d --no-deps app`.
More: [docs/AI_SETUP.md](docs/AI_SETUP.md), [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Tests and lint

```bash
cd <path-to>/3d-printing-model-prompt && pip install -r requirements-dev.txt && pip install -e . && pytest -q && ruff check src tests && black --check src tests
```

CAD/LLM calls are mocked; the `*_integration` tests only run when a real
`blender` is on `PATH`.

## Troubleshooting

See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) (missing numpy in Blender,
Ollama port conflicts, viewer marker offsets, backups of `OUTPUT_DIR`).
