# 0001: Ollama-first LLM backend, with a paid API as opt-in fallback

Date: 2026-08-28

## Decision

`src/llm_client.py` defaults to a local Ollama instance (`LLM_PROVIDER=ollama`)
for every LLM call this app makes at runtime. A paid provider (Anthropic, via
the plain Messages API over HTTP) is supported but only activates when
`LLM_PROVIDER=anthropic` is set explicitly, and fails loudly if
`ANTHROPIC_API_KEY` is missing rather than silently falling back to Ollama.

## Why

CLAUDE.md rule 6 requires any LLM capability the *program itself* calls at
runtime to avoid being permanently locked into a paid API, with Ollama as the
default no-cost option. This project's core function -- turning a short
description into a generated design prompt -- is called interactively, an
unbounded number of times, by whoever is using the app. Metered per-call
pricing on that access pattern is a real, recurring cost driver with no
natural cap unless one is added (rule 10); a local model has none.

Ollama's general-purpose models (llama3.1 by default, matching the rest of
this suite per SuiteControl's own documented shared instance) are meaningfully
weaker than a frontier hosted model at nuanced, multi-constraint generation.
That tradeoff is accepted here: the task (turning a plain description into a
structured, printer-aware prompt) doesn't need frontier-model reasoning, and
the abstraction in `src/llm_client.py` means switching to a stronger paid
backend later is a one-line env var change, not a rewrite.

## Alternatives considered

- **Paid API as the default.** Rejected -- directly against rule 6/10's
  default-cheap stance, and there's no technical reason this task needs it.
- **No abstraction, hardcode Ollama.** Rejected -- rule 6 explicitly asks for
  a swappable abstraction even when Ollama is the intended long-term default,
  so a future need for higher quality doesn't require touching every call
  site.
