"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/tests/test_blender_generator.py
Description: Tests for the Blender (complex-part) generation backend.
    Mocks the `blender` subprocess call and shutil.which so tests run
    without Blender installed.
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

from threedprompt import blender_generator
from threedprompt.blender_generator import BlenderGenerationError, generate


def _fake_which(binary_name):
    return "/usr/bin/blender"


def _make_fake_run(succeed: bool = True, tail: str = "Traceback: NameError"):
    def _fake_run(cmd, capture_output, text, timeout):
        # cmd == [binary, "--background", "--python", script_path, "--", stl_path]
        stl_path = Path(cmd[5])
        if succeed:
            stl_path.write_bytes(b"solid fake\nendsolid fake\n")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=1, stdout=tail, stderr="")

    return _fake_run


def test_golden_path_complex_prompt_generates_via_llm(monkeypatch, tmp_path, fake_llm_client):
    monkeypatch.setattr(blender_generator.shutil, "which", _fake_which)
    monkeypatch.setattr(blender_generator.subprocess, "run", _make_fake_run(succeed=True))

    llm = fake_llm_client(responses=["def build_scene():\n    import bpy\n    bpy.ops.mesh.primitive_cube_add()\n"])
    result = generate("a dwarf sitting under a mushroom", tmp_path, llm_client=llm)

    assert Path(result.stl_path).is_file()
    assert result.wall_thickness_param is None
    assert len(llm.calls) == 1


def test_blender_failure_retries_once_then_raises_with_traceback(monkeypatch, tmp_path, fake_llm_client):
    monkeypatch.setattr(blender_generator.shutil, "which", _fake_which)
    monkeypatch.setattr(
        blender_generator.subprocess, "run", _make_fake_run(succeed=False, tail="NameError: bad_call undefined")
    )

    llm = fake_llm_client(responses=["def build_scene():\n    bad_call()\n", "def build_scene():\n    bad_call()\n"])
    with pytest.raises(BlenderGenerationError, match="bad_call undefined"):
        generate("a dragon figurine", tmp_path, llm_client=llm)
    assert len(llm.calls) == 2


def test_missing_binary_raises_clear_error_without_calling_llm(monkeypatch, tmp_path, fake_llm_client):
    monkeypatch.setattr(blender_generator.shutil, "which", lambda _name: None)
    llm = fake_llm_client()  # no scripted responses - must fail before any LLM call
    with pytest.raises(BlenderGenerationError, match="not found on PATH"):
        generate("a small creature", tmp_path, llm_client=llm)
    assert llm.calls == []
