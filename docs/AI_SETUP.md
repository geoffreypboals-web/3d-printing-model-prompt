# AI / LLM setup

What uses the LLM (all in `src/threedprompt/`, via `llm_client.py`):

| Feature | File | If the LLM is unavailable |
|---|---|---|
| Classify ambiguous prompts (`/generate`) | `classifier.py` | Only called when heuristic confidence is below `CLASSIFIER_CONFIDENCE_THRESHOLD`; on LLM failure it keeps the heuristic's guess (`llm_fallback_to_heuristic`) |
| Write OpenSCAD for prompts with no template | `openscad_generator.py` | 503 |
| Write CadQuery code | `cadquery_gen/` | 503 |
| Write `bpy` scripts for organic shapes | `blender_generator.py` | 503 |
| Suggest library tags (`/tags/suggest`) | `tag_suggester.py` | Falls back to filename-derived tags, `"method": "heuristic_fallback"` |

Thickening, watertight analysis/repair, molds, STEP, thumbnails need no LLM.
Speech-to-text (`/transcribe`) is separate: local faster-whisper, no LLM and
no cloud (see README "Voice answers").

## Providers and variables

`LLM_PROVIDER` selects the backend; anything other than `ollama` or `claude`
raises `LLMError`.

| Variable | Default | Notes |
|---|---|---|
| `LLM_PROVIDER` | `ollama` | |
| `OLLAMA_HOST` | code: `http://localhost:11434`; compose fallback: `http://ollama:11434` | Keep commented out in `.env` for the bundled container |
| `OLLAMA_MODEL` | `llama3.1` | Must match `ollama list` exactly, tag included |
| `LLM_TIMEOUT_SECONDS` | `60` | Applies to both providers |
| `ANTHROPIC_API_KEY` | empty | Required for `claude`; never commit it |
| `ANTHROPIC_MODEL` | `claude-sonnet-5` | |

`OllamaClient` posts to `{OLLAMA_HOST}/api/generate` (non-streaming) and
health-checks `GET {OLLAMA_HOST}/api/tags`. `ClaudeClient` uses the
`anthropic` SDK with `max_tokens=4096`; its health check lists models.

## Which Ollama?

1. **Bundled (default).** `docker compose up` starts an `ollama` service;
   pull the model once with `docker compose exec ollama ollama pull llama3.1`
   (run from the repo folder). Leave `OLLAMA_HOST` unset.
2. **Host Ollama (native install, or lambda02).** Set `OLLAMA_HOST` in `.env`
   and start with `docker compose up -d --no-deps app` so the bundled service
   (and its `11434` port mapping) is skipped. From a container use
   `http://host.docker.internal:<port>` - `docker-compose.yml` adds
   `extra_hosts: host.docker.internal:host-gateway` for native Linux Docker.
   Never use `localhost`: inside the container that is the container itself.
3. **lambda02 specifically.** The deployed app uses the host Ollama at
   `http://host.docker.internal:11436` (set in that host's untracked `.env`).
   From another machine on the tailnet the same server is reachable at
   `http://100.104.44.41:11436`. Which models are pulled on lambda02 cannot be
   verified from this repo - check with `ollama list` against that endpoint
   and set `OLLAMA_MODEL` to an installed tag. `Backlog.md` notes that
   `qwen2.5-coder:14b` is already on lambda02 and likely writes better code
   than `llama3.1`; that is an untested suggestion, not the configured model.
4. **Local fallback.** If lambda02 is unreachable, either point `OLLAMA_HOST`
   at a local Ollama (or leave it unset and use the bundled container) or set
   `LLM_PROVIDER=claude`. There is no automatic failover between providers.
5. **Claude API.** `LLM_PROVIDER=claude` plus `ANTHROPIC_API_KEY` in `.env`,
   started with `--no-deps app`. Paid; it gives noticeably better organic
   (Blender) shapes than a small local model (see README "Known limitation").

## Verify

Replace the host/port with your deployment (`localhost:8000` locally).

```bash
curl -s http://localhost:8000/health
```

`llm_provider` should show your choice and `llm_reachable` should be `true`
(`status` is `ok` only if OpenSCAD and Blender are also found). Then check the
model itself, which `/health` does not do:

```bash
curl -s http://100.104.44.41:11436/api/tags
```

```bash
curl -s -X POST http://localhost:8000/generate -H "Content-Type: application/json" -d '{"prompt": "a dragon figurine"}'
```

A `200` with `"backend": "blender"` proves the LLM path end to end; a `503`
with `Ollama request failed: ...` means the host, model tag or timeout is
wrong (see `TROUBLESHOOTING.md`). Note identical prompts are served from
cache (`CACHE_ENABLED`), so vary the prompt when re-testing.
