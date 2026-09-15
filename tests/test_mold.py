"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/tests/test_mold.py
Description: Tests for mold.py's orchestration - parameter validation,
    Blender subprocess invocation, JSON report handling, and the
    pre-generation watertight auto-repair step (FR-2). Mocks all
    subprocess/binary lookups, matching test_thickness.py's approach: the
    actual boolean/bmesh geometry inside blender_scripts/make_mold.py is
    verified separately against a real Blender install (see
    test_mold_integration.py), not here.
Inputs: pytest, tests/conftest.py fixtures.
Outputs: N/A (test module).
Troubleshooting:
    - A fake subprocess.run must still write a report.json at the
      --report-output path it was given, mirroring exactly what the real
      Blender script does - mold.make_mold() reads that file, not the
      subprocess's return value.
    - mold._analyze_mesh (watertight.analyze_mesh) and mold._analyze_draft
      (draft_analysis.analyze_draft) are both mocked directly (not via
      subprocess) in autouse fixtures defaulting to "already watertight"/
      "fully releasable" - each runs its own separate Blender subprocess
      with a different script/args shape than make_mold's, so faking
      either at the subprocess.run level (shared, since mold.subprocess
      IS the global subprocess module) would require every existing fake
      to also understand that call. Tests exercising the repair path or
      a draft warning override the relevant fixture's patch directly.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from threedprompt import mold
from threedprompt.config import settings
from threedprompt.models import (
    BoundingBox,
    DraftReport,
    Hole,
    HoleClassification,
    ProblemFaceIsland,
    RepairResult,
    WatertightReport,
)
from threedprompt.mold import MoldError, make_mold


def _fake_which_blender(binary_name):
    return "/usr/bin/blender"


def _make_watertight_report(is_watertight=True, holes=None):
    return WatertightReport(
        source_path="model.stl",
        is_watertight=is_watertight,
        vertex_count=8,
        face_count=12,
        total_surface_area=6.0,
        bounding_box=BoundingBox(min=(0.0, 0.0, 0.0), max=(1.0, 1.0, 1.0)),
        holes=holes or [],
    )


def _make_draft_report(releasable=True, problem_islands=None):
    return DraftReport(
        source_path="model.stl",
        pull_axis="z",
        parting_coordinate=0.5,
        min_draft_angle_deg=2.0,
        releasable=releasable,
        problem_islands=problem_islands or [],
    )


@pytest.fixture(autouse=True)
def _mock_watertight_analysis(monkeypatch):
    """Default every test to an already-watertight input, so the FR-2 pre-flight check is a no-op unless a
    test explicitly re-patches mold._analyze_mesh/_repair_mesh to exercise the auto-repair path."""
    monkeypatch.setattr(mold, "_analyze_mesh", lambda path: _make_watertight_report())


@pytest.fixture(autouse=True)
def _mock_draft_analysis(monkeypatch):
    """Default every test to a fully-releasable draft check, so direct_cast's automatic FR-4 warning is a
    no-op unless a test explicitly re-patches mold._analyze_draft to exercise the warning path."""
    monkeypatch.setattr(mold, "_analyze_draft", lambda path, **kwargs: _make_draft_report())


def _arg_after(cmd, flag):
    return cmd[cmd.index(flag) + 1]


def _make_fake_blender_run(
    succeed: bool = True,
    part_keys=("pour_box_bottom", "pour_box_top", "clamp_shell_bottom", "clamp_shell_top"),
    cavity_volume_mm3: float = 1000.0,
):
    def _fake_run(cmd, capture_output, text, timeout):
        report_path = Path(_arg_after(cmd, "--report-output"))
        output_dir = Path(_arg_after(cmd, "--output-dir"))
        if succeed:
            paths = {}
            for key in part_keys:
                part_path = output_dir / f"{key}.stl"
                part_path.write_bytes(b"solid fake\nendsolid fake\n")
                paths[key] = str(part_path)
            report_path.write_text(json.dumps({"paths": paths, "cavity_volume_mm3": cavity_volume_mm3}))
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        report_path.write_text(json.dumps({"error": "boom"}))
        return SimpleNamespace(returncode=1, stdout="", stderr="mold error")

    return _fake_run


def test_make_mold_golden_path(monkeypatch, tmp_path):
    monkeypatch.setattr(mold.shutil, "which", _fake_which_blender)
    monkeypatch.setattr(mold.subprocess, "run", _make_fake_blender_run(succeed=True))

    input_stl = tmp_path / "model.stl"
    input_stl.write_bytes(b"solid fake\nendsolid fake\n")
    output_dir = tmp_path / "out"

    result = make_mold(input_stl, output_dir)

    assert Path(result.pour_box_bottom_stl).is_file()
    assert Path(result.pour_box_top_stl).is_file()
    assert Path(result.clamp_shell_bottom_stl).is_file()
    assert Path(result.clamp_shell_top_stl).is_file()
    assert result.direct_mold_bottom_stl is None
    assert result.direct_mold_top_stl is None
    # FR-4's automatic draft warning only runs for direct_cast.
    assert result.draft_check is None


def test_make_mold_direct_cast_golden_path(monkeypatch, tmp_path):
    monkeypatch.setattr(mold.shutil, "which", _fake_which_blender)
    monkeypatch.setattr(
        mold.subprocess,
        "run",
        _make_fake_blender_run(succeed=True, part_keys=("direct_mold_bottom", "direct_mold_top")),
    )

    input_stl = tmp_path / "model.stl"
    input_stl.write_bytes(b"solid fake\nendsolid fake\n")

    result = make_mold(input_stl, tmp_path / "out", mode="direct_cast")

    assert Path(result.direct_mold_bottom_stl).is_file()
    assert Path(result.direct_mold_top_stl).is_file()
    assert result.pour_box_bottom_stl is None
    assert result.pour_box_top_stl is None
    assert result.clamp_shell_bottom_stl is None
    assert result.clamp_shell_top_stl is None
    assert result.draft_check is not None
    assert result.draft_check.releasable is True


def test_make_mold_form_fitting_golden_path(monkeypatch, tmp_path):
    monkeypatch.setattr(mold.shutil, "which", _fake_which_blender)
    monkeypatch.setattr(
        mold.subprocess,
        "run",
        _make_fake_blender_run(
            succeed=True,
            part_keys=("skin_pour_bottom", "skin_pour_top", "support_jacket_bottom", "support_jacket_top"),
        ),
    )

    input_stl = tmp_path / "model.stl"
    input_stl.write_bytes(b"solid fake\nendsolid fake\n")

    result = make_mold(input_stl, tmp_path / "out", mode="form_fitting")

    assert Path(result.skin_pour_bottom_stl).is_file()
    assert Path(result.skin_pour_top_stl).is_file()
    assert Path(result.support_jacket_bottom_stl).is_file()
    assert Path(result.support_jacket_top_stl).is_file()
    assert result.pour_box_bottom_stl is None
    assert result.direct_mold_bottom_stl is None
    # FR-4's automatic draft warning only runs for direct_cast.
    assert result.draft_check is None


def test_make_mold_form_fitting_passes_shell_params_as_cli_args(monkeypatch, tmp_path):
    captured = {}

    def _fake_run(cmd, capture_output, text, timeout):
        captured["cmd"] = cmd
        return _make_fake_blender_run(
            succeed=True,
            part_keys=("skin_pour_bottom", "skin_pour_top", "support_jacket_bottom", "support_jacket_top"),
        )(cmd, capture_output, text, timeout)

    monkeypatch.setattr(mold.shutil, "which", _fake_which_blender)
    monkeypatch.setattr(mold.subprocess, "run", _fake_run)

    input_stl = tmp_path / "model.stl"
    input_stl.write_bytes(b"solid fake\nendsolid fake\n")

    make_mold(
        input_stl,
        tmp_path / "out",
        mode="form_fitting",
        shell_thickness_mm=2.5,
        skin_pour_wall_mm=3.5,
        support_jacket_wall_mm=6.0,
    )

    assert _arg_after(captured["cmd"], "--mode") == "form_fitting"
    assert _arg_after(captured["cmd"], "--shell-thickness-mm") == "2.5"
    assert _arg_after(captured["cmd"], "--skin-pour-wall-mm") == "3.5"
    assert _arg_after(captured["cmd"], "--support-jacket-wall-mm") == "6.0"


def test_make_mold_hollow_cast_golden_path(monkeypatch, tmp_path):
    monkeypatch.setattr(mold.shutil, "which", _fake_which_blender)
    monkeypatch.setattr(
        mold.subprocess,
        "run",
        _make_fake_blender_run(
            succeed=True,
            part_keys=("hollow_cast_bottom", "hollow_cast_top", "hollow_cast_core"),
        ),
    )

    input_stl = tmp_path / "model.stl"
    input_stl.write_bytes(b"solid fake\nendsolid fake\n")

    result = make_mold(input_stl, tmp_path / "out", mode="hollow_cast")

    assert Path(result.hollow_cast_bottom_stl).is_file()
    assert Path(result.hollow_cast_top_stl).is_file()
    assert Path(result.hollow_cast_core_stl).is_file()
    assert result.direct_mold_bottom_stl is None
    assert result.pour_box_bottom_stl is None
    assert result.skin_pour_bottom_stl is None
    # FR-4's automatic draft warning only runs for direct_cast, not hollow_cast.
    assert result.draft_check is None


def test_make_mold_hollow_cast_passes_cast_wall_thickness_as_cli_arg(monkeypatch, tmp_path):
    captured = {}

    def _fake_run(cmd, capture_output, text, timeout):
        captured["cmd"] = cmd
        return _make_fake_blender_run(
            succeed=True,
            part_keys=("hollow_cast_bottom", "hollow_cast_top", "hollow_cast_core"),
        )(cmd, capture_output, text, timeout)

    monkeypatch.setattr(mold.shutil, "which", _fake_which_blender)
    monkeypatch.setattr(mold.subprocess, "run", _fake_run)

    input_stl = tmp_path / "model.stl"
    input_stl.write_bytes(b"solid fake\nendsolid fake\n")

    make_mold(
        input_stl,
        tmp_path / "out",
        mode="hollow_cast",
        direct_mold_wall_mm=4.0,
        cast_wall_thickness_mm=2.5,
    )

    assert _arg_after(captured["cmd"], "--mode") == "hollow_cast"
    assert _arg_after(captured["cmd"], "--direct-mold-wall-mm") == "4.0"
    assert _arg_after(captured["cmd"], "--cast-wall-thickness-mm") == "2.5"


def test_make_mold_direct_cast_surfaces_draft_warning(monkeypatch, tmp_path):
    """A direct_cast mold with an undercut still generates successfully (non-blocking) but carries the warning."""
    island = ProblemFaceIsland(
        id=0,
        face_indices=[0, 1],
        centroid=(0.5, 0.5, 0.5),
        face_count=2,
        min_draft_angle_deg=-10.0,
        classification="undercut",
    )
    island_report = _make_draft_report(releasable=False, problem_islands=[island])
    monkeypatch.setattr(mold, "_analyze_draft", lambda path, **kwargs: island_report)
    monkeypatch.setattr(mold.shutil, "which", _fake_which_blender)
    monkeypatch.setattr(
        mold.subprocess,
        "run",
        _make_fake_blender_run(succeed=True, part_keys=("direct_mold_bottom", "direct_mold_top")),
    )

    input_stl = tmp_path / "model.stl"
    input_stl.write_bytes(b"solid fake\nendsolid fake\n")

    result = make_mold(input_stl, tmp_path / "out", mode="direct_cast")

    assert Path(result.direct_mold_bottom_stl).is_file()  # generation still succeeded
    assert result.draft_check.releasable is False
    assert len(result.draft_check.problem_islands) == 1


def test_make_mold_direct_cast_draft_warning_failure_is_non_fatal(monkeypatch, tmp_path):
    """If the draft check itself fails, mold generation must still succeed with draft_check=None."""

    def _failing_analyze_draft(path, **kwargs):
        raise mold.DraftAnalysisError("boom")

    monkeypatch.setattr(mold, "_analyze_draft", _failing_analyze_draft)
    monkeypatch.setattr(mold.shutil, "which", _fake_which_blender)
    monkeypatch.setattr(
        mold.subprocess,
        "run",
        _make_fake_blender_run(succeed=True, part_keys=("direct_mold_bottom", "direct_mold_top")),
    )

    input_stl = tmp_path / "model.stl"
    input_stl.write_bytes(b"solid fake\nendsolid fake\n")

    result = make_mold(input_stl, tmp_path / "out", mode="direct_cast")

    assert Path(result.direct_mold_bottom_stl).is_file()
    assert result.draft_check is None


def test_make_mold_direct_cast_passes_mode_and_wall_as_cli_args(monkeypatch, tmp_path):
    captured = {}

    def _fake_run(cmd, capture_output, text, timeout):
        captured["cmd"] = cmd
        return _make_fake_blender_run(succeed=True, part_keys=("direct_mold_bottom", "direct_mold_top"))(
            cmd, capture_output, text, timeout
        )

    monkeypatch.setattr(mold.shutil, "which", _fake_which_blender)
    monkeypatch.setattr(mold.subprocess, "run", _fake_run)

    input_stl = tmp_path / "model.stl"
    input_stl.write_bytes(b"solid fake\nendsolid fake\n")

    make_mold(input_stl, tmp_path / "out", mode="direct_cast", direct_mold_wall_mm=7.5)

    assert _arg_after(captured["cmd"], "--mode") == "direct_cast"
    assert _arg_after(captured["cmd"], "--direct-mold-wall-mm") == "7.5"


def test_make_mold_passes_params_through_as_cli_args(monkeypatch, tmp_path):
    captured = {}

    def _fake_run(cmd, capture_output, text, timeout):
        captured["cmd"] = cmd
        return _make_fake_blender_run(succeed=True)(cmd, capture_output, text, timeout)

    monkeypatch.setattr(mold.shutil, "which", _fake_which_blender)
    monkeypatch.setattr(mold.subprocess, "run", _fake_run)

    input_stl = tmp_path / "model.stl"
    input_stl.write_bytes(b"solid fake\nendsolid fake\n")

    make_mold(input_stl, tmp_path / "out", clearance_mm=5.0, key_diameter_mm=3.0)

    assert _arg_after(captured["cmd"], "--clearance-mm") == "5.0"
    assert _arg_after(captured["cmd"], "--key-diameter-mm") == "3.0"
    assert _arg_after(captured["cmd"], "--max-dimension-mm") == str(settings.max_mold_dimension_mm)


def test_make_mold_passes_parting_axis_and_offset_as_cli_args(monkeypatch, tmp_path):
    captured = {}

    def _fake_run(cmd, capture_output, text, timeout):
        captured["cmd"] = cmd
        return _make_fake_blender_run(succeed=True)(cmd, capture_output, text, timeout)

    monkeypatch.setattr(mold.shutil, "which", _fake_which_blender)
    monkeypatch.setattr(mold.subprocess, "run", _fake_run)

    input_stl = tmp_path / "model.stl"
    input_stl.write_bytes(b"solid fake\nendsolid fake\n")

    make_mold(input_stl, tmp_path / "out", parting_axis="x", parting_offset_mm=1.5)

    assert _arg_after(captured["cmd"], "--parting-axis") == "x"
    assert _arg_after(captured["cmd"], "--parting-offset-mm") == "1.5"


def test_make_mold_rejects_bad_parting_axis_without_calling_blender(monkeypatch, tmp_path):
    called = False

    def _should_not_run(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(mold.subprocess, "run", _should_not_run)
    input_stl = tmp_path / "model.stl"
    input_stl.write_bytes(b"solid fake\nendsolid fake\n")

    with pytest.raises(MoldError, match="parting_axis must be"):
        make_mold(input_stl, tmp_path / "out", parting_axis="w")
    assert called is False


def test_make_mold_populates_cavity_volume_cm3_from_report(monkeypatch, tmp_path):
    monkeypatch.setattr(mold.shutil, "which", _fake_which_blender)
    monkeypatch.setattr(mold.subprocess, "run", _make_fake_blender_run(succeed=True, cavity_volume_mm3=6840.0))

    input_stl = tmp_path / "model.stl"
    input_stl.write_bytes(b"solid fake\nendsolid fake\n")

    result = make_mold(input_stl, tmp_path / "out")

    assert result.cavity_volume_cm3 == pytest.approx(6.84)


@pytest.mark.parametrize(
    "bad_param",
    [
        "clearance_mm",
        "pour_box_wall_mm",
        "clamp_wall_mm",
        "bolt_hole_diameter_mm",
        "direct_mold_wall_mm",
        "shell_thickness_mm",
        "skin_pour_wall_mm",
        "support_jacket_wall_mm",
        "cast_wall_thickness_mm",
    ],
)
def test_make_mold_rejects_non_positive_params_without_calling_blender(monkeypatch, tmp_path, bad_param):
    called = False

    def _should_not_run(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(mold.subprocess, "run", _should_not_run)
    input_stl = tmp_path / "model.stl"
    input_stl.write_bytes(b"solid fake\nendsolid fake\n")

    with pytest.raises(MoldError, match="must be greater than 0"):
        make_mold(input_stl, tmp_path / "out", **{bad_param: 0.0})
    assert called is False


def test_make_mold_missing_binary_raises_clear_error(monkeypatch, tmp_path):
    monkeypatch.setattr(mold.shutil, "which", lambda _name: None)
    input_stl = tmp_path / "model.stl"
    input_stl.write_bytes(b"solid fake\nendsolid fake\n")

    with pytest.raises(MoldError, match="not found on PATH"):
        make_mold(input_stl, tmp_path / "out")


def test_make_mold_propagates_blender_reported_error(monkeypatch, tmp_path):
    monkeypatch.setattr(mold.shutil, "which", _fake_which_blender)
    monkeypatch.setattr(mold.subprocess, "run", _make_fake_blender_run(succeed=False))

    input_stl = tmp_path / "model.stl"
    input_stl.write_bytes(b"solid fake\nendsolid fake\n")

    with pytest.raises(MoldError, match="boom"):
        make_mold(input_stl, tmp_path / "out")


def test_make_mold_auto_repairs_likely_defect_holes(monkeypatch, tmp_path):
    """A LIKELY_DEFECT hole should be auto-repaired (FR-2), and the repaired copy fed to Blender instead
    of the original, with the closed hole id surfaced on the result."""
    holes = [
        Hole(
            id=3,
            vertex_indices=[0, 1, 2],
            centroid=(0.5, 0.5, 0.0),
            area=1.0,
            perimeter=4.0,
            planarity=1.0,
            classification=HoleClassification.LIKELY_DEFECT,
        )
    ]
    monkeypatch.setattr(mold, "_analyze_mesh", lambda path: _make_watertight_report(is_watertight=False, holes=holes))

    repaired_paths = {}

    def _fake_repair(input_path, hole_ids, output_path):
        Path(output_path).write_bytes(b"solid repaired\nendsolid repaired\n")
        repaired_paths["input"] = input_path
        repaired_paths["output"] = output_path
        repaired_paths["hole_ids"] = hole_ids
        return RepairResult(output_path=output_path, closed_hole_ids=hole_ids, is_watertight=True)

    monkeypatch.setattr(mold, "_repair_mesh", _fake_repair)

    captured = {}

    def _fake_run(cmd, capture_output, text, timeout):
        captured["cmd"] = cmd
        return _make_fake_blender_run(succeed=True)(cmd, capture_output, text, timeout)

    monkeypatch.setattr(mold.shutil, "which", _fake_which_blender)
    monkeypatch.setattr(mold.subprocess, "run", _fake_run)

    input_stl = tmp_path / "model.stl"
    input_stl.write_bytes(b"solid fake\nendsolid fake\n")

    result = make_mold(input_stl, tmp_path / "out")

    assert repaired_paths["hole_ids"] == [3]
    assert result.repaired_hole_ids == [3]
    # The repaired copy - not the original - must be what Blender actually receives.
    assert _arg_after(captured["cmd"], "--input") == repaired_paths["output"]


def test_make_mold_fails_fast_when_no_confidently_repairable_holes(monkeypatch, tmp_path):
    holes = [
        Hole(
            id=1,
            vertex_indices=[0, 1, 2],
            centroid=(0.5, 0.5, 0.0),
            area=1.0,
            perimeter=4.0,
            planarity=1.0,
            classification=HoleClassification.AMBIGUOUS,
        )
    ]
    monkeypatch.setattr(mold, "_analyze_mesh", lambda path: _make_watertight_report(is_watertight=False, holes=holes))
    monkeypatch.setattr(mold.shutil, "which", _fake_which_blender)

    called = False

    def _should_not_run(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(mold.subprocess, "run", _should_not_run)
    input_stl = tmp_path / "model.stl"
    input_stl.write_bytes(b"solid fake\nendsolid fake\n")

    with pytest.raises(MoldError, match="not watertight"):
        make_mold(input_stl, tmp_path / "out")
    assert called is False


def test_make_mold_fails_fast_when_repair_leaves_holes_remaining(monkeypatch, tmp_path):
    holes = [
        Hole(
            id=2,
            vertex_indices=[0, 1, 2],
            centroid=(0.5, 0.5, 0.0),
            area=1.0,
            perimeter=4.0,
            planarity=1.0,
            classification=HoleClassification.LIKELY_DEFECT,
        )
    ]
    monkeypatch.setattr(mold, "_analyze_mesh", lambda path: _make_watertight_report(is_watertight=False, holes=holes))

    def _fake_repair(input_path, hole_ids, output_path):
        Path(output_path).write_bytes(b"solid repaired\nendsolid repaired\n")
        return RepairResult(
            output_path=output_path, closed_hole_ids=hole_ids, is_watertight=False, remaining_holes=holes
        )

    monkeypatch.setattr(mold, "_repair_mesh", _fake_repair)
    monkeypatch.setattr(mold.shutil, "which", _fake_which_blender)

    called = False

    def _should_not_run(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(mold.subprocess, "run", _should_not_run)
    input_stl = tmp_path / "model.stl"
    input_stl.write_bytes(b"solid fake\nendsolid fake\n")

    with pytest.raises(MoldError, match="hole\\(s\\) remain"):
        make_mold(input_stl, tmp_path / "out")
    assert called is False
