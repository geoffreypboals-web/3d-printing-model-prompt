"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/tests/test_draft_analysis_integration.py
Description: End-to-end test for draft_analysis.py against a real
    Blender install - runs the real dot-product/island-grouping geometry
    from blender_scripts/analyze_draft.py against the same unit-cube
    fixture used elsewhere, confirming exactly what
    .scratch_moldtest/debug_draft.py and debug_draft_undercut.py verified
    live during development (see docs/adr/0007-draft-undercut-analysis.md):
    a plain vertical-walled cube's 4 side walls (8 triangles) group into
    one "insufficient_draft" island at 0 degrees while the top/bottom
    caps are correctly excluded (+90 degrees, ideal). Skipped
    automatically when no `blender` binary is on PATH (set BLENDER_BINARY
    to point at one otherwise). The mocked-subprocess plumbing tests in
    test_draft_analysis.py always run; this proves the actual geometry is
    correct, which a mock can't do.
Inputs: The watertight_cube_stl fixture from conftest.py.
Outputs: N/A (test module).
Troubleshooting:
    - "Blender executable not found": install Blender or export
      BLENDER_BINARY=/path/to/blender before running pytest; this test
      skips rather than fails without it.
"""

from __future__ import annotations

import shutil

import pytest

from threedprompt.draft_analysis import analyze_draft

pytestmark = pytest.mark.skipif(shutil.which("blender") is None, reason="blender binary not found on PATH")


def test_analyze_draft_flags_vertical_walls_on_unit_cube(watertight_cube_stl):
    report = analyze_draft(str(watertight_cube_stl), pull_axis="z", min_draft_angle_deg=2.0)

    assert report.releasable is False
    assert report.parting_coordinate == pytest.approx(0.5)
    assert len(report.problem_islands) == 1

    island = report.problem_islands[0]
    assert island.classification == "insufficient_draft"
    assert island.min_draft_angle_deg == pytest.approx(0.0, abs=1e-6)
    # The 4 side walls (2 triangles each) - not the 2 top-cap + 2
    # bottom-cap triangles, which are perfectly aligned with their own
    # half's pull direction and so must NOT be flagged.
    assert island.face_count == 8


def test_analyze_draft_releasable_with_a_lenient_threshold(watertight_cube_stl):
    # A 0-degree threshold means "only flag genuine undercuts" - a plain
    # vertical wall (exactly 0 degrees) no longer qualifies as "less
    # than" the threshold, so the cube should read fully releasable.
    report = analyze_draft(str(watertight_cube_stl), pull_axis="z", min_draft_angle_deg=0.0)

    assert report.releasable is True
    assert report.problem_islands == []
