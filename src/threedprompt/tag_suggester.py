"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/src/threedprompt/tag_suggester.py
Description: Suggests descriptive tags for a library model file from its
    filename/designer/metadata via the configured LLM backend (Ollama-
    first, rule 6). Called over HTTP by the sibling 3dPrinterWorkshopManager
    repo's library route the same way it already calls /watertight/upload
    and /thumbnail -- see that repo's
    docs/industry-tool-review-and-recommendations.md #7 item 5. Text-only
    (filename/metadata) tagging, not vision-based: no multimodal Ollama
    model is assumed to be configured here. Upgrade path: feed the file's
    already-rendered thumbnail (thumbnail.py) to a vision-capable Ollama
    model (e.g. llava) if one becomes available.
Inputs: TagSuggestRequest (file name, optional designer name/extension/
    existing tags/filament colors).
Outputs: (tags, method) where method is "llm" or "heuristic_fallback" --
    never raises. Falls back to filename-derived heuristic tags if the LLM
    is unavailable or its response can't be parsed, the same
    fail-open-to-heuristic pattern classifier.py already uses for prompt
    classification.
Troubleshooting:
    - Suggestions look generic or off-base: this reasons over the file
      name and metadata only, not the actual model geometry/appearance --
      expected to be a rough starting point for human review, not
      authoritative.
    - Always returns filename-derived tags, never LLM ones: check
      GET /health's llm status; the configured Ollama model may be
      unreachable or LLM_PROVIDER may be misconfigured.
"""

from __future__ import annotations

import json
import re
from typing import Literal

from threedprompt.llm_client import LLMClient, LLMError
from threedprompt.logging_config import get_logger
from threedprompt.models import TagSuggestRequest

logger = get_logger(__name__)

_MAX_TAGS = 8
_WORD_PATTERN = re.compile(r"[a-z0-9]+")
_TAG_SANITIZE_PATTERN = re.compile(r"[^a-z0-9\-]")

_SYSTEM_PROMPT = (
    "You suggest short, lowercase, comma-free descriptive tags for 3D-printable model files, "
    "based only on the file name and any metadata given -- you cannot see the actual model. "
    'Respond with ONLY a JSON object: {"tags": ["tag1", "tag2", ...]}. '
    f"Suggest at most {_MAX_TAGS} tags. Each tag is 1-3 lowercase words (use hyphens, not spaces, for "
    'multi-word tags), describing likely subject/category/use (e.g. "miniature", "articulated", '
    '"tabletop-terrain", "phone-stand"), not administrative details already in the metadata.'
)


def _dedupe_against_existing(candidates: list[str], existing_tags: list[str]) -> list[str]:
    """Drops candidates already present in existing_tags (case-insensitive) and internal duplicates."""
    existing = {tag.lower() for tag in existing_tags}
    seen: set[str] = set()
    result: list[str] = []
    for candidate in candidates:
        if candidate in existing or candidate in seen:
            continue
        seen.add(candidate)
        result.append(candidate)
        if len(result) >= _MAX_TAGS:
            break
    return result


def _heuristic_tags(request: TagSuggestRequest) -> list[str]:
    """
    Derives rough tags from the filename alone, for when the LLM is unavailable.

    Splits the filename (minus extension) on non-alphanumeric characters, drops
    short/numeric-only tokens, and keeps the rest in order as candidate tags.
    """
    stem = request.file_name.rsplit(".", 1)[0]
    words = _WORD_PATTERN.findall(stem.lower())
    candidates = [word for word in words if len(word) > 2 and not word.isdigit()]
    return _dedupe_against_existing(candidates, request.existing_tags)


def _build_prompt(request: TagSuggestRequest) -> str:
    """Builds the user-turn prompt describing the file for tag suggestion."""
    lines = [f"File name: {request.file_name}"]
    if request.designer_name:
        lines.append(f"Designer: {request.designer_name}")
    if request.extension:
        lines.append(f"File format: {request.extension}")
    if request.colors:
        lines.append(f"Filament colors used: {', '.join(request.colors)}")
    if request.existing_tags:
        lines.append(f"Tags already applied (do not repeat these): {', '.join(request.existing_tags)}")
    return "\n".join(lines)


def _parse_tags(raw: str, existing_tags: list[str]) -> list[str] | None:
    """Parses the LLM's JSON tag-list response. Returns None if it's unparseable or empty."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None

    raw_tags = data.get("tags") if isinstance(data, dict) else None
    if not isinstance(raw_tags, list):
        return None

    candidates: list[str] = []
    for item in raw_tags:
        if not isinstance(item, str):
            continue
        tag = _TAG_SANITIZE_PATTERN.sub("", item.strip().lower().replace(" ", "-"))
        if tag:
            candidates.append(tag)

    tags = _dedupe_against_existing(candidates, existing_tags)
    return tags or None


def suggest_tags(
    request: TagSuggestRequest, llm_client: LLMClient | None = None
) -> tuple[list[str], Literal["llm", "heuristic_fallback"]]:
    """
    Suggests tags for a model file.

    Tries the LLM first; falls back to filename-derived heuristic tags if the LLM is
    unavailable or its response can't be parsed. Never raises.

    Returns (tags, method) where method is "llm" or "heuristic_fallback".
    """
    if llm_client is None:
        from threedprompt.llm_client import get_llm_client

        try:
            llm_client = get_llm_client()
        except LLMError as exc:
            logger.warning("No LLM client available for tag suggestion (%s); using heuristic.", exc)
            return _heuristic_tags(request), "heuristic_fallback"

    try:
        raw = llm_client.generate(prompt=_build_prompt(request), system=_SYSTEM_PROMPT)
    except LLMError as exc:
        logger.warning("LLM tag suggestion request failed (%s); using heuristic.", exc)
        return _heuristic_tags(request), "heuristic_fallback"

    tags = _parse_tags(raw, request.existing_tags)
    if tags is None:
        logger.warning("LLM tag suggestion returned unparseable output; using heuristic.")
        return _heuristic_tags(request), "heuristic_fallback"
    return tags, "llm"
