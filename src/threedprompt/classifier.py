"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/src/threedprompt/classifier.py
Description: Decides whether a prompt describes a "simple" parametric
    part (routed to OpenSCAD) or a "complex"/organic shape (routed to
    Blender). Uses a cheap, deterministic keyword heuristic first; only
    falls back to an LLM call when the heuristic isn't confident, per
    rule 10's cost-control guidance (don't pay for an LLM call when a
    free heuristic already knows the answer).
Inputs: A raw prompt string; config.settings.classifier_confidence_threshold;
    an LLMClient (see llm_client.py) used only on the fallback path.
Outputs: A ClassificationResult (see models.py) with a label, confidence,
    the method that produced it, and human-readable reasoning.
Troubleshooting:
    - If everything routes to Blender unexpectedly, check that the
      prompt isn't empty after keyword extraction - see
      _score_keywords() - and that the LLM fallback isn't defaulting to
      "complex" on parse failure (see _parse_llm_response()).
    - If classification feels wrong for a specific term, add it to
      SIMPLE_KEYWORDS / COMPLEX_KEYWORDS below rather than special-casing
      it elsewhere - this is the single place that vocabulary lives.
"""

from __future__ import annotations

import json
import re

from threedprompt.config import settings
from threedprompt.llm_client import LLMClient, LLMError
from threedprompt.logging_config import get_logger
from threedprompt.models import ClassificationMethod, ClassificationResult, Complexity

logger = get_logger(__name__)

# Deterministic, mechanical/parametric parts - safe for OpenSCAD's CSG model.
SIMPLE_KEYWORDS = {
    "bracket",
    "mount",
    "mounting",
    "bolt",
    "screw",
    "nut",
    "washer",
    "box",
    "case",
    "enclosure",
    "plate",
    "spacer",
    "standoff",
    "holder",
    "clip",
    "hinge",
    "gusset",
    "panel",
    "bushing",
    "flange",
    "adapter",
    "coupler",
    "sleeve",
    "tube",
    "pipe",
    "shelf",
    "jig",
    "fixture",
    "gear",
    "knob",
    "handle",
    "cap",
    "lid",
    "block",
    "rod",
    "peg",
    "hook",
    "rail",
    "channel",
    "frame",
    "grid",
    "tray",
    "stand",
    "riser",
    "wedge",
    "shim",
}

# Organic / freeform / character shapes - need sculpting, not CSG primitives.
COMPLEX_KEYWORDS = {
    "character",
    "creature",
    "animal",
    "figure",
    "figurine",
    "dwarf",
    "elf",
    "dragon",
    "goblin",
    "orc",
    "wizard",
    "knight",
    "sculpture",
    "face",
    "statue",
    "organic",
    "mushroom",
    "person",
    "human",
    "humanoid",
    "bust",
    "monster",
    "beast",
    "toy",
    "miniature",
    "mini",
    "sculpt",
    "hair",
    "fur",
    "tree",
    "plant",
    "flower",
    "skull",
    "hand",
    "body",
    "portrait",
    "cartoon",
    "mascot",
    "dinosaur",
    "fish",
    "bird",
    "insect",
    "tentacle",
}

_WORD_RE = re.compile(r"[a-z0-9]+")

_LLM_SYSTEM_PROMPT = (
    "You classify a short description of a physical object for a 3D printing "
    "pipeline. Respond with ONLY a JSON object of the form "
    '{"label": "simple", "confidence": 0.0-1.0, "reasoning": "..."}. '
    'label must be "simple" for parts that are built from basic geometric '
    "primitives (boxes, cylinders, holes, fillets - e.g. brackets, mounts, "
    'enclosures, spacers) or "complex" for organic, characterful, or '
    "freeform shapes (creatures, figures, sculptural forms) that need "
    "sculpting rather than parametric CSG."
)


def _score_keywords(prompt: str) -> tuple[int, int]:
    """Count simple-vocabulary and complex-vocabulary word hits in the prompt."""
    words = set(_WORD_RE.findall(prompt.lower()))
    return len(words & SIMPLE_KEYWORDS), len(words & COMPLEX_KEYWORDS)


def _heuristic_classify(prompt: str) -> ClassificationResult:
    """Cheap, deterministic first pass. Confident only when exactly one vocabulary matched."""
    simple_hits, complex_hits = _score_keywords(prompt)

    if simple_hits > 0 and complex_hits == 0:
        confidence = min(0.6 + 0.1 * simple_hits, 0.95)
        return ClassificationResult(
            label=Complexity.SIMPLE,
            confidence=confidence,
            method=ClassificationMethod.HEURISTIC,
            reasoning=f"Matched {simple_hits} simple-part keyword(s), no complex-shape keywords.",
        )
    if complex_hits > 0 and simple_hits == 0:
        confidence = min(0.6 + 0.1 * complex_hits, 0.95)
        return ClassificationResult(
            label=Complexity.COMPLEX,
            confidence=confidence,
            method=ClassificationMethod.HEURISTIC,
            reasoning=f"Matched {complex_hits} complex-shape keyword(s), no simple-part keywords.",
        )
    # Ambiguous: nothing matched, or both vocabularies matched. Low confidence -> LLM fallback.
    reasoning = (
        "No recognized keywords matched."
        if simple_hits == 0 and complex_hits == 0
        else f"Matched both simple ({simple_hits}) and complex ({complex_hits}) keywords - ambiguous."
    )
    return ClassificationResult(
        label=Complexity.COMPLEX if complex_hits >= simple_hits else Complexity.SIMPLE,
        confidence=0.3,
        method=ClassificationMethod.HEURISTIC,
        reasoning=reasoning,
    )


def _parse_llm_response(raw: str) -> ClassificationResult:
    """Parse the LLM's JSON classification response, tolerating surrounding prose."""
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON object found in LLM response: {raw!r}")
    data = json.loads(match.group(0))
    label_raw = str(data["label"]).strip().lower()
    if label_raw not in (Complexity.SIMPLE.value, Complexity.COMPLEX.value):
        raise ValueError(f"Unexpected label from LLM: {label_raw!r}")
    confidence = float(data.get("confidence", 0.5))
    confidence = max(0.0, min(confidence, 1.0))
    reasoning = str(data.get("reasoning", "")) or "LLM classification (no reasoning given)."
    return ClassificationResult(
        label=Complexity(label_raw),
        confidence=confidence,
        method=ClassificationMethod.LLM,
        reasoning=reasoning,
    )


def classify_prompt(prompt: str, llm_client: LLMClient | None = None) -> ClassificationResult:
    """
    Classify a prompt as simple (OpenSCAD) or complex (Blender).

    Tries the free keyword heuristic first. If its confidence is below
    settings.classifier_confidence_threshold, calls the LLM for a second
    opinion; if the LLM call itself fails, falls back to the heuristic's
    best guess rather than failing the whole request.
    """
    if not prompt or not prompt.strip():
        raise ValueError("prompt must be a non-empty string")

    heuristic_result = _heuristic_classify(prompt)
    if heuristic_result.confidence >= settings.classifier_confidence_threshold:
        return heuristic_result

    if llm_client is None:
        from threedprompt.llm_client import get_llm_client

        try:
            llm_client = get_llm_client()
        except LLMError as exc:
            logger.warning("No LLM client available for classification fallback (%s); using heuristic guess.", exc)
            return heuristic_result

    try:
        raw = llm_client.generate(prompt=f"Classify this object description: {prompt!r}", system=_LLM_SYSTEM_PROMPT)
        return _parse_llm_response(raw)
    except (LLMError, ValueError, KeyError, json.JSONDecodeError) as exc:
        logger.warning("LLM classification fallback failed (%s); using heuristic guess.", exc)
        return ClassificationResult(
            label=heuristic_result.label,
            confidence=heuristic_result.confidence,
            method=ClassificationMethod.LLM_FALLBACK_TO_HEURISTIC,
            reasoning=f"LLM fallback failed ({exc}); kept heuristic result: {heuristic_result.reasoning}",
        )
