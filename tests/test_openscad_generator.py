"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/tests/test_openscad_generator.py
Description: Tests for the OpenSCAD (simple-part) generation backend.
    Mocks the `openscad` subprocess call and shutil.which so tests run
    without OpenSCAD installed.
Inputs: pytest, tests/conftest.py fixtures.
Outputs: N/A (test module).
Troubleshooting:
    - If a test hangs, check the subprocess.run mock actually returns
      (real subprocess.run is never invoked in this module).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from threedprompt import openscad_generator
from threedprompt.openscad_generator import OpenScadGenerationError, generate


def _fake_which(binary_name):
    return "/usr/bin/openscad"


def _make_fake_run(succeed: bool = True, stderr: str = "syntax error"):
    """Build a fake subprocess.run that writes the target STL on success, per openscad's argv shape."""

    def _fake_run(cmd, capture_output, text, timeout):
        # cmd == [binary, "-o", stl_path, scad_path]
        stl_path = Path(cmd[2])
        if succeed:
            stl_path.write_bytes(b"solid fake\nendsolid fake\n")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr=stderr)

    return _fake_run


def test_golden_path_bracket_uses_template_no_llm_call(monkeypatch, tmp_path, fake_llm_client):
    monkeypatch.setattr(openscad_generator.shutil, "which", _fake_which)
    monkeypatch.setattr(openscad_generator.subprocess, "run", _make_fake_run(succeed=True))

    llm = fake_llm_client()  # no scripted responses - template path must not call it
    result = generate("a simple mounting bracket, 50mm long", tmp_path, wall_thickness_mm=4.0, llm_client=llm)

    assert result.wall_thickness_param == "wall_thickness"
    assert Path(result.stl_path).is_file()
    assert "wall_thickness = 4.0" in Path(result.source_path).read_text()
    assert llm.calls == []


def test_unrecognized_prompt_falls_back_to_llm_authored_scad(monkeypatch, tmp_path, fake_llm_client):
    monkeypatch.setattr(openscad_generator.shutil, "which", _fake_which)
    monkeypatch.setattr(openscad_generator.subprocess, "run", _make_fake_run(succeed=True))

    llm = fake_llm_client(responses=["wall_thickness = 2;\ncube([10, 10, wall_thickness]);"])
    result = generate("a weird little part", tmp_path, llm_client=llm)

    assert result.wall_thickness_param == "wall_thickness"
    assert len(llm.calls) == 1


def test_compile_failure_retries_once_then_raises_with_compiler_output(monkeypatch, tmp_path, fake_llm_client):
    monkeypatch.setattr(openscad_generator.shutil, "which", _fake_which)
    monkeypatch.setattr(
        openscad_generator.subprocess, "run", _make_fake_run(succeed=False, stderr="ERROR: unmatched brace")
    )

    llm = fake_llm_client(responses=["broken scad {", "still broken scad {"])
    with pytest.raises(OpenScadGenerationError, match="unmatched brace"):
        generate("a weird little part", tmp_path, llm_client=llm)
    assert len(llm.calls) == 2  # initial attempt + one retry with error fed back


def test_missing_binary_raises_clear_error(monkeypatch, tmp_path):
    monkeypatch.setattr(openscad_generator.shutil, "which", lambda _name: None)
    with pytest.raises(OpenScadGenerationError, match="not found on PATH"):
        generate("a simple bracket", tmp_path, wall_thickness_mm=3.0)


def test_missing_binary_on_llm_path_raises_without_calling_llm(monkeypatch, tmp_path, fake_llm_client):
    monkeypatch.setattr(openscad_generator.shutil, "which", lambda _name: None)
    llm = fake_llm_client()  # no scripted responses - must fail before any LLM call
    with pytest.raises(OpenScadGenerationError, match="not found on PATH"):
        generate("a weird little part", tmp_path, llm_client=llm)
    assert llm.calls == []
