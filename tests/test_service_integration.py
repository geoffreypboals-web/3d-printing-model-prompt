"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/tests/test_service_integration.py
Description: End-to-end tests for src/mesh_repair/service.py against a
    real Blender install — analyze_mesh() on a known-good and a
    known-broken STL fixture, then repair_mesh() to confirm the broken
    one becomes watertight. Skipped automatically when no `blender`
    binary is on PATH (set BLENDER_BINARY to point at one otherwise),
    since not every environment running the unit tests will have Blender
    installed. The CI workflow does install Blender specifically so this
    file runs there — see .github/workflows/ci.yml.
Inputs: Fixtures from conftest.py (dependency-free hand-written STL
    files); no network access needed.
Outputs: None (pass/fail via pytest).
Troubleshooting:
    - "Blender executable not found": install Blender (see README.md /
      Dockerfile) or export BLENDER_BINARY=/path/to/blender before
      running pytest; these tests skip rather than fail without it, so a
      green run doesn't by itself prove Blender is even installed —
      check the test session's skip summary.
"""

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from mesh_repair import MeshRepairError, analyze_mesh, repair_mesh

pytestmark = pytest.mark.skipif(
    shutil.which("blender") is None, reason="blender binary not found on PATH"
)


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
    with pytest.raises(MeshRepairError, match="not found"):
        repair_mesh(str(cube_missing_one_triangle_stl), [99], str(tmp_path / "out.stl"))


def test_analyze_missing_file_raises_actionable_error(tmp_path):
    with pytest.raises(MeshRepairError, match="not found"):
        analyze_mesh(str(tmp_path / "does_not_exist.stl"))


def test_analyze_unsupported_extension_raises(tmp_path):
    bogus = tmp_path / "model.xyz"
    bogus.write_text("not a mesh")
    with pytest.raises(MeshRepairError, match="Unsupported"):
        analyze_mesh(str(bogus))
