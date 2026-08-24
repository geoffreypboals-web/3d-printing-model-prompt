"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/tests/conftest.py
Description: Shared pytest fixtures - redirects storage.output_dir at a
    per-test temp directory and provides a fake LLMClient so tests never
    need a real Ollama/Claude backend or the openscad/blender binaries.
Inputs: pytest's tmp_path fixture.
Outputs: fixtures `tmp_output_dir` (autouse) and `fake_llm_client`.
Troubleshooting:
    - If a test leaks state into another, check it isn't writing outside
      settings.output_dir - tmp_output_dir only redirects that one field.
"""

from __future__ import annotations

import pytest

from threedprompt.config import settings
from threedprompt.llm_client import LLMClient


@pytest.fixture(autouse=True)
def tmp_output_dir(tmp_path, monkeypatch):
    """Point settings.output_dir at a fresh temp directory for every test."""
    monkeypatch.setattr(settings, "output_dir", str(tmp_path / "output"))
    return tmp_path / "output"


class FakeLLMClient(LLMClient):
    """Deterministic stand-in for a real LLM backend, scripted per-test with canned responses."""

    def __init__(self, responses: list[str] | None = None, reachable: bool = True):
        self._responses = list(responses or [])
        self.calls: list[tuple[str, str | None]] = []
        self._reachable = reachable

    def generate(self, prompt: str, system: str | None = None) -> str:
        self.calls.append((prompt, system))
        if not self._responses:
            raise AssertionError("FakeLLMClient.generate() called with no scripted responses left")
        return self._responses.pop(0)

    def is_reachable(self) -> bool:
        return self._reachable


@pytest.fixture
def fake_llm_client():
    return FakeLLMClient
