"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/tests/test_draft_analysis.py
Description: Tests for draft_analysis.py's orchestration - parameter
    validation, Blender subprocess invocation, and JSON report parsing
    (FR-4). Mocks the subprocess/binary lookup, matching
    test_mold.py/test_watertight.py's approach: the actual dot-product/
    island-grouping geometry inside blender_scripts/analyze_draft.py is
    verified separately against a real Blender install (see
    test_draft_analysis_integration.py), not here.
Inputs: pytest, tests/conftest.py fixtures.
Outputs: N/A (test module).
Troubleshooting:
    - A fake subprocess.run must still write a report.json at the
      --output path it was given, mirroring exactly what the real
      Blender script does - analyze_draft() reads that file, not the
      subprocess's return value.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from threedprompt import draft_analysis
from threedprompt.draft_analysis import DraftAnalysisError, analyze_draft


def _fake_which_blender(binary_name):
    return "/usr/bin/blender"


def _arg_after(cmd, flag):
    return cmd[cmd.index(flag) + 1]


def _make_fake_blender_run(succeed: bool = True, **report_overrides):
    def _fake_run(cmd, capture_output, text, timeout):
        output_path = Path(_arg_after(cmd, "--output"))
        if succeed:
            report = {
                "source_path": _arg_after(cmd, "--input"),
                "pull_axis": _arg_after(cmd, "--pull-axis"),
                "parting_coordinate": 0.5,
                "min_draft_angle_deg": float(_arg_after(cmd, "--min-draft-angle-deg")),
                "releasable": True,
                "problem_islands": [],
                "vertex_count": 8,
                "face_count": 12,
                "blender_version": "4.5.3",
            }
            report.update(report_overrides)
            output_path.write_text(json.dumps(report))
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        output_path.write_text(json.dumps({"error": "boom"}))
        return SimpleNamespace(returncode=1, stdout="", stderr="draft analysis error")

    return _fake_run


def test_analyze_draft_golden_path(monkeypatch, tmp_path):
    monkeypatch.setattr(draft_analysis.shutil, "which", _fake_which_blender)
    monkeypatch.setattr(draft_analysis.subprocess, "run", _make_fake_blender_run(succeed=True))

    input_stl = tmp_path / "model.stl"
    input_stl.write_bytes(b"solid fake\nendsolid fake\n")

    report = analyze_draft(str(input_stl))

    assert report.releasable is True
    assert report.problem_islands == []
    assert report.pull_axis == "z"


def test_analyze_draft_reports_problem_islands(monkeypatch, tmp_path):
    islands = [
        {
            "id": 0,
            "face_indices": [4, 5, 8, 9],
            "centroid": [0.5, 0.5, 0.5],
            "face_count": 4,
            "min_draft_angle_deg": 0.0,
            "classification": "insufficient_draft",
        }
    ]
    monkeypatch.setattr(draft_analysis.shutil, "which", _fake_which_blender)
    monkeypatch.setattr(
        draft_analysis.subprocess,
        "run",
        _make_fake_blender_run(succeed=True, releasable=False, problem_islands=islands),
    )

    input_stl = tmp_path / "model.stl"
    input_stl.write_bytes(b"solid fake\nendsolid fake\n")

    report = analyze_draft(str(input_stl))

    assert report.releasable is False
    assert len(report.problem_islands) == 1
    assert report.problem_islands[0].classification == "insufficient_draft"
    assert report.problem_islands[0].min_draft_angle_deg == 0.0


def test_analyze_draft_passes_params_as_cli_args(monkeypatch, tmp_path):
    captured = {}

    def _fake_run(cmd, capture_output, text, timeout):
        captured["cmd"] = cmd
        return _make_fake_blender_run(succeed=True)(cmd, capture_output, text, timeout)

    monkeypatch.setattr(draft_analysis.shutil, "which", _fake_which_blender)
    monkeypatch.setattr(draft_analysis.subprocess, "run", _fake_run)

    input_stl = tmp_path / "model.stl"
    input_stl.write_bytes(b"solid fake\nendsolid fake\n")

    analyze_draft(str(input_stl), pull_axis="x", min_draft_angle_deg=4.0)

    assert _arg_after(captured["cmd"], "--pull-axis") == "x"
    assert _arg_after(captured["cmd"], "--min-draft-angle-deg") == "4.0"


@pytest.mark.parametrize("bad_axis", ["w", "Z", "", "xyz"])
def test_analyze_draft_rejects_bad_pull_axis(monkeypatch, tmp_path, bad_axis):
    called = False

    def _should_not_run(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(draft_analysis.subprocess, "run", _should_not_run)
    input_stl = tmp_path / "model.stl"
    input_stl.write_bytes(b"solid fake\nendsolid fake\n")

    with pytest.raises(DraftAnalysisError, match="pull_axis must be"):
        analyze_draft(str(input_stl), pull_axis=bad_axis)
    assert called is False


def test_analyze_draft_rejects_negative_min_angle(monkeypatch, tmp_path):
    called = False

    def _should_not_run(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(draft_analysis.subprocess, "run", _should_not_run)
    input_stl = tmp_path / "model.stl"
    input_stl.write_bytes(b"solid fake\nendsolid fake\n")

    with pytest.raises(DraftAnalysisError, match="min_draft_angle_deg must be"):
        analyze_draft(str(input_stl), min_draft_angle_deg=-1.0)
    assert called is False


def test_analyze_draft_missing_binary_raises_clear_error(monkeypatch, tmp_path):
    monkeypatch.setattr(draft_analysis.shutil, "which", lambda _name: None)
    input_stl = tmp_path / "model.stl"
    input_stl.write_bytes(b"solid fake\nendsolid fake\n")

    with pytest.raises(DraftAnalysisError, match="not found on PATH"):
        analyze_draft(str(input_stl))


def test_analyze_draft_propagates_blender_reported_error(monkeypatch, tmp_path):
    monkeypatch.setattr(draft_analysis.shutil, "which", _fake_which_blender)
    monkeypatch.setattr(draft_analysis.subprocess, "run", _make_fake_blender_run(succeed=False))

    input_stl = tmp_path / "model.stl"
    input_stl.write_bytes(b"solid fake\nendsolid fake\n")

    with pytest.raises(DraftAnalysisError, match="boom"):
        analyze_draft(str(input_stl))
