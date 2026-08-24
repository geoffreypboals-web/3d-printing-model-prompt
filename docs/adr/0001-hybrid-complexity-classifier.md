# 0001. Hybrid heuristic/LLM classifier for routing prompts

Date: 2026-08-24

## Status

Accepted

## Context

Every generation request needs to be routed to either OpenSCAD (simple,
parametric parts) or Blender (complex, organic shapes). We could route with
an LLM call on every request, a pure keyword heuristic, or something in
between.

## Decision

Try a free, deterministic keyword heuristic first
(`src/threedprompt/classifier.py`: `SIMPLE_KEYWORDS` / `COMPLEX_KEYWORDS`).
Only when it isn't confident (no keywords matched, or both vocabularies
matched) do we fall back to asking the configured LLM to classify the
prompt. If that LLM call itself fails, we fall back again to the
heuristic's best guess rather than failing the request.

## Consequences

- Most requests (anything with an unambiguous keyword like "bracket" or
  "dragon") cost zero LLM calls, directly serving CLAUDE.md rule 10's
  cost-control guidance.
- Vocabulary lives in one place and is easy to extend as new part types
  come up.
- A genuinely novel prompt with no recognizable keywords still gets a
  reasonable routing decision via the LLM, rather than a hardcoded guess.
- Misclassification is still possible for prompts the LLM itself gets
  wrong, or where the heuristic is confidently wrong (e.g. a prompt that
  matches a simple-part keyword but actually describes something organic
  the user attached that word to informally) - acceptable tradeoff for the
  cost savings; the vocabulary can be tuned as real prompts reveal gaps.
