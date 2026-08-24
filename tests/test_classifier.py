"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/tests/test_classifier.py
Description: Tests for the hybrid heuristic/LLM complexity classifier.
Inputs: pytest, tests/conftest.py fixtures.
Outputs: N/A (test module).
Troubleshooting:
    - If a heuristic test starts failing after editing SIMPLE_KEYWORDS /
      COMPLEX_KEYWORDS in classifier.py, check the test prompt didn't
      accidentally start matching both vocabularies.
"""

from __future__ import annotations

import pytest

from threedprompt.classifier import classify_prompt
from threedprompt.models import ClassificationMethod, Complexity


def test_golden_path_simple_bracket_uses_heuristic_only(fake_llm_client):
    llm = fake_llm_client()  # no scripted responses - must not be called
    result = classify_prompt("a mounting bracket for a raspberry pi", llm_client=llm)
    assert result.label is Complexity.SIMPLE
    assert result.method is ClassificationMethod.HEURISTIC
    assert result.confidence >= 0.6
    assert llm.calls == []


def test_golden_path_complex_creature_uses_heuristic_only(fake_llm_client):
    llm = fake_llm_client()
    result = classify_prompt("a dwarf sitting under a mushroom", llm_client=llm)
    assert result.label is Complexity.COMPLEX
    assert result.method is ClassificationMethod.HEURISTIC
    assert llm.calls == []


def test_ambiguous_prompt_matching_both_vocabularies_falls_back_to_llm(fake_llm_client):
    llm = fake_llm_client(responses=['{"label": "complex", "confidence": 0.9, "reasoning": "organic sculpture"}'])
    # "bracket" (simple) and "dragon" (complex) both match - heuristic can't be confident.
    result = classify_prompt("a bracket shaped like a dragon", llm_client=llm)
    assert result.label is Complexity.COMPLEX
    assert result.method is ClassificationMethod.LLM
    assert len(llm.calls) == 1


def test_unrecognized_prompt_with_no_keywords_falls_back_to_llm(fake_llm_client):
    llm = fake_llm_client(responses=['{"label": "simple", "confidence": 0.8, "reasoning": "basic geometric part"}'])
    result = classify_prompt("a widget for my desk", llm_client=llm)
    assert result.label is Complexity.SIMPLE
    assert result.method is ClassificationMethod.LLM


def test_llm_failure_falls_back_to_heuristic_guess(fake_llm_client):
    llm = fake_llm_client(responses=["not valid json at all"])
    result = classify_prompt("a mysterious object", llm_client=llm)
    assert result.method is ClassificationMethod.LLM_FALLBACK_TO_HEURISTIC


def test_empty_prompt_raises():
    with pytest.raises(ValueError):
        classify_prompt("   ")
