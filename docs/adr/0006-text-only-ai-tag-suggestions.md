# 0006: AI tag suggestions reason over filename/metadata text, not the rendered thumbnail image

Date: 2026-09-14

## Context

The farm-manager sibling repo's Library feature review
(`3dPrinterWorkshopManager/docs/industry-tool-review-and-recommendations.md`
#7 item 5) asked for AI-assisted tagging, modeled on Meshory's optional
bring-your-own-OpenRouter-key tagging feature, but reusing this project's
own Ollama-first `llm_client.py` abstraction instead (per this project's
rule 6/rule 10: no-cost local backend by default, paid API opt-in). By
the time this landed, `/thumbnail` already existed (a rendered PNG per
model file), so a vision-based approach - feed the thumbnail image to a
multimodal model and ask what it shows - was the more Meshory-faithful
option on the table.

## Decision

**Ship text-only reasoning first**: `POST /tags/suggest` builds a prompt
from the file name, designer name, extension, and any known slicer
metadata (filament colors), and asks the configured LLM (via
`llm_client.LLMClient.generate()`, unchanged) for a short JSON tag list.
No image is sent anywhere.

This was a scope call, not a technical dead end. `llm_client.py`'s
`OllamaClient.generate()` only exercises Ollama's `/api/generate` text
endpoint. Sending an image would mean either extending `LLMClient` with a
new vision-capable method (a real interface change touching both
`OllamaClient` and `ClaudeClient`) or a second, parallel client path -
and it would additionally assume a multimodal model (e.g. `llava`) is
pulled locally, which nothing in this project currently verifies or
documents. Text-only reasoning needed zero changes to `llm_client.py` or
`config.py`, works against whatever text model `OLLAMA_MODEL` already
names, and ships a working (if rougher) version of the feature now.

## Consequences

- Suggestions are a starting point for human review, not a description of
  what the model actually looks like - a file named `part_47.stl` with no
  designer/metadata gets weak or empty suggestions, same as the
  heuristic-fallback path would. This is called out directly in
  `tag_suggester.py`'s docstring and the `/tags/suggest` route's own
  docstring, not left implicit.
- Never fails outright: `suggest_tags()` falls back to filename-derived
  heuristic tags (splitting the filename on non-alphanumeric characters)
  whenever the LLM is unreachable or its response can't be parsed as the
  expected `{"tags": [...]}` JSON shape, the same fail-open-to-heuristic
  pattern `classifier.py` already established for prompt classification.
  The response's `method` field (`"llm"` vs. `"heuristic_fallback"`) lets
  a caller distinguish the two rather than silently getting a
  weaker-than-expected result.
- **Upgrade path, not ruled out**: if/when a multimodal Ollama model
  becomes a documented, verified part of this project's local setup,
  `tag_suggester.py` is the one place to change - feed it the file's
  already-rendered thumbnail (from `thumbnail.py`) instead of/alongside
  the text prompt. Nothing about the `/tags/suggest` request/response
  shape (`TagSuggestRequest`/`TagSuggestResponse` in `models.py`) would
  need to change for a caller.
