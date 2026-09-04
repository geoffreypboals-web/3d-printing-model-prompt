"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/src/mesh_repair/classifier.py
Description: Pure-Python heuristics that decide, for each boundary-edge
    loop (Hole) Blender found on a mesh, whether it looks like a deliberate
    opening the model was designed with (a cup's mouth, a box's open top,
    the open underside of a stand/base) or an unintentional gap caused by
    bad topology (a couple of stray vertices/polygons that never merged,
    per the trophy example this feature was built for). Has no dependency
    on bpy so it can be unit tested without Blender installed.
Inputs: A WatertightReport (see models.py) whose holes still carry the
    default AMBIGUOUS classification from the Blender analysis script.
Outputs: A new WatertightReport with each Hole's classification,
    confidence, and reason fields filled in.
Troubleshooting:
    - If real intentional openings (cup mouths, open box tops) keep
      getting flagged as defects, the model is probably small/low-poly
      enough that SIZE_REF or RAGGED_REF need loosening for that class of
      object — these constants are deliberately simple round numbers, not
      tuned against a large corpus.
    - If tiny stray-vertex gaps stop getting flagged, check that the
      Blender script is still reporting true per-vertex loop area/
      perimeter and not a degenerate 0-area loop (which sorts as
      "ambiguous" here rather than "defect" — see classify_hole below).
"""

from __future__ import annotations

import math
from copy import deepcopy

from .models import BoundingBox, Hole, HoleClassification, WatertightReport

# A hole covering at least this fraction of the model's total surface area
# maxes out the "size" signal toward "intentional opening". 2% is roughly
# the mouth of a mug relative to its total surface area.
SIZE_REF_FRACTION = 0.02

# A hole whose centroid falls within this fraction of the model's overall
# height from either the top or bottom bounding-box face counts as
# "cap-aligned" — the classic profile of an open container top or an open
# stand/base underside.
CAP_ALIGNMENT_FRACTION = 0.08

# perimeter^2 / (4*pi*area) is 1.0 for a perfect circle and grows for
# jagged/irregular boundaries. Above this, we treat the loop as ragged
# (a strong defect signal); this constant sets where "ragged" starts.
RAGGED_REF = 3.0

INTENTIONAL_THRESHOLD = 0.6
DEFECT_THRESHOLD = 0.35


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp value into [lo, hi]."""
    return max(lo, min(hi, value))


def _size_score(hole: Hole, total_surface_area: float) -> float:
    """0..1 score: bigger holes (relative to total surface area) score higher."""
    if total_surface_area <= 0:
        return 0.0
    fraction = hole.area / total_surface_area
    return _clamp(fraction / SIZE_REF_FRACTION)


def _position_score(hole: Hole, bbox: BoundingBox) -> float:
    """
    0..1 score: holes whose centroid sits near the top or bottom of the
    model's bounding box score higher, matching the container/base pattern
    the project owner described (open top of a cup, open underside of a
    stand).
    """
    height = bbox.size[2]
    if height <= 0:
        return 0.0
    z = hole.centroid[2]
    dist_from_top = abs(bbox.max[2] - z) / height
    dist_from_bottom = abs(z - bbox.min[2]) / height
    # "nearest" ranges 0 (touching a cap) .. 0.5 (dead center of the
    # model), never up to 1.0, so normalize the falloff against 0.5 —
    # not 1.0 — or a hole at the true middle would still score ~0.5
    # instead of the intended ~0.
    nearest = min(dist_from_top, dist_from_bottom)
    if nearest >= CAP_ALIGNMENT_FRACTION:
        return _clamp(1.0 - (nearest - CAP_ALIGNMENT_FRACTION) / (0.5 - CAP_ALIGNMENT_FRACTION))
    return 1.0


def _raggedness_penalty(hole: Hole) -> float:
    """
    0..1 penalty: jagged, high-perimeter-for-their-area loops (the
    "couple of misaligned polygons" case) score high here; clean
    round/rectangular openings score near 0. Degenerate (near-zero-area)
    loops are also treated as maximally ragged — a real opening always
    encloses some area, so a collapsed/sliver loop is a defect signal,
    not a "can't tell" one.
    """
    if hole.area <= 1e-9:
        return 1.0
    roundness = (hole.perimeter**2) / (4 * math.pi * hole.area)
    excess = max(0.0, roundness - 1.0)
    return _clamp(excess / RAGGED_REF)


def classify_hole(hole: Hole, bbox: BoundingBox, total_surface_area: float) -> Hole:
    """
    Score one hole and return a copy with classification/confidence/reason
    filled in. Does not mutate the input hole.
    """
    size = _size_score(hole, total_surface_area)
    position = _position_score(hole, bbox)
    planarity = _clamp(hole.planarity)
    raggedness = _raggedness_penalty(hole)

    # Size/position/planarity say how much a hole *looks* like a designed
    # opening; raggedness then dampens that multiplicatively rather than
    # just averaging in, because a real designed opening (a rim, an open
    # box face) is essentially always a clean, low-perimeter-for-its-area
    # shape — a highly jagged boundary should heavily discount an
    # otherwise "big and cap-aligned" score, not just shave a fixed amount
    # off it. This matters most on low-poly models, where a couple of
    # stray missing triangles can still cover a surprisingly large
    # fraction of the total surface area.
    base_score = 0.45 * size + 0.35 * position + 0.20 * planarity
    intentional_score = _clamp(base_score * (1.0 - 0.6 * raggedness))

    area_fraction = (hole.area / total_surface_area * 100) if total_surface_area > 0 else 0.0

    result = deepcopy(hole)
    if intentional_score >= INTENTIONAL_THRESHOLD:
        result.classification = HoleClassification.INTENTIONAL_OPENING
        result.confidence = intentional_score
        result.reason = (
            f"Large, {'top' if position >= 0.5 else 'well'}-aligned planar opening "
            f"({area_fraction:.2f}% of total surface area, {len(hole.vertex_indices)} "
            "boundary vertices) — looks like a deliberate opening such as a "
            "cup mouth, an open box top, or an open base underside."
        )
    elif intentional_score <= DEFECT_THRESHOLD:
        result.classification = HoleClassification.LIKELY_DEFECT
        result.confidence = 1.0 - intentional_score
        result.reason = (
            f"Small, isolated gap ({area_fraction:.3f}% of total surface area, "
            f"{len(hole.vertex_indices)} boundary vertices"
            + (", jagged boundary" if raggedness > 0.3 else "")
            + ") that doesn't line up with a natural opening — looks like "
            "unintentional geometry (misaligned or duplicate vertices "
            "leaving a void) rather than a designed feature."
        )
    else:
        result.classification = HoleClassification.AMBIGUOUS
        result.confidence = 1.0 - 2 * abs(intentional_score - 0.475)
        result.reason = (
            f"Moderate-sized opening ({area_fraction:.2f}% of total surface area) that "
            "doesn't clearly match either a designed opening or a stray defect — "
            "please confirm in the viewer whether this should stay open or be closed."
        )
    return result


def classify_report(report: WatertightReport) -> WatertightReport:
    """
    Return a copy of report with every hole's classification/confidence/
    reason filled in by classify_hole. flipped_normal_islands and all
    other fields pass through unchanged.
    """
    updated = deepcopy(report)
    updated.holes = [
        classify_hole(hole, report.bounding_box, report.total_surface_area) for hole in report.holes
    ]
    return updated
