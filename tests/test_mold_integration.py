"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/tests/test_mold_integration.py
Description: End-to-end test for mold.py against a real Blender install -
    generates a mold for a known unit-cube fixture and checks the parts'
    bounding boxes have the expected size relationships (silicone_block:
    pour box bigger than the cube, clamp shell bigger than the pour box;
    direct_cast: both halves watertight and correctly sized around the
    cube; form_fitting: all 4 parts watertight, support jacket bigger
    than the skin-pour tool; hollow_cast: outer mold watertight and
    correctly sized, core watertight and strictly smaller than the
    original cube). Skipped automatically when no `blender` binary is on
    PATH (set
    BLENDER_BINARY to point at one otherwise). The mocked-subprocess
    plumbing tests in test_mold.py always run; this proves the actual
    boolean/bmesh geometry in blender_scripts/make_mold.py is correct,
    which a mock can't do.

    Bounding-box size is a a deliberately coarse check - it can't tell you
    the cavity is genuinely *open* (a sealed box would have an identical
    outer bbox). That was verified separately, live, via ray-casting into
    the cavity from a real Blender session during development (see
    docs/adr/0005-two-piece-silicone-mold-and-clamp-shell.md) - not
    re-checked on every test run since bmesh ray-casting from a pytest
    fixture is a lot of machinery for a regression a bbox/watertightness
    check would also catch if a future change broke the boolean ops
    entirely (e.g. a part missing or degenerate).
Inputs: The watertight_cube_stl fixture from conftest.py (a
    dependency-free hand-written unit-cube STL - no Blender needed to
    *generate* it, only to build the mold from it).
Outputs: N/A (test module).
Troubleshooting:
    - "Blender executable not found": install Blender or export
      BLENDER_BINARY=/path/to/blender before running pytest; this test
      skips rather than fails without it.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from threedprompt.mold import MoldError, _blender_binary_path, make_mold
from threedprompt.watertight import analyze_mesh

pytestmark = pytest.mark.skipif(shutil.which("blender") is None, reason="blender binary not found on PATH")


def _hits_solid(stl_path: str, x: float, y: float) -> bool:
    """
    Ray-casts straight down through (x, y) into stl_path and reports
    whether it hits solid material - False means a hole passes all the
    way through at that XY location. Used by FR-7's vent-placement test
    (see docs/adr/0011-geometry-aware-vent-placement.md) since a hole's
    position can't be read off a bounding box the way overall size can.
    """
    script = f"""
import bpy
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.wm.stl_import(filepath=r"{stl_path}")
obj = bpy.context.selected_objects[0]
success, loc, normal, idx = obj.ray_cast(({x}, {y}, 1000.0), (0.0, 0.0, -1.0))
print("RAY_RESULT:" + str(success))
"""
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(script)
        script_path = f.name
    try:
        result = subprocess.run(
            [_blender_binary_path(), "--background", "--python", script_path],
            capture_output=True,
            text=True,
            timeout=60,
        )
    finally:
        Path(script_path).unlink(missing_ok=True)
    for line in result.stdout.splitlines():
        if line.startswith("RAY_RESULT:"):
            return line.split(":", 1)[1] == "True"
    raise AssertionError(f"ray-cast helper produced no RAY_RESULT line; stderr:\n{result.stderr}")


def test_make_mold_produces_four_watertight_parts_sized_correctly(watertight_cube_stl, tmp_path):
    result = make_mold(
        watertight_cube_stl,
        tmp_path / "out",
        clearance_mm=5.0,
        pour_box_wall_mm=3.0,
        clamp_wall_mm=4.0,
        clamp_flange_width_mm=8.0,
    )

    parts = {
        "pour_box_bottom": result.pour_box_bottom_stl,
        "pour_box_top": result.pour_box_top_stl,
        "clamp_shell_bottom": result.clamp_shell_bottom_stl,
        "clamp_shell_top": result.clamp_shell_top_stl,
    }
    reports = {name: analyze_mesh(path) for name, path in parts.items()}

    # Every part must itself be a watertight, printable solid - confirmed
    # live during development that this holds even though each half's
    # cavity is a genuine open pocket (verified separately by ray-casting
    # into it, see the module docstring): the boolean solver represents
    # an annulus-shaped cut face as a single n-gon-with-a-hole, which
    # doesn't produce the boundary edges a torn/incomplete shell would.
    for name, report in reports.items():
        assert report.is_watertight, f"{name} is not watertight: {[h.reason for h in report.holes]}"
        assert report.vertex_count > 8, f"{name} looks unmodified (too few vertices for a shelled+keyed/holed part)"

    # Cube is 1x1x1mm; pour box wall 3mm + 5mm clearance on each side ->
    # footprint should be noticeably larger than the cube alone.
    pour_bbox = reports["pour_box_bottom"].bounding_box
    pour_size_x = pour_bbox.max[0] - pour_bbox.min[0]
    assert pour_size_x > 1.0 + 2 * 5.0, "pour box footprint should be cube + 2*clearance + 2*wall, not just the cube"

    # Clamp shell adds a wider wall (4mm vs 3mm) plus an 8mm flange on
    # every side, so its footprint must exceed the pour box's.
    clamp_bbox = reports["clamp_shell_bottom"].bounding_box
    clamp_size_x = clamp_bbox.max[0] - clamp_bbox.min[0]
    assert clamp_size_x > pour_size_x, "clamp shell (thicker wall + flange) should be larger than the pour box"

    # Bottom and top halves should meet close to the cube's own vertical
    # midpoint (z=0.5, the parting line) - not exactly at it, since the
    # registration keys deliberately protrude (bottom) / recess (top)
    # past that line by up to their radius (clamped to 40% of
    # pour_box_wall_mm=3.0, so up to 1.2mm here).
    assert reports["pour_box_bottom"].bounding_box.max[2] == pytest.approx(0.5, abs=1.5)
    assert reports["pour_box_top"].bounding_box.min[2] == pytest.approx(0.5, abs=1.5)

    # FR-8: cavity is a (1+2*5)^3 = 11^3 = 1331mm3 box -> 1.331cm3.
    assert result.cavity_volume_cm3 == pytest.approx(1.331, abs=0.01)


def test_make_mold_rejects_oversized_result(watertight_cube_stl, tmp_path):
    from threedprompt.mold import MoldError

    with pytest.raises(MoldError, match="MAX_MOLD_DIMENSION_MM"):
        make_mold(watertight_cube_stl, tmp_path / "out", clearance_mm=500.0)


def test_make_mold_direct_cast_produces_two_watertight_halves_shaped_to_the_model(watertight_cube_stl, tmp_path):
    result = make_mold(
        watertight_cube_stl,
        tmp_path / "out",
        mode="direct_cast",
        direct_mold_wall_mm=3.0,
        clamp_flange_width_mm=8.0,
        # Cube is 1x1x1mm; a pour hole must be smaller than the cavity's own
        # footprint (see _add_pour_holes's validation) so the defaults (10mm/
        # 4mm) don't apply here - scale down to fit.
        sprue_diameter_mm=0.4,
        vent_diameter_mm=0.2,
    )

    assert result.pour_box_bottom_stl is None
    assert result.clamp_shell_bottom_stl is None

    parts = {
        "direct_mold_bottom": result.direct_mold_bottom_stl,
        "direct_mold_top": result.direct_mold_top_stl,
    }
    reports = {name: analyze_mesh(path) for name, path in parts.items()}

    # Each half must be its own watertight, printable solid, exactly like
    # silicone_block's parts - even though its cavity is the model's own
    # mesh rather than a box (see build_direct_cast_half()'s docstring for
    # why that still produces a clean open cut at the parting line).
    for name, report in reports.items():
        assert report.is_watertight, f"{name} is not watertight: {[h.reason for h in report.holes]}"

    # Cube is 1x1x1mm; direct_mold_wall_mm=3 -> outer footprint (before the
    # flange) is cube + 2*wall = 7mm on a side, plus 2*8mm flange = 23mm.
    bottom_bbox = reports["direct_mold_bottom"].bounding_box
    bottom_size_x = bottom_bbox.max[0] - bottom_bbox.min[0]
    assert bottom_size_x == pytest.approx(23.0, abs=0.5)

    # FR-4's automatic, real (not mocked) draft check on a plain cube's
    # vertical walls: not releasable at the 2-degree default threshold -
    # end-to-end proof that mold.py -> draft_analysis.py -> a second real
    # Blender subprocess actually wires together, not just each piece in
    # isolation (see test_draft_analysis_integration.py for the geometry
    # itself, and test_mold.py for the mocked non-blocking-warning cases).
    assert result.draft_check is not None
    assert result.draft_check.releasable is False

    # No release tolerance is applied in v1, so the cavity's own footprint
    # (ignoring wall/flange) should match the cube exactly - verified
    # separately via ray-casting in .scratch_moldtest/debug_direct_cast.py
    # during development; here we just check the halves meet at the
    # cube's own vertical midpoint (no keys to offset this, unlike
    # silicone_block).
    assert reports["direct_mold_bottom"].bounding_box.max[2] == pytest.approx(0.5, abs=0.01)
    assert reports["direct_mold_top"].bounding_box.min[2] == pytest.approx(0.5, abs=0.01)


def test_make_mold_direct_cast_rejects_oversized_result(watertight_cube_stl, tmp_path):
    with pytest.raises(MoldError, match="MAX_MOLD_DIMENSION_MM"):
        make_mold(watertight_cube_stl, tmp_path / "out", mode="direct_cast", direct_mold_wall_mm=500.0)


def test_make_mold_direct_cast_reports_cavity_volume_cm3(watertight_cube_stl, tmp_path):
    """Cube is 1x1x1mm -> cavity_volume_cm3 should be the cube's own volume, 0.001 cm3 (1000mm3)."""
    result = make_mold(
        watertight_cube_stl,
        tmp_path / "out",
        mode="direct_cast",
        direct_mold_wall_mm=3.0,
        sprue_diameter_mm=0.4,
        vent_diameter_mm=0.2,
    )
    assert result.cavity_volume_cm3 == pytest.approx(0.001, abs=1e-6)


def test_make_mold_direct_cast_parting_offset_produces_asymmetric_halves(watertight_cube_stl, tmp_path):
    """
    FR-6: a 0.2mm parting_offset_mm on the 1x1x1mm cube (parting axis z,
    default midpoint 0.5) should move the split to z=0.7, making the
    bottom half's Z span 0.2mm taller than a symmetric split and the top
    half's 0.2mm shorter - see docs/adr/0010-configurable-parting-axis-and-volume-reporting.md.
    """
    result = make_mold(
        watertight_cube_stl,
        tmp_path / "out",
        mode="direct_cast",
        direct_mold_wall_mm=3.0,
        parting_offset_mm=0.2,
        sprue_diameter_mm=0.4,
        vent_diameter_mm=0.2,
    )
    bottom_report = analyze_mesh(result.direct_mold_bottom_stl)
    top_report = analyze_mesh(result.direct_mold_top_stl)

    # Outer Z envelope: bottom spans [parting_z - bottom_half_extent - wall, parting_z] = [-3, 0.7],
    # top spans [parting_z, parting_z + top_half_extent + wall] = [0.7, 3.3].
    assert bottom_report.bounding_box.max[2] == pytest.approx(0.7, abs=0.01)
    assert top_report.bounding_box.min[2] == pytest.approx(0.7, abs=0.01)
    bottom_span = bottom_report.bounding_box.max[2] - bottom_report.bounding_box.min[2]
    top_span = top_report.bounding_box.max[2] - top_report.bounding_box.min[2]
    assert bottom_span - top_span == pytest.approx(0.4, abs=0.02)  # 2 * offset


def test_make_mold_direct_cast_rejects_out_of_range_parting_offset(watertight_cube_stl, tmp_path):
    with pytest.raises(MoldError, match="outside the model's own range"):
        make_mold(watertight_cube_stl, tmp_path / "out", mode="direct_cast", parting_offset_mm=10.0)


def test_make_mold_direct_cast_parting_axis_x_produces_watertight_rotated_mold(watertight_cube_stl, tmp_path):
    """
    FR-6: parting_axis='x' rotates the model internally before building
    (see _rotate_axis_to_z()) - the exported parts stay in that rotated
    frame (ADR 0010), so this only checks the result is still a valid,
    correctly-sized, watertight mold, not that it matches the original
    orientation.
    """
    result = make_mold(
        watertight_cube_stl,
        tmp_path / "out",
        mode="direct_cast",
        direct_mold_wall_mm=3.0,
        parting_axis="x",
        sprue_diameter_mm=0.4,
        vent_diameter_mm=0.2,
    )
    bottom_report = analyze_mesh(result.direct_mold_bottom_stl)
    top_report = analyze_mesh(result.direct_mold_top_stl)
    assert bottom_report.is_watertight
    assert top_report.is_watertight
    # A unit cube is symmetric under axis rotation, so the size math is identical to the z-axis case.
    assert bottom_report.bounding_box.max[2] == pytest.approx(0.5, abs=0.01)


def test_make_mold_form_fitting_produces_four_watertight_parts_sized_correctly(watertight_cube_stl, tmp_path):
    """
    Cube is 1x1x1mm. The offset-along-normals cavity source grows the
    cube's corners by shell_thickness_mm/sqrt(3) per axis (not the full
    shell_thickness_mm - see docs/adr/0008-form-fitting-thin-shell-mold.md
    for why), so with shell_thickness_mm=3.0 the offset object's own
    footprint is 1 + 2*(3/sqrt(3)) ~= 4.46mm/side. Each half's own
    footprint is then that cavity size + 2*its own wall thickness:
    skin_pour_wall_mm=3.0 -> ~10.46mm (no flange, like silicone_block's
    pour box). The support jacket also gets the bolted flange (like
    silicone_block's clamp shell), so support_jacket_wall_mm=5.0 +
    clamp_flange_width_mm=8.0 -> ~30.46mm.
    """
    result = make_mold(
        watertight_cube_stl,
        tmp_path / "out",
        mode="form_fitting",
        shell_thickness_mm=3.0,
        skin_pour_wall_mm=3.0,
        support_jacket_wall_mm=5.0,
        clamp_flange_width_mm=8.0,
        # Offset shell footprint is ~4.46mm/side (see docstring below) - a
        # pour hole must be smaller than that, so scale down from the 10mm/
        # 4mm defaults.
        sprue_diameter_mm=2.0,
        vent_diameter_mm=1.0,
    )

    assert result.pour_box_bottom_stl is None
    assert result.direct_mold_bottom_stl is None
    # FR-4's automatic draft warning only runs for direct_cast.
    assert result.draft_check is None

    parts = {
        "skin_pour_bottom": result.skin_pour_bottom_stl,
        "skin_pour_top": result.skin_pour_top_stl,
        "support_jacket_bottom": result.support_jacket_bottom_stl,
        "support_jacket_top": result.support_jacket_top_stl,
    }
    reports = {name: analyze_mesh(path) for name, path in parts.items()}

    for name, report in reports.items():
        assert report.is_watertight, f"{name} is not watertight: {[h.reason for h in report.holes]}"

    skin_bbox = reports["skin_pour_bottom"].bounding_box
    skin_size_x = skin_bbox.max[0] - skin_bbox.min[0]
    assert skin_size_x == pytest.approx(10.46, abs=0.5)

    jacket_bbox = reports["support_jacket_bottom"].bounding_box
    jacket_size_x = jacket_bbox.max[0] - jacket_bbox.min[0]
    assert jacket_size_x == pytest.approx(30.46, abs=0.5)
    assert jacket_size_x > skin_size_x, "support jacket (thicker wall + flange) should be larger than the skin tool"

    # FR-8: shell volume (offset solid minus the model) must be positive but far
    # smaller than the offset solid's own bulk - it's just the thin shell gap.
    assert 0 < result.cavity_volume_cm3 < 1.0


def test_make_mold_form_fitting_rejects_oversized_result(watertight_cube_stl, tmp_path):
    with pytest.raises(MoldError, match="MAX_MOLD_DIMENSION_MM"):
        make_mold(watertight_cube_stl, tmp_path / "out", mode="form_fitting", shell_thickness_mm=500.0)


def test_make_mold_hollow_cast_produces_watertight_outer_mold_and_smaller_core(watertight_cube_stl, tmp_path):
    """
    Cube is 1x1x1mm. The outer mold halves are built exactly like
    direct_cast's (see that test for the size math); the core is the
    same cube shrunk inward by cast_wall_thickness_mm along every vertex
    normal - not a uniform per-axis shrink for a low-poly cube (see
    docs/adr/0008-form-fitting-thin-shell-mold.md's corner-rounding
    finding, which applies here in reverse), so we only assert it's
    watertight, smaller than the original cube, non-degenerate, and
    roughly centered - not an exact expected size.
    """
    result = make_mold(
        watertight_cube_stl,
        tmp_path / "out",
        mode="hollow_cast",
        direct_mold_wall_mm=3.0,
        cast_wall_thickness_mm=0.1,
        clamp_flange_width_mm=8.0,
        # Outer mold cavity is the 1x1x1mm cube itself - scale the pour hole
        # down from the 10mm/4mm defaults to fit (see _add_pour_holes's
        # validation).
        sprue_diameter_mm=0.4,
        vent_diameter_mm=0.2,
    )

    assert result.direct_mold_bottom_stl is None
    assert result.pour_box_bottom_stl is None
    # FR-4's automatic draft warning only runs for direct_cast, not hollow_cast.
    assert result.draft_check is None

    parts = {
        "hollow_cast_bottom": result.hollow_cast_bottom_stl,
        "hollow_cast_top": result.hollow_cast_top_stl,
        "hollow_cast_core": result.hollow_cast_core_stl,
    }
    reports = {name: analyze_mesh(path) for name, path in parts.items()}

    for name, report in reports.items():
        assert report.is_watertight, f"{name} is not watertight: {[h.reason for h in report.holes]}"

    # Outer mold footprint (before the flange): cube + 2*direct_mold_wall_mm
    # = 7mm on a side, plus 2*clamp_flange_width_mm=8mm = 23mm - same math
    # as direct_cast's own integration test (it reuses the same halves).
    bottom_bbox = reports["hollow_cast_bottom"].bounding_box
    bottom_size_x = bottom_bbox.max[0] - bottom_bbox.min[0]
    assert bottom_size_x == pytest.approx(23.0, abs=0.5)

    # Core must be strictly smaller than the original 1mm cube on every
    # axis, and stay inside [0, 1] - a shrunk-inward, non-degenerate,
    # roughly-centered solid.
    core_bbox = reports["hollow_cast_core"].bounding_box
    for axis in range(3):
        assert core_bbox.min[axis] > 0.0
        assert core_bbox.max[axis] < 1.0
    core_size_x = core_bbox.max[0] - core_bbox.min[0]
    assert core_size_x < 1.0

    # FR-8: model volume (1mm3) minus the slightly-shrunk core's volume ->
    # a small positive shell volume, far less than the full 1mm3 cube.
    assert 0 < result.cavity_volume_cm3 < 0.001


def test_make_mold_direct_cast_vents_the_real_trapped_air_pocket(bumpy_box_stl, tmp_path):
    """
    FR-7: the bumpy_box_stl fixture (see conftest.py) has one top corner
    lifted to (8, -8, 9) - a real, off-center trapped-air pocket far
    enough from the sprue (centered at the cavity's own XY center, 0,0)
    to survive _find_vent_xy's exclude-radius filter. The vent should
    land there instead of at the old fixed 0.35*cavity_size offset
    (7, 7 for this 20x20mm cavity) - see
    docs/adr/0011-geometry-aware-vent-placement.md.
    """
    result = make_mold(
        bumpy_box_stl,
        tmp_path / "out",
        mode="direct_cast",
        direct_mold_wall_mm=3.0,
        sprue_diameter_mm=3.0,
        vent_diameter_mm=1.5,
    )

    top_path = result.direct_mold_top_stl
    assert _hits_solid(top_path, 0.0, 0.0) is False, "sprue hole should pass through the cavity center"
    assert _hits_solid(top_path, 8.0, -8.0) is False, "vent should be cut at the real trapped-air pocket"
    assert _hits_solid(top_path, 7.0, 7.0) is True, "the old fixed-offset location should be untouched now"


def test_make_mold_hollow_cast_rejects_oversized_result(watertight_cube_stl, tmp_path):
    with pytest.raises(MoldError, match="MAX_MOLD_DIMENSION_MM"):
        make_mold(watertight_cube_stl, tmp_path / "out", mode="hollow_cast", direct_mold_wall_mm=500.0)


def test_make_mold_fails_fast_on_non_watertight_input_with_no_confident_defect(cube_missing_one_triangle_stl, tmp_path):
    """
    FR-2's pre-flight check (mold._ensure_watertight_input) runs the real
    hole_classifier.py heuristic, not a mock - this fixture's single
    missing triangle happens to get classified INTENTIONAL_OPENING (a
    large, top-aligned planar gap looks like a deliberate cup-mouth/open-
    box-top shape to that heuristic, per ADR 0004), so it's deliberately
    NOT auto-repaired. Confirms the real end-to-end wiring refuses to
    hand Blender's boolean solver a non-watertight mesh it isn't
    confident about, rather than silently mangling the result - the
    auto-repair *success* path (a genuinely LIKELY_DEFECT hole) is
    covered by the mocked cases in test_mold.py since manufacturing a
    real fixture that classifies as a likely defect isn't worth the
    machinery here.
    """
    with pytest.raises(MoldError, match="not watertight"):
        make_mold(cube_missing_one_triangle_stl, tmp_path / "out")
