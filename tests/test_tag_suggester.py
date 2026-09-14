"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/tests/test_tag_suggester.py
Description: Tests for the LLM-first/heuristic-fallback tag suggester.
Inputs: pytest, tests/conftest.py's fake_llm_client fixture.
Outputs: N/A (test module).
Troubleshooting:
    - If a heuristic test starts failing after editing the word-splitting
      regex in tag_suggester.py, check the test filename didn't
      accidentally stop matching the expected token boundaries.
"""

from __future__ import annotations

from threedprompt.models import TagSuggestRequest
from threedprompt.tag_suggester import suggest_tags


def test_golden_path_llm_suggests_tags_from_valid_json_response(fake_llm_client):
    llm = fake_llm_client(responses=['{"tags": ["miniature", "articulated", "dragon"]}'])
    request = TagSuggestRequest(file_name="dragon_v2.stl", designer_name="ExampleStudio")
    tags, method = suggest_tags(request, llm_client=llm)
    assert tags == ["miniature", "articulated", "dragon"]
    assert method == "llm"
    assert len(llm.calls) == 1


def test_llm_tags_are_sanitized_and_deduped_against_existing_tags(fake_llm_client):
    llm = fake_llm_client(responses=['{"tags": ["Tabletop Terrain", "dragon", "dragon", "MINIATURE!!"]}'])
    request = TagSuggestRequest(file_name="dragon_v2.stl", existing_tags=["dragon"])
    tags, method = suggest_tags(request, llm_client=llm)
    assert tags == ["tabletop-terrain", "miniature"]
    assert method == "llm"


def test_unparseable_llm_response_falls_back_to_filename_heuristic(fake_llm_client):
    llm = fake_llm_client(responses=["not valid json at all"])
    request = TagSuggestRequest(file_name="dragon_articulated_v2.stl")
    tags, method = suggest_tags(request, llm_client=llm)
    assert method == "heuristic_fallback"
    assert "dragon" in tags
    assert "articulated" in tags
    assert "v2" not in tags  # short/alnum-mixed token filtered out by len()>2 -- "v2" is len 2


def test_llm_response_with_no_usable_tags_falls_back_to_heuristic(fake_llm_client):
    # Valid JSON, but every suggested tag is already in existing_tags -- nothing left after dedupe.
    llm = fake_llm_client(responses=['{"tags": ["dragon"]}'])
    request = TagSuggestRequest(file_name="dragon_model.stl", existing_tags=["dragon"])
    tags, method = suggest_tags(request, llm_client=llm)
    assert method == "heuristic_fallback"
    assert "model" in tags


def test_heuristic_tags_drop_short_and_numeric_only_tokens(fake_llm_client):
    llm = fake_llm_client(responses=["not valid json"])
    request = TagSuggestRequest(file_name="v2_42_phone_stand.stl")
    tags, method = suggest_tags(request, llm_client=llm)
    assert method == "heuristic_fallback"
    assert "42" not in tags  # numeric-only token
    assert "v2" not in tags  # length <= 2
    assert tags == ["phone", "stand"]
