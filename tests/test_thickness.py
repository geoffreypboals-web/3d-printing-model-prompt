"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/tests/test_thickness.py
Description: Tests for both wall-thickness strategies - regenerating an
    OpenSCAD source's wall_thickness variable, and Blender mesh shelling
    via a Solidify modifier. Mocks all subprocess/binary lookups.
Inputs: pytest, tests/conftest.py fixtures.
Outputs: N/A (test module).
Troubleshooting:
    - regenerate_from_source tests patch openscad_generator's subprocess
      module (thickness.py calls into openscad_generator.render_scad_to_stl,
      which does the actual subprocess.run) - patch the right module or
      the mock won't take effect.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from threedprompt import openscad_generator, thickness
from threedprompt.config import settings
from threedprompt.thickness import ThicknessError, mesh_shell, regenerate_from_source


def _fake_which_openscad(binary_name):
    return "/usr/bin/openscad"


def _fake_which_blender(binary_name):
    return "/usr/bin/blender"


def _make_fake_openscad_run(succeed: bool = True):
    def _fake_run(cmd, capture_output, text, timeout):
        stl_path = Path(cmd[2])
        if succeed:
            stl_path.write_bytes(b"solid fake\nendsolid fake\n")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="render error")

    return _fake_run


def _make_fake_blender_run(succeed: bool = True):
    def _fake_run(cmd, capture_output, text, timeout):
        # cmd == [binary, "--background", "--python", script, "--", input, output, thickness]
        output_path = Path(cmd[6])
        if succeed:
            output_path.write_bytes(b"solid fake\nendsolid fake\n")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="solidify error")

    return _fake_run


def test_regenerate_from_source_golden_path(monkeypatch, tmp_path):
    monkeypatch.setattr(openscad_generator.shutil, "which", _fake_which_openscad)
    monkeypatch.setattr(openscad_generator.subprocess, "run", _make_fake_openscad_run(succeed=True))

    scad_path = tmp_path / "model.scad"
    scad_path.write_text("wall_thickness = 3.0;\ncube([10, 10, wall_thickness]);\n")
    stl_path = tmp_path / "model.stl"

    new_value = regenerate_from_source(scad_path, amount_mm=1.5, output_stl_path=stl_path)

    assert new_value == 4.5
    assert "wall_thickness = 4.5;" in scad_path.read_text()
    assert stl_path.is_file()


def test_regenerate_from_source_missing_variable_raises():
    # no filesystem/subprocess needed - fails before any of that
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        scad_path = Path(d) / "model.scad"
        scad_path.write_text("cube([10, 10, 10]);\n")
        with pytest.raises(ThicknessError, match="no wall_thickness variable"):
            regenerate_from_source(scad_path, amount_mm=1.0, output_stl_path=Path(d) / "model.stl")


def test_mesh_shell_golden_path(monkeypatch, tmp_path):
    monkeypatch.setattr(thickness.shutil, "which", _fake_which_blender)
    monkeypatch.setattr(thickness.subprocess, "run", _make_fake_blender_run(succeed=True))

    input_stl = tmp_path / "uploaded.stl"
    input_stl.write_bytes(b"solid fake\nendsolid fake\n")
    output_dir = tmp_path / "out"

    result_path = mesh_shell(input_stl, amount_mm=2.0, output_dir=output_dir)

    assert result_path.is_file()
    assert result_path == output_dir / "model.stl"


def test_mesh_shell_exceeds_max_thickness_raises_without_calling_blender(monkeypatch, tmp_path):
    called = False

    def _should_not_run(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(thickness.subprocess, "run", _should_not_run)
    input_stl = tmp_path / "uploaded.stl"
    input_stl.write_bytes(b"solid fake\nendsolid fake\n")

    with pytest.raises(ThicknessError, match="MAX_WALL_THICKNESS_MM"):
        mesh_shell(input_stl, amount_mm=settings.max_wall_thickness_mm + 1, output_dir=tmp_path / "out")
    assert called is False


def test_mesh_shell_missing_binary_raises_clear_error(monkeypatch, tmp_path):
    monkeypatch.setattr(thickness.shutil, "which", lambda _name: None)
    input_stl = tmp_path / "uploaded.stl"
    input_stl.write_bytes(b"solid fake\nendsolid fake\n")

    with pytest.raises(ThicknessError, match="not found on PATH"):
        mesh_shell(input_stl, amount_mm=2.0, output_dir=tmp_path / "out")
