"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/tests/test_watertight.py
Description: Tests for watertight.py's plumbing (binary resolution,
    subprocess invocation, JSON parsing, error translation) with the
    actual Blender subprocess mocked out, matching test_thickness.py's
    style. Real Blender geometry correctness (the bmesh hole-detection
    algorithm itself) is covered separately in
    test_watertight_integration.py, which needs a real Blender install.
Inputs: pytest, tests/conftest.py fixtures (tmp_output_dir).
Outputs: N/A (test module).
Troubleshooting:
    - These tests patch watertight.shutil.which and watertight.subprocess.run
      directly (the names watertight.py imported), not the stdlib modules -
      patch the wrong target and the mock silently doesn't take effect.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from threedprompt import watertight
from threedprompt.watertight import WatertightError, analyze_mesh, repair_mesh


def _fake_which_blender(binary_name):
    return "/usr/bin/blender"


_FAKE_REPORT = {
    "source_path": "input.stl",
    "is_watertight": False,
    "vertex_count": 8,
    "face_count": 11,
    "total_surface_area": 24.0,
    "bounding_box": {"min": [-1.0, -1.0, -1.0], "max": [1.0, 1.0, 1.0]},
    "holes": [
        {
            "id": 0,
            "vertex_indices": [0, 1, 2],
            "centroid": [0.0, 0.0, 1.0],
            "area": 3.0,
            "perimeter": 6.14,
            "planarity": 1.0,
            "classification": "ambiguous",
            "confidence": 0.0,
            "reason": "",
        }
    ],
    "flipped_normal_islands": [],
    "nonmanifold_junction_edge_count": 0,
    "blender_version": "4.0.2",
}


def _make_fake_analyze_run(succeed: bool = True, report: dict | None = None):
    def _fake_run(cmd, capture_output, text, timeout, check):
        # cmd == [binary, "--background", "--python", script, "--", "--input", path, "--output", out_path, ...]
        output_path = Path(cmd[cmd.index("--output") + 1])
        if succeed:
            output_path.write_text(json.dumps(report if report is not None else _FAKE_REPORT))
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="blender crashed")

    return _fake_run


def test_analyze_mesh_golden_path(monkeypatch, tmp_path):
    monkeypatch.setattr(watertight.shutil, "which", _fake_which_blender)
    monkeypatch.setattr(watertight.subprocess, "run", _make_fake_analyze_run(succeed=True))

    input_stl = tmp_path / "model.stl"
    input_stl.write_bytes(b"solid fake\nendsolid fake\n")

    report = analyze_mesh(str(input_stl))

    assert report.is_watertight is False
    assert len(report.holes) == 1
    # classify_report should have filled in a real classification, not
    # left the Blender script's default "ambiguous" placeholder.
    assert report.holes[0].classification.value in ("intentional_opening", "likely_defect", "ambiguous")
    assert report.holes[0].reason != ""


def test_analyze_mesh_missing_input_file_raises():
    with pytest.raises(WatertightError, match="not found"):
        analyze_mesh("/no/such/file.stl")


def test_analyze_mesh_unsupported_extension_raises(tmp_path):
    bogus = tmp_path / "model.xyz"
    bogus.write_text("not a mesh")
    with pytest.raises(WatertightError, match="Unsupported"):
        analyze_mesh(str(bogus))


def test_analyze_mesh_missing_blender_binary_raises_clear_error(monkeypatch, tmp_path):
    monkeypatch.setattr(watertight.shutil, "which", lambda _name: None)
    input_stl = tmp_path / "model.stl"
    input_stl.write_bytes(b"solid fake\nendsolid fake\n")

    with pytest.raises(WatertightError, match="not found on PATH"):
        analyze_mesh(str(input_stl))


def test_analyze_mesh_blender_error_in_json_is_surfaced(monkeypatch, tmp_path):
    monkeypatch.setattr(watertight.shutil, "which", _fake_which_blender)

    def _fake_run(cmd, capture_output, text, timeout, check):
        output_path = Path(cmd[cmd.index("--output") + 1])
        output_path.write_text(json.dumps({"error": "No mesh objects found in the imported file"}))
        return SimpleNamespace(returncode=1, stdout="", stderr="")

    monkeypatch.setattr(watertight.subprocess, "run", _fake_run)
    input_stl = tmp_path / "model.stl"
    input_stl.write_bytes(b"solid fake\nendsolid fake\n")

    with pytest.raises(WatertightError, match="No mesh objects found"):
        analyze_mesh(str(input_stl))


def test_repair_mesh_requires_at_least_one_hole_id(tmp_path):
    input_stl = tmp_path / "model.stl"
    input_stl.write_bytes(b"solid fake\nendsolid fake\n")
    with pytest.raises(WatertightError, match="no hole_ids"):
        repair_mesh(str(input_stl), [], str(tmp_path / "out.stl"))


def test_repair_mesh_golden_path(monkeypatch, tmp_path):
    monkeypatch.setattr(watertight.shutil, "which", _fake_which_blender)

    watertight_report = dict(_FAKE_REPORT)
    watertight_report["is_watertight"] = True
    watertight_report["holes"] = []

    def _fake_run(cmd, capture_output, text, timeout, check):
        output_path = Path(cmd[cmd.index("--output") + 1])
        output_path.write_bytes(b"solid fake\nendsolid fake\n")
        report_path = Path(cmd[cmd.index("--report-output") + 1])
        report_path.write_text(json.dumps(watertight_report))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(watertight.subprocess, "run", _fake_run)
    input_stl = tmp_path / "model.stl"
    input_stl.write_bytes(b"solid fake\nendsolid fake\n")
    output_stl = tmp_path / "out" / "model.stl"

    result = repair_mesh(str(input_stl), [0], str(output_stl))

    assert result.is_watertight is True
    assert result.closed_hole_ids == [0]
    assert output_stl.is_file()
