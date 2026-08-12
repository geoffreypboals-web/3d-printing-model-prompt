# 3d-printing-model-prompt

**Mesh Prompt Interviewer** — a small local Docker app that interviews you (via a local Ollama model) about the 3D model you want, then writes a ready-to-use text-to-3D prompt (Meshy / Tripo3D / Rodin style) and renders a preview image of it.

## How it works

1. Open the app in your browser. A local Ollama model interviews you one question at a time — subject, purpose (print vs. game asset vs. CAD, etc.), style, scale, topology needs, materials, orientation, level of detail, output format, and constraints.
2. Once it has enough information, it synthesizes a final structured prompt: a dense descriptive `prompt`, a `negativePrompt`, and a handful of `styleTags` — the format consumer text-to-3D generators expect.
3. Click **Render preview image** to generate a preview of that prompt, either as 2D concept art from a local Stable Diffusion instance, or (if configured) as a real mesh render from Meshy or Tripo3D.
4. Copy the final prompt into your text-to-3D tool of choice, or use the downloaded mesh/image directly if you rendered via Meshy/Tripo3D.

## Prerequisites

- Docker and Docker Compose.
- [Ollama](https://ollama.com) running locally with a chat-capable model pulled, e.g.:
  ```
  ollama pull llama3
  ```
  (a stronger instruction-following model, e.g. `llama3.1`, is worth pulling later if you see malformed replies — see Configuration below)
- Optional, for local image rendering: a Stable Diffusion WebUI (AUTOMATIC1111 or compatible) running locally with its API enabled (`--api` flag), or a Meshy/Tripo3D API key for real mesh renders instead.

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
- `src/routes/` — Express API routes consumed by the frontend in `public/`.
