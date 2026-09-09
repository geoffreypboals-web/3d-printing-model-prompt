"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/tests/test_watertight_integration.py
Description: End-to-end tests for watertight.py against a real Blender
    install - analyze_mesh() on a known-good and a known-broken STL
    fixture, then repair_mesh() to confirm the broken one becomes
    watertight. Skipped automatically when no `blender` binary is on
    PATH (set BLENDER_BINARY to point at one otherwise). The mocked-
    subprocess plumbing tests in test_watertight.py always run; these
    prove the actual bmesh hole-detection/repair algorithm is correct,
    which a mock can't do.
Inputs: Fixtures from conftest.py's write_binary_stl helper (a
    dependency-free hand-written STL writer - no Blender needed to
    *generate* these fixtures, only to analyze/repair them); no network
    access needed.
Outputs: N/A (test module).
Troubleshooting:
    - "Blender executable not found": install Blender (see README.md /
      Dockerfile) or export BLENDER_BINARY=/path/to/blender before
      running pytest; these tests skip rather than fail without it -
      check the test session's skip summary, a green run doesn't by
      itself prove Blender is even installed.
"""

from __future__ import annotations

import shutil

import pytest

from threedprompt.watertight import WatertightError, analyze_mesh, repair_mesh

pytestmark = pytest.mark.skipif(shutil.which("blender") is None, reason="blender binary not found on PATH")


def test_analyze_watertight_cube_reports_no_holes(watertight_cube_stl):
    report = analyze_mesh(str(watertight_cube_stl))
    assert report.is_watertight is True
    assert report.holes == []
    assert report.vertex_count == 8
    assert report.face_count == 12


def test_analyze_cube_with_gap_finds_exactly_one_hole(cube_missing_one_triangle_stl):
    report = analyze_mesh(str(cube_missing_one_triangle_stl))
    assert report.is_watertight is False
    assert len(report.holes) == 1
    assert len(report.holes[0].vertex_indices) == 3  # the missing triangle's 3 corners


def test_repair_closes_the_hole_and_becomes_watertight(cube_missing_one_triangle_stl, tmp_path):
    report = analyze_mesh(str(cube_missing_one_triangle_stl))
    assert report.is_watertight is False

    output_path = tmp_path / "repaired.stl"
    result = repair_mesh(str(cube_missing_one_triangle_stl), [report.holes[0].id], str(output_path))

    assert result.is_watertight is True
    assert result.remaining_holes == []
    assert output_path.is_file()

    # Prove it with a fresh, independent analysis of the written file too,
    # not just trusting repair_mesh's own report.
    follow_up = analyze_mesh(str(output_path))
    assert follow_up.is_watertight is True


def test_repair_rejects_unknown_hole_id(cube_missing_one_triangle_stl, tmp_path):
    with pytest.raises(WatertightError, match="not found"):
        repair_mesh(str(cube_missing_one_triangle_stl), [99], str(tmp_path / "out.stl"))


def test_repair_with_quad_remesh_stays_watertight(cube_missing_one_triangle_stl, tmp_path):
    """
    quad_target_faces is a topology/cosmetic pass (see _shared.quad_remesh's
    docstring) that runs after hole-filling -- confirms it doesn't undo the
    repair or break the export, on top of test_repair_closes_the_hole_...'s
    coverage of the plain (non-quad) repair path.

    Uses 200 as the target, not something tiny like 20 -- confirmed live
    that a target at or below a mesh's own natural face count (a cube's
    minimum is 6) can make QuadriFlow produce a genuinely degenerate
    result even _shared.quad_remesh's own fill_holes safety net can't
    cleanly recover; 50+ was reliable on this exact 11-triangle fixture in
    that same live check. This isn't a realistic use case anyway -- real
    calls target hundreds to thousands of faces on non-trivial meshes.
    """
    report = analyze_mesh(str(cube_missing_one_triangle_stl))
    output_path = tmp_path / "repaired_quad.stl"

    result = repair_mesh(
        str(cube_missing_one_triangle_stl), [report.holes[0].id], str(output_path), quad_target_faces=200
    )

    assert result.is_watertight is True
    assert output_path.is_file()
    follow_up = analyze_mesh(str(output_path))
    assert follow_up.is_watertight is True
