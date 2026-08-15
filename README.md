# 3d-printing-model-prompt

**Mesh Prompt Interviewer** — a small local Docker app that interviews you (via a local Ollama model) about the 3D model you want, then writes a ready-to-use text-to-3D prompt (Meshy / Tripo3D / Rodin style) and renders a preview image of it.

## How it works

1. Open the app in your browser. A local Ollama model interviews you one question at a time — subject, purpose (print vs. game asset vs. CAD, etc.), style, scale, topology needs, materials, orientation, level of detail, output format, and constraints.
2. Once it has enough information, it synthesizes a final structured prompt: a dense descriptive `prompt`, a `negativePrompt`, and a handful of `styleTags` — the format consumer text-to-3D generators expect.
3. Click **Render preview image** to generate a preview of that prompt, either as 2D concept art from a local Stable Diffusion instance, or (if configured) as a real mesh render from Meshy or Tripo3D.
4. Copy the final prompt into your text-to-3D tool of choice, or use the downloaded mesh/image directly if you rendered via Meshy/Tripo3D.
5. If you rendered via Meshy/Tripo3D (a real `.glb` mesh, not just a 2D image), a **Wall thickness** panel appears: analyze the mesh's current minimum wall thickness, then regenerate it with a new target thickness, choosing whether to preserve the inside dimensions (material added outward) or the outside dimensions (material added inward, center void shrinks). See [Wall thickness](#wall-thickness) below.

## Prerequisites

- Docker and Docker Compose.
- [Ollama](https://ollama.com) running locally with a chat-capable model pulled, e.g.:
  ```
  ollama pull llama3
  ```
  (a stronger instruction-following model, e.g. `llama3.1`, is worth pulling later if you see malformed replies — see Configuration below)
- Optional, for local image rendering: a Stable Diffusion WebUI (AUTOMATIC1111 or compatible) running locally with its API enabled (`--api` flag), or a Meshy/Tripo3D API key for real mesh renders instead.
- Nothing extra needed for wall-thickness editing — Blender runs headless inside the container image (built in automatically; it's why the image is noticeably larger than a bare Node image).

## Run it

1. Copy the env file and adjust if needed:
   ```
   cp .env.example .env
   ```
   By default this points at Ollama and Stable Diffusion running on your host machine via `host.docker.internal`.
2. Start the app:
   ```
   docker compose up --build
   ```
3. Open <http://localhost:4100>.

Generated images (and meshes, when using Meshy/Tripo3D) are saved to `./output/` on your host machine.

## Configuration (`.env`)

| Variable              | Default                              | Purpose |
|-----------------------|---------------------------------------|---------|
| `PORT`                | `4100`                                | Port the app listens on. |
| `LOCAL_LLM_BASE_URL`  | `http://host.docker.internal:11434`   | Ollama server base URL. |
| `LOCAL_LLM_MODEL`     | `llama3`                              | Ollama model used for the interview and prompt synthesis. |
| `RENDER_PROVIDER`     | `local-sd`                            | `local-sd`, `meshy`, or `tripo`. |
| `SD_BASE_URL`         | `http://host.docker.internal:7860`    | Stable Diffusion WebUI base URL (used when `RENDER_PROVIDER=local-sd`). |
| `MESHY_API_KEY`       | *(empty)*                             | Required when `RENDER_PROVIDER=meshy`. |
| `TRIPO_API_KEY`       | *(empty)*                             | Required when `RENDER_PROVIDER=tripo`. |
| `BLENDER_BIN`         | `blender`                             | Path to the Blender executable, if not on `PATH`. |
| `BLENDER_TIMEOUT_MS`  | `120000`                              | Max time allowed for a single thickness analysis or solidify pass. |

## Wall thickness

Once you have a real generated mesh (Meshy or Tripo3D — the local Stable Diffusion path only produces a 2D image, which has no wall thickness to change), the app can regenerate it with a different minimum wall thickness using Blender running headless inside the container:

- **Analyze current thickness** estimates the thinnest wall in the mesh via a binary search over Blender's 3D Print Toolbox thin-wall check.
- **Regenerate with new thickness** applies a Blender Solidify modifier at your requested thickness, in one of two directions:
  - **Preserve inside dimensions** — new material is added outward, away from center. The original surface becomes an inner wall and the model's outer size grows.
  - **Preserve outside dimensions** — new material is added inward. The outer silhouette stays fixed and the interior void shrinks.
  - Both directions were verified against a known test mesh with a known wall thickness before shipping (see `scripts/blender/`).

**Important caveats:**
- Thickness values assume 1 model unit ≈ 1mm. Text-to-3D generators don't guarantee real-world scale, so treat these numbers as estimates relative to the generated mesh's own units, not calibrated physical measurements — cross-check against your printer/CAD tool before relying on an exact figure.
- This only supports *increasing* thickness (adding material); requesting a target below the detected minimum won't remove material.
- Meshes with pole singularities (many triangles converging at one vertex, as on a UV-sphere-style cap) or other non-manifold topology can produce a spuriously thin reading right at those points — this is an inherent limitation of per-vertex-normal offsetting, verified during development: a plain cube round-trips a 1mm target to an analyzed ~1.0002mm correctly, while a UV-sphere-topology test mesh reported a false near-zero reading at its poles. Typical organic text-to-3D output (dense, irregular meshes) is far less prone to this than primitive-style topology, but treat a surprisingly-low post-regeneration reading as worth a visual check rather than an automatic re-run.

Very high-poly meshes may take a while (or time out — see `BLENDER_TIMEOUT_MS` below) since both the thickness search and the solidify pass run a full geometry analysis per request.

## Local development (without Docker)

```
npm install
npm run dev
```

This runs the server directly with `tsx`, reading the same `.env` file, useful for iterating on the interview prompt or UI without a rebuild.

## Project layout

- `src/prompts/interviewSystemPrompt.ts` — the system prompt driving the Ollama interview.
- `src/prompts/synthesizePrompt.ts` — turns interview answers into the final mesh-generation prompt.
- `src/services/localLlmClient.ts` — thin client for the local Ollama server (chat + generate endpoints).
- `src/services/renderProviders/` — pluggable render backends (`local-sd`, `meshy`, `tripo`).
- `src/services/blenderMesh.ts` — spawns headless Blender to run the scripts below.
- `scripts/blender/solidify.py` — applies the Solidify modifier and renders a preview of the result.
- `scripts/blender/analyze_thickness.py` — binary-searches for the mesh's minimum wall thickness.
- `src/routes/` — Express API routes consumed by the frontend in `public/`.
