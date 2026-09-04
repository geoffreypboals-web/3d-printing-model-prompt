"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/tests/test_classifier.py
Description: Unit tests for src/mesh_repair/classifier.py — the pure-
    Python heuristics that decide whether a detected hole looks like a
    deliberate opening or an unintentional defect. Constructs Hole/
    BoundingBox dataclasses directly (no mesh file, no Blender) so these
    stay fast and deterministic; the Blender-backed end-to-end path is
    covered separately in test_service_integration.py.
Inputs: None (pytest discovers and runs these directly).
Outputs: None (pass/fail via pytest).
Troubleshooting:
    - A test here starts failing after tuning classifier.py's constants
      (SIZE_REF_FRACTION, RAGGED_REF, the score weights, etc.): that's
      expected when the tuning changes behavior at the edges these tests
      sit near — recompute the expected score by hand (each test's
      docstring/comments show the arithmetic) before assuming the test
      itself is wrong.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mesh_repair.classifier import classify_hole, classify_report  # noqa: E402
from mesh_repair.models import BoundingBox, Hole, HoleClassification, WatertightReport  # noqa: E402


def _bbox() -> BoundingBox:
    return BoundingBox(min=(-1.0, -1.0, -1.0), max=(1.0, 1.0, 1.0))


def test_large_top_aligned_planar_hole_is_intentional_opening():
    # Sits exactly at the top cap, covers 12.5% of total surface area,
    # perfectly planar, and its perimeter/area ratio matches a circle
    # (roundness ~= 1) -- every signal points the same way.
    hole = Hole(
        id=0,
        vertex_indices=[0, 1, 2, 3],
        centroid=(0.0, 0.0, 1.0),
        area=3.0,
        perimeter=6.14,  # ~= sqrt(4*pi*area), i.e. roundness ~= 1 (a circle)
        planarity=1.0,
    )
    result = classify_hole(hole, _bbox(), total_surface_area=24.0)
    assert result.classification == HoleClassification.INTENTIONAL_OPENING
    assert result.confidence > 0.9
    assert "opening" in result.reason.lower()
    # classify_hole must not mutate its input
    assert hole.classification == HoleClassification.AMBIGUOUS


def test_tiny_jagged_offcenter_hole_is_likely_defect():
    # Dead center on the model (as far from either cap as possible),
    # covers a fraction of a percent of the surface, and its boundary is
    # far longer than a clean shape of that area would need -- the
    # "couple of stray polygons" pattern this feature was built for.
    hole = Hole(
        id=0,
        vertex_indices=[10, 11, 12],
        centroid=(0.0, 0.0, 0.0),
        area=0.05,
        perimeter=3.0,
        planarity=0.9,
    )
    result = classify_hole(hole, _bbox(), total_surface_area=24.0)
    assert result.classification == HoleClassification.LIKELY_DEFECT
    assert result.confidence > 0.9
    assert "unintentional" in result.reason.lower()


def test_zero_total_surface_area_does_not_crash():
    hole = Hole(
        id=0,
        vertex_indices=[0, 1, 2],
        centroid=(0.0, 0.0, 0.0),
        area=1.0,
        perimeter=4.0,
        planarity=1.0,
    )
    result = classify_hole(hole, _bbox(), total_surface_area=0.0)
    # No natural-language area fraction is possible with 0 total area;
    # classify_hole must still return a valid classification, not raise.
    assert result.classification in HoleClassification


def test_zero_height_bbox_does_not_crash():
    flat_bbox = BoundingBox(min=(-1.0, -1.0, 0.0), max=(1.0, 1.0, 0.0))
    hole = Hole(
        id=0,
        vertex_indices=[0, 1, 2],
        centroid=(0.0, 0.0, 0.0),
        area=1.0,
        perimeter=4.0,
        planarity=1.0,
    )
    result = classify_hole(hole, flat_bbox, total_surface_area=10.0)
    assert result.classification in HoleClassification


def test_degenerate_zero_area_hole_is_treated_as_ragged_not_crashing():
    hole = Hole(
        id=0,
        vertex_indices=[0, 1],
        centroid=(0.0, 0.0, 0.0),
        area=0.0,
        perimeter=5.0,
        planarity=0.5,
    )
    result = classify_hole(hole, _bbox(), total_surface_area=24.0)
    assert result.classification in HoleClassification
    assert result.confidence >= 0.0


def test_classify_report_does_not_mutate_original_holes():
    original_hole = Hole(
        id=0,
        vertex_indices=[0, 1, 2, 3],
        centroid=(0.0, 0.0, 1.0),
        area=3.0,
        perimeter=6.14,
        planarity=1.0,
    )
    report = WatertightReport(
        source_path="model.stl",
        is_watertight=False,
        vertex_count=8,
        face_count=12,
        total_surface_area=24.0,
        bounding_box=_bbox(),
        holes=[original_hole],
    )
    result = classify_report(report)

    assert original_hole.classification == HoleClassification.AMBIGUOUS
    assert original_hole.confidence == 0.0
    assert result.holes[0].classification == HoleClassification.INTENTIONAL_OPENING
    assert result is not report
    assert result.holes[0] is not original_hole


def test_classify_report_preserves_hole_count_and_order():
    holes = [
        Hole(
            id=i,
            vertex_indices=[0, 1, 2],
            centroid=(0.0, 0.0, float(i)),
            area=0.1,
            perimeter=2.0,
            planarity=0.8,
        )
        for i in range(3)
    ]
    report = WatertightReport(
        source_path="model.stl",
        is_watertight=False,
        vertex_count=8,
        face_count=12,
        total_surface_area=24.0,
        bounding_box=_bbox(),
        holes=holes,
    )
    result = classify_report(report)
    assert [h.id for h in result.holes] == [0, 1, 2]
