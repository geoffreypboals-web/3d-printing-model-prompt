"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/tests/test_freecad_cad.py
Description: Tests for freecad_cad.py's plumbing (binary resolution,
    subprocess invocation, JSON report parsing, error translation) with
    the actual FreeCADCmd subprocess mocked out, matching
    test_watertight.py's style. Real FreeCAD geometry correctness (Part
    Thickness, STEP export/import, ShapeFix) was verified by hand against
    a real FreeCAD install during development - see
    docs/adr/0005-freecad-third-cad-backend.md - not re-verified here.
Inputs: pytest, tests/conftest.py fixtures (tmp_output_dir).
Outputs: N/A (test module).
Troubleshooting:
    - These tests patch freecad_cad.shutil.which and
      freecad_cad.subprocess.run directly (the names freecad_cad.py
      imported), not the stdlib modules - patch the wrong target and the
      mock silently doesn't take effect.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from threedprompt import freecad_cad
from threedprompt.freecad_cad import FreeCADCADError, heal_solid, mesh_to_step, step_to_mesh, thicken_mesh


def _fake_which_freecad(binary_name):
    return "/usr/bin/freecadcmd"


def _make_fake_run(succeed: bool = True, report: dict | None = None):
    def _fake_run(cmd, capture_output, text, timeout, check):
        # cmd == [binary, script, "--", "--input", path, ..., "--report", report_path, ...]
        report_path = Path(cmd[cmd.index("--report") + 1])
        output_path = Path(cmd[cmd.index("--output") + 1])
        if succeed:
            output_path.write_bytes(b"solid fake\nendsolid fake\n")
            report_path.write_text(json.dumps(report if report is not None else {"ok": True}))
        else:
            report_path.write_text(json.dumps(report if report is not None else {"ok": False, "error": "boom"}))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    return _fake_run


def test_thicken_mesh_golden_path(monkeypatch, tmp_path):
    monkeypatch.setattr(freecad_cad.shutil, "which", _fake_which_freecad)
    fake_report = {"ok": True, "original_volume": 1000.0, "thickened_volume": 712.0}
    monkeypatch.setattr(freecad_cad.subprocess, "run", _make_fake_run(report=fake_report))

    input_stl = tmp_path / "input.stl"
    input_stl.write_bytes(b"solid fake\nendsolid fake\n")
    output_path = tmp_path / "out" / "model.stl"

    result = thicken_mesh(input_stl, amount_mm=2.0, output_path=output_path)

    assert result["thickened_volume"] == 712.0
    assert output_path.is_file()


def test_thicken_mesh_script_error_raises_freecadcaderror(monkeypatch, tmp_path):
    monkeypatch.setattr(freecad_cad.shutil, "which", _fake_which_freecad)
    fake_report = {"ok": False, "error": "Null input shape"}
    monkeypatch.setattr(freecad_cad.subprocess, "run", _make_fake_run(succeed=False, report=fake_report))

    input_stl = tmp_path / "input.stl"
    input_stl.write_bytes(b"solid fake\nendsolid fake\n")

    with pytest.raises(FreeCADCADError, match="Null input shape"):
        thicken_mesh(input_stl, amount_mm=2.0, output_path=tmp_path / "out" / "model.stl")


def test_missing_binary_raises_clear_error(monkeypatch, tmp_path):
    monkeypatch.setattr(freecad_cad.shutil, "which", lambda _name: None)
    input_stl = tmp_path / "input.stl"
    input_stl.write_bytes(b"solid fake\nendsolid fake\n")

    with pytest.raises(FreeCADCADError, match="not found on PATH"):
        thicken_mesh(input_stl, amount_mm=2.0, output_path=tmp_path / "out" / "model.stl")


def test_mesh_to_step_golden_path(monkeypatch, tmp_path):
    monkeypatch.setattr(freecad_cad.shutil, "which", _fake_which_freecad)
    monkeypatch.setattr(freecad_cad.subprocess, "run", _make_fake_run(report={"ok": True, "volume": 1000.0}))

    input_stl = tmp_path / "input.stl"
    input_stl.write_bytes(b"solid fake\nendsolid fake\n")
    output_step = tmp_path / "out" / "model.step"

    result = mesh_to_step(input_stl, output_step)

    assert result["volume"] == 1000.0
    assert output_step.is_file()


def test_step_to_mesh_golden_path(monkeypatch, tmp_path):
    monkeypatch.setattr(freecad_cad.shutil, "which", _fake_which_freecad)
    fake_report = {"ok": True, "volume": 1000.0, "solids": 1}
    monkeypatch.setattr(freecad_cad.subprocess, "run", _make_fake_run(report=fake_report))

    input_step = tmp_path / "input.step"
    input_step.write_text("ISO-10303-21;\n")
    output_stl = tmp_path / "out" / "model.stl"

    result = step_to_mesh(input_step, output_stl)

    assert result["solids"] == 1
    assert output_stl.is_file()


def test_heal_solid_reports_validity(monkeypatch, tmp_path):
    monkeypatch.setattr(freecad_cad.shutil, "which", _fake_which_freecad)
    monkeypatch.setattr(
        freecad_cad.subprocess,
        "run",
        _make_fake_run(
            report={
                "ok": True,
                "fixed": True,
                "valid_before": False,
                "valid_after": True,
                "shells_before": 2,
                "shells_after": 1,
            }
        ),
    )

    input_stl = tmp_path / "input.stl"
    input_stl.write_bytes(b"solid fake\nendsolid fake\n")
    output_stl = tmp_path / "out" / "model.stl"

    result = heal_solid(input_stl, output_stl)

    assert result["valid_before"] is False
    assert result["valid_after"] is True
    assert output_stl.is_file()


def test_report_missing_raises_with_stderr(monkeypatch, tmp_path):
    monkeypatch.setattr(freecad_cad.shutil, "which", _fake_which_freecad)

    def _fake_run(cmd, capture_output, text, timeout, check):
        return SimpleNamespace(returncode=1, stdout="", stderr="freecadcmd crashed")

    monkeypatch.setattr(freecad_cad.subprocess, "run", _fake_run)

    input_stl = tmp_path / "input.stl"
    input_stl.write_bytes(b"solid fake\nendsolid fake\n")

    with pytest.raises(FreeCADCADError, match="freecadcmd crashed"):
        thicken_mesh(input_stl, amount_mm=2.0, output_path=tmp_path / "out" / "model.stl")
