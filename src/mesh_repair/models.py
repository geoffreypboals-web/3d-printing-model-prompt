"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/src/mesh_repair/models.py
Description: Plain-data types shared between the Blender-side analysis
    scripts, the Python service wrapper, and the viewer API. Kept free of
    any Blender (bpy) or web-framework imports so it can be unit tested and
    imported from both the "outside" (service.py, api.py) and reasoned
    about when reading the JSON emitted by the Blender scripts.
Inputs: N/A (pure data definitions).
Outputs: N/A (pure data definitions).
Troubleshooting:
    - If service.py fails to parse a Blender script's JSON output with a
      KeyError/TypeError, check that the field names here still match the
      keys written by blender_scripts/analyze_watertight.py and
      blender_scripts/close_holes.py — they are hand-kept in sync, not
      generated from a shared schema.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class HoleClassification(StrEnum):
    """Verdict the heuristic classifier assigns to one detected hole."""

    INTENTIONAL_OPENING = "intentional_opening"
    LIKELY_DEFECT = "likely_defect"
    AMBIGUOUS = "ambiguous"


@dataclass
class BoundingBox:
    """Axis-aligned min/max corners, in the mesh's local units."""

    min: tuple[float, float, float]
    max: tuple[float, float, float]

    @property
    def size(self) -> tuple[float, float, float]:
        """Return the (x, y, z) extents of the box."""
        return tuple(mx - mn for mn, mx in zip(self.min, self.max, strict=True))


@dataclass
class Hole:
    """
    One connected boundary-edge loop found on the mesh — a location where
    the surface is not watertight, either because it is a deliberate
    opening (e.g. the mouth of a cup) or an unintentional gap left by bad
    topology (e.g. two vertices that were supposed to merge but didn't).

    Attributes:
        id: Stable index into the analysis report's hole list, used to
            refer back to this hole when requesting a repair.
        vertex_indices: Mesh vertex indices making up the boundary loop,
            in loop order.
        centroid: Approximate center of the loop, in mesh local space.
        area: Approximate planar area of the loop if capped flat, in the
            mesh's native units squared.
        perimeter: Sum of boundary edge lengths.
        planarity: 0..1 score of how close the loop's vertices are to a
            single best-fit plane (1.0 = perfectly flat).
        classification: The heuristic verdict (see HoleClassification).
        confidence: 0..1 confidence in the classification.
        reason: Short human-readable explanation for the classification,
            shown to the user in the viewer.
    """

    id: int
    vertex_indices: list[int]
    centroid: tuple[float, float, float]
    area: float
    perimeter: float
    planarity: float
    classification: HoleClassification = HoleClassification.AMBIGUOUS
    confidence: float = 0.0
    reason: str = ""


@dataclass
class FlippedNormalIsland:
    """
    A connected group of faces whose normals point the opposite way from
    the rest of the surrounding surface — the "inverted shape" / unexpected
    wrinkle the project owner described, which often does not show up in
    a shaded viewport but breaks slicer watertightness checks.
    """

    id: int
    face_indices: list[int]
    centroid: tuple[float, float, float]
    face_count: int


@dataclass
class WatertightReport:
    """Full result of analyzing one mesh file for print-readiness."""

    source_path: str
    is_watertight: bool
    vertex_count: int
    face_count: int
    total_surface_area: float
    bounding_box: BoundingBox
    holes: list[Hole] = field(default_factory=list)
    flipped_normal_islands: list[FlippedNormalIsland] = field(default_factory=list)
    nonmanifold_junction_edge_count: int = 0
    blender_version: str = ""
    viewer_path: str = ""


@dataclass
class RepairResult:
    """Result of asking Blender to close a chosen set of holes."""

    output_path: str
    closed_hole_ids: list[int]
    is_watertight: bool
    remaining_holes: list[Hole] = field(default_factory=list)
    viewer_path: str = ""
