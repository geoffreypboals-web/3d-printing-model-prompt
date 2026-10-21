"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/src/threedprompt/models.py
Description: Shared data types for the service - the classification
    result the router produces, the generation result a backend produces,
    and the Pydantic request/response schemas the HTTP API exposes.
Inputs: None at import time; instances are built by classifier.py,
    openscad_generator.py, blender_generator.py, thickness.py, and main.py.
Outputs: Type definitions imported throughout the package.
Troubleshooting:
    - If FastAPI rejects a request with a 422, check the request body
      against the Pydantic model here first - the error detail names the
      offending field.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class Complexity(StrEnum):
    """Which backend a prompt should be routed to."""

    SIMPLE = "simple"
    COMPLEX = "complex"


class ClassificationMethod(StrEnum):
    """Which stage of the hybrid classifier produced the final label."""

    HEURISTIC = "heuristic"
    LLM = "llm"
    LLM_FALLBACK_TO_HEURISTIC = "llm_fallback_to_heuristic"


@dataclass(frozen=True)
class ClassificationResult:
    """Output of classifier.classify_prompt()."""

    label: Complexity
    confidence: float
    method: ClassificationMethod
    reasoning: str


class Backend(StrEnum):
    """Which generation engine actually produced a model."""

    OPENSCAD = "openscad"
    BLENDER = "blender"
    CADQUERY = "cadquery"


@dataclass(frozen=True)
class GenerationResult:
    """Output of an OpenSCAD or Blender generation backend."""

    backend: Backend
    stl_path: str
    source_path: str | None
    """Path to the .scad source (OpenSCAD) or .py build script (Blender), if kept for regeneration."""
    source_kind: Literal["openscad_scad", "blender_bpy_script", "cadquery_py"] | None
    wall_thickness_param: str | None
    """Name of the source-level variable that controls wall thickness, if the backend exposed one."""


class GenerateRequest(BaseModel):
    """POST /generate request body."""

    prompt: str = Field(..., min_length=1, description="Natural-language description of the part to model.")
    wall_thickness_mm: float | None = Field(
        default=None, gt=0, description="Optional initial wall thickness in millimeters, if the part is a shell."
    )


class GenerateResponse(BaseModel):
    """POST /generate response body."""

    model_id: str
    backend: Backend
    classification_label: Complexity
    classification_confidence: float
    classification_method: ClassificationMethod
    classification_reasoning: str
    download_url: str


class ThickenRequest(BaseModel):
    """POST /models/{model_id}/thicken request body."""

    amount_mm: float = Field(..., gt=0, description="Amount, in millimeters, to increase wall thickness by.")
    quad_target_faces: int = Field(
        0,
        ge=0,
        le=1_000_000,
        description="0 (default) disables. When > 0, retopologizes the result into roughly this many "
        "quad-dominant faces via QuadriFlow after thickening -- mainly a topology/cosmetic pass (ignored "
        "on the regenerate-from-source path, which has no mesh to retopologize). Pick a value proportional "
        "to the mesh's real complexity: a target at or below its natural face count can leave it not "
        "watertight even with the repair pass that runs afterward.",
    )


class ThickenResponse(BaseModel):
    """POST /models/{model_id}/thicken and POST /thicken response body."""

    model_id: str
    method: Literal["regenerated_from_source", "mesh_shell"]
    download_url: str


@dataclass(frozen=True)
class MoldResult:
    """
    Output of mold.make_mold(). silicone_block mode populates the first
    4 fields; direct_cast mode populates only direct_mold_bottom_stl/
    direct_mold_top_stl; form_fitting mode populates only the
    skin_pour_*/support_jacket_* fields; hollow_cast mode populates only
    the hollow_cast_* fields. All fields not populated by the active mode
    stay None.
    repaired_hole_ids lists any watertight-defect holes auto-repaired
    before generation (empty if the input was already watertight).
    draft_check is the automatic non-blocking draft/undercut warning
    (direct_cast only - None for silicone_block/form_fitting, or if the
    check itself failed).
    """

    pour_box_bottom_stl: str | None = None
    pour_box_top_stl: str | None = None
    clamp_shell_bottom_stl: str | None = None
    clamp_shell_top_stl: str | None = None
    direct_mold_bottom_stl: str | None = None
    direct_mold_top_stl: str | None = None
    skin_pour_bottom_stl: str | None = None
    skin_pour_top_stl: str | None = None
    support_jacket_bottom_stl: str | None = None
    support_jacket_top_stl: str | None = None
    hollow_cast_bottom_stl: str | None = None
    hollow_cast_top_stl: str | None = None
    hollow_cast_core_stl: str | None = None
    repaired_hole_ids: list[int] = field(default_factory=list)
    draft_check: DraftReport | None = None
    cavity_volume_cm3: float = 0.0
    """The actual cast/pour material volume (FR-8); see docs/adr/0010-configurable-parting-axis-and-volume-reporting.md
    for what "cavity" means per mode."""


class MoldRequest(BaseModel):
    """POST /models/{model_id}/mold request body."""

    mode: Literal["silicone_block", "direct_cast", "form_fitting", "hollow_cast"] = Field(
        "silicone_block",
        description="silicone_block: pour-box + clamp-shell for casting the model in RTV silicone, then "
        "plaster/cement in the silicone (4 output files). direct_cast: a single rigid two-part mold shaped "
        "from the model's own geometry, for casting resin/urethane directly with no silicone step (2 output "
        "files) -- only suitable for models with no undercuts along the Z axis. form_fitting: a thin, "
        "contour-following silicone skin (grown outward from the model surface by shell_thickness_mm) plus a "
        "rigid support jacket that holds the finished skin rigid for a final pour (4 output files) -- for "
        "casting organic/detailed models where a rigid direct_cast mold couldn't release. hollow_cast: the "
        "same rigid two-part outer mold as direct_cast, plus a separate solid core (the model's surface "
        "shrunk inward by cast_wall_thickness_mm) that seats inside the cavity before pouring, so the cast "
        "forms a hollow shell rather than a solid block (3 output files).",
    )
    clearance_mm: float = Field(
        8.0,
        gt=0,
        description="(silicone_block only) Gap between the model surface and the pour-box cavity wall "
        "(silicone thickness), in mm.",
    )
    pour_box_wall_mm: float = Field(
        4.0, gt=0, description="(silicone_block only) Wall thickness of the rigid pour box, in mm."
    )
    key_diameter_mm: float = Field(
        6.0,
        gt=0,
        description="(silicone_block only) Diameter of the hemispherical registration keys at the parting "
        "line, in mm.",
    )
    sprue_diameter_mm: float = Field(
        10.0, gt=0, description="Diameter of the pour hole through each top half's ceiling, in mm."
    )
    vent_diameter_mm: float = Field(
        4.0, gt=0, description="Diameter of the air-vent hole through each top half's ceiling, in mm."
    )
    clamp_wall_mm: float = Field(
        6.0, gt=0, description="(silicone_block only) Wall thickness of the rigid clamp shell, in mm."
    )
    clamp_flange_width_mm: float = Field(
        12.0, gt=0, description="Width of the bolted flange around each mold's parting line, in mm."
    )
    bolt_hole_diameter_mm: float = Field(
        4.5, gt=0, description="Diameter of the flange's bolt holes, in mm (4.5mm = M4 clearance)."
    )
    direct_mold_wall_mm: float = Field(
        6.0,
        gt=0,
        description="(direct_cast, hollow_cast) Wall thickness of the rigid outer mold, in mm.",
    )
    shell_thickness_mm: float = Field(
        3.0,
        gt=0,
        description="(form_fitting only) How far the model's surface is grown outward to form the silicone "
        "skin's own cavity shape, in mm - this becomes the finished silicone shell's thickness.",
    )
    skin_pour_wall_mm: float = Field(
        3.0, gt=0, description="(form_fitting only) Wall thickness of the rigid skin-pour tool, in mm."
    )
    support_jacket_wall_mm: float = Field(
        5.0, gt=0, description="(form_fitting only) Wall thickness of the rigid support jacket, in mm."
    )
    cast_wall_thickness_mm: float = Field(
        4.0,
        gt=0,
        description="(hollow_cast only) How far the core is shrunk inward from the model's own surface, in "
        "mm - this becomes the finished cast's wall thickness.",
    )
    parting_axis: Literal["x", "y", "z"] = Field(
        "z",
        description="Which of the model's own axes the two halves split along. Non-'z' rotates the model "
        "internally before building, and the exported STL parts stay in that rotated frame rather than "
        "being rotated back to the original upload's orientation.",
    )
    parting_offset_mm: float = Field(
        0.0,
        description="Shift the parting plane this far from the model's own bounding-box midpoint along "
        "parting_axis (positive moves it toward the max end) - for models whose natural widest "
        "cross-section isn't at the geometric center. Must keep the plane strictly within the model's own "
        "bounding box.",
    )
    material_density_g_per_cm3: float | None = Field(
        None,
        gt=0,
        description="Optional casting material density, in g/cm3 (e.g. ~1.08 for platinum-cure silicone, "
        "~1.1-1.2 for common casting resins). When given, the response also reports "
        "estimated_cast_mass_g alongside cavity_volume_cm3.",
    )


class MoldResponse(BaseModel):
    """POST /models/{model_id}/mold response body."""

    model_id: str
    download_url: str
    repaired_hole_ids: list[int] = Field(
        default_factory=list,
        description="Watertight-defect hole ids auto-repaired before generation (empty if the input mesh "
        "was already watertight). See POST /models/{model_id}/analyze for what each id refers to.",
    )
    draft_check: dict | None = Field(
        None,
        description="direct_cast mode only: {'releasable': bool, 'problem_island_count': int} from an "
        "automatic, non-blocking draft-angle/undercut check. None for silicone_block, or if the check "
        "itself failed. Call POST /models/{model_id}/mold/draft-check for full per-face detail.",
    )
    cavity_volume_cm3: float = Field(
        0.0,
        description="The actual cast/pour material volume, in cm3 (FR-8) - meaning differs slightly per "
        "mode: silicone_block/direct_cast report the whole pour/cast volume; form_fitting/hollow_cast "
        "subtract out the model's own volume first, since only the thin gap around it fills with material.",
    )
    estimated_cast_mass_g: float | None = Field(
        None,
        description="cavity_volume_cm3 * request.material_density_g_per_cm3, if that field was given. "
        "None if no density was supplied.",
    )


class TagSuggestRequest(BaseModel):
    """POST /tags/suggest request body."""

    file_name: str = Field(..., min_length=1, description="The model file's name, e.g. 'dragon_articulated_v2.stl'.")
    designer_name: str | None = Field(default=None, description="Designer/creator name, if known.")
    extension: str | None = Field(default=None, description="File extension, e.g. '.stl'.")
    existing_tags: list[str] = Field(
        default_factory=list, description="Tags already applied, so suggestions don't just repeat them."
    )
    colors: list[str] = Field(default_factory=list, description="Filament colors from slicer metadata, if known.")


class TagSuggestResponse(BaseModel):
    """POST /tags/suggest response body."""

    tags: list[str]
    method: Literal["llm", "heuristic_fallback"]


class TranscribeResponse(BaseModel):
    """POST /transcribe response body."""

    text: str


class HealthResponse(BaseModel):
    """GET /health response body."""

    status: Literal["ok", "degraded"]
    openscad_available: bool
    blender_available: bool
    freecad_available: bool
    llm_provider: str
    llm_reachable: bool


# --- Watertight analysis/repair (hole detection & classification) ---
# Added alongside thickness.py's mesh-shell path: both features share the
# same "load into headless Blender, do bmesh work, export" shape, but
# watertight.py's job is finding/closing gaps rather than adding wall
# thickness. See docs/adr/0004-watertight-hole-detection-and-repair.md.


class HoleClassification(StrEnum):
    """Verdict watertight.py's heuristic assigns to one detected hole."""

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
    One connected boundary-edge loop found on a mesh - a location where
    the surface is not watertight, either a deliberate opening (a cup's
    mouth, an open box top) or an unintentional gap from bad topology.
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
    """A connected group of faces whose normals disagree with their neighbors (an "inverted wrinkle")."""

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
    self_intersection_count: int = 0
    # Enclosed volume in mm3; None when the mesh isn't watertight (an open
    # surface has no meaningful enclosed volume).
    mesh_volume_mm3: float | None = None
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


class BoundingBoxSchema(BaseModel):
    """JSON shape of a BoundingBox, nested in AnalyzeResponse to match the viewer's report.bounding_box.min/max."""

    min: tuple[float, float, float]
    max: tuple[float, float, float]


class AnalyzeResponse(BaseModel):
    """POST /models/{model_id}/analyze response body."""

    model_id: str
    is_watertight: bool
    vertex_count: int
    face_count: int
    total_surface_area: float
    bounding_box: BoundingBoxSchema
    holes: list[dict]
    flipped_normal_islands: list[dict]
    nonmanifold_junction_edge_count: int
    self_intersection_count: int = Field(
        0, description="Pairs of faces that intersect each other without sharing a vertex (0 = clean)."
    )
    mesh_volume_cm3: float | None = Field(
        None, description="Enclosed volume in cm3 (1 unit = 1 mm). Null when the mesh isn't watertight."
    )
    blender_version: str
    viewer_glb_url: str | None = None


class RepairRequest(BaseModel):
    """POST /models/{model_id}/repair request body."""

    hole_ids: list[int] = Field(
        default_factory=list,
        description="Hole ids (from a prior /analyze) to close. Required unless auto_repair is true.",
    )
    auto_repair: bool = Field(
        False,
        description="When true (and hole_ids is empty), re-analyze the model and close only holes the "
        "heuristic classifies as likely defects, recalculating face normals across the mesh. "
        "Likely-intentional and ambiguous openings are never closed.",
    )
    quad_target_faces: int = Field(
        0,
        ge=0,
        le=1_000_000,
        description="0 (default) disables. When > 0, retopologizes the repaired mesh into roughly this "
        "many quad-dominant faces via QuadriFlow after hole-filling -- mainly a topology/cosmetic pass. "
        "Pick a value proportional to the mesh's real complexity: a target at or below its natural face "
        "count can leave it not watertight even with the repair pass that runs afterward.",
    )

    @model_validator(mode="after")
    def _hole_ids_or_auto(self) -> RepairRequest:
        """Require exactly one selection mode: explicit hole_ids, or auto_repair."""
        if self.auto_repair and self.hole_ids:
            raise ValueError("pass either hole_ids or auto_repair=true, not both")
        if not self.auto_repair and not self.hole_ids:
            raise ValueError("hole_ids must list at least one hole id (or set auto_repair=true)")
        return self


class RepairResponse(BaseModel):
    """POST /models/{model_id}/repair response body."""

    model_id: str
    closed_hole_ids: list[int]
    is_watertight: bool
    remaining_holes: list[dict]
    viewer_glb_url: str | None = None
    download_url: str
    changed: bool = Field(True, description="False when auto_repair found nothing to fix and left the file untouched.")
    skipped_hole_ids: list[int] = Field(
        default_factory=list,
        description="auto_repair only: holes left open because they look intentional or are ambiguous.",
    )


class UploadResponse(BaseModel):
    """POST /watertight/upload and POST /step/upload response body."""

    model_id: str
    filename: str


# --- Draft-angle / undercut analysis (FR-4) ---
# A different geometric heuristic from watertight.py's hole detection, but
# the same "deterministic geometry pass over the mesh, no LLM/ML" shape
# (see hole_classifier.py / ADR 0004) and the same headless-Blender-
# subprocess-then-parse-JSON pattern watertight.py already establishes.
# See draft_analysis.py and docs/adr/0007-draft-undercut-analysis.md.


@dataclass
class ProblemFaceIsland:
    """
    A connected group of faces whose draft angle (relative to their
    half's pull direction) falls below the requested threshold - either
    a genuine undercut (won't release at all) or just an under-drafted
    near-vertical wall (releases, but with more friction than the
    threshold calls for).
    """

    id: int
    face_indices: list[int]
    centroid: tuple[float, float, float]
    face_count: int
    min_draft_angle_deg: float
    classification: Literal["undercut", "insufficient_draft"]


@dataclass
class DraftReport:
    """Full result of analyzing one mesh for draft-angle/undercut problems along a given pull axis."""

    source_path: str
    pull_axis: Literal["x", "y", "z"]
    parting_coordinate: float
    min_draft_angle_deg: float
    releasable: bool
    problem_islands: list[ProblemFaceIsland] = field(default_factory=list)
    vertex_count: int = 0
    face_count: int = 0
    blender_version: str = ""


class DraftCheckRequest(BaseModel):
    """POST /models/{model_id}/mold/draft-check request body."""

    pull_axis: Literal["x", "y", "z"] = Field(
        "z", description="Axis the two mold halves are pulled apart along (matches make_mold.py's own convention)."
    )
    min_draft_angle_deg: float = Field(
        2.0,
        ge=0,
        description="Faces drafted less than this (including negative = a genuine undercut) are flagged. "
        "2-4 degrees is the commonly cited range for reliable rigid-mold release.",
    )


class DraftCheckResponse(BaseModel):
    """POST /models/{model_id}/mold/draft-check response body."""

    source_path: str
    pull_axis: Literal["x", "y", "z"]
    parting_coordinate: float
    min_draft_angle_deg: float
    releasable: bool
    problem_islands: list[dict]
    vertex_count: int
    face_count: int
    blender_version: str


# --- FreeCAD-backed features: STEP conversion & solid healing ---
# Added alongside thickness.py's FreeCAD-first mesh-shell path - see
# docs/adr/0012-freecad-third-cad-backend.md.


class RepairSolidResponse(BaseModel):
    """POST /models/{model_id}/repair-solid response body."""

    model_id: str
    fixed: bool
    valid_before: bool
    valid_after: bool
    download_url: str


# --- Spiral wand: twisted wand + figurine riders (spiral_wand.py) ---
# See docs/adr/0014-spiral-wand-manifold3d.md.


class SpiralRodFields(BaseModel):
    """Rod settings shared by the wand and its riders - a rider only fits a wand built with identical values."""

    rod_diameter_mm: float = Field(14.0, ge=6, le=60, description="Outer diameter of the twisted rod (lobe tips).")
    lobes: int = Field(3, ge=2, le=8, description="Number of lobes in the rod cross-section.")
    lobe_depth_mm: float = Field(3.0, gt=0, le=20, description="How far the valleys sit below the lobe tips.")
    pitch_mm: float = Field(
        120.0,
        ge=20,
        le=2000,
        description="Rod length per full 360-degree turn. Smaller = more spin per drop but more friction.",
    )
    handed: Literal["right", "left"] = Field("right", description="Spiral handedness (which way the rider spins).")
    clearance_mm: float = Field(
        0.45, ge=0.1, le=2.0, description="Radial gap per side between rod and rider bore (starting guess)."
    )


class SpiralWandRequest(SpiralRodFields):
    """POST /spiral-wand request body."""

    total_length_mm: float = Field(300.0, ge=60, le=3000, description="Overall length, handle bottom to cap top.")
    spiral_length_mm: float | None = Field(
        None, ge=10, le=3000, description="Set this instead of total_length_mm to fix the spiral length directly."
    )
    handle_length_mm: float = Field(90.0, ge=10, le=1000)
    handle_diameter_mm: float = Field(24.0, ge=8, le=120)
    handle_gap_mm: float = Field(
        25.0,
        ge=0,
        le=1000,
        description="Handle distance: plain neck between the handle stop and the spiral, where a rider rests. "
        "Make it at least the rider's height for the rider to spin freely there.",
    )
    stop_diameter_mm: float = Field(30.0, ge=8, le=150, description="Flange at the top of the handle.")
    stop_thickness_mm: float = Field(4.0, ge=1, le=50)
    cap_style: Literal["dome", "disc", "cone", "none"] = Field(
        "dome", description="End cap shape. Ignored when cap_model_id is given."
    )
    cap_model_id: str | None = Field(
        None,
        pattern=r"^[0-9a-f]{32}$",
        description="Use an uploaded/generated model as the end cap (scaled to cap_diameter_mm wide, Z up).",
    )
    cap_diameter_mm: float = Field(26.0, ge=8, le=200)
    cap_height_mm: float = Field(14.0, ge=2, le=200)
    peg_length_mm: float = Field(10.0, ge=3, le=50, description="Length of the D-pegs at the cap and section joints.")
    joint_clearance_mm: float = Field(0.15, ge=0, le=1.0, description="Radial gap in the D-peg sockets.")
    max_print_height_mm: float = Field(
        250.0, ge=50, le=2000, description="Tallest piece your printer can print standing up."
    )


class SpiralRiderRequest(SpiralRodFields):
    """POST /models/{model_id}/spiral-wand/rider request body."""

    height_mm: float | None = Field(
        None, gt=0, le=1000, description="Scale the figurine to this height. Omit to use `scale`."
    )
    scale: float = Field(1.0, gt=0, le=1000, description="Scale factor, used when height_mm is not given.")
    up_axis: Literal["z", "y", "x", "-z", "-y", "-x"] = Field(
        "z", description="Which axis of the input file points up (y for many Blender/game exports)."
    )
    offset_x_mm: float = Field(0.0, ge=-500, le=500, description="Move the bore off the figurine's centre.")
    offset_y_mm: float = Field(0.0, ge=-500, le=500)
    rotate_deg: float = Field(0.0, ge=-360, le=360, description="Spin the figurine about the bore axis.")
    collar: Literal["full", "base", "none"] = Field(
        "full", description="Solid sleeve around the bore: whole height, bottom collar_height_mm only, or none."
    )
    collar_wall_mm: float = Field(2.4, ge=0.8, le=20)
    collar_height_mm: float = Field(12.0, ge=1, le=500)
    base_diameter_mm: float = Field(0.0, ge=0, le=500, description=">0 adds a round platform under the figurine.")
    base_thickness_mm: float = Field(3.0, ge=0.8, le=50)


class SpiralWandPart(BaseModel):
    """One STL produced by spiral_wand.py, and where it sits in the assembled wand (for the viewer)."""

    filename: str
    role: Literal["wand", "cap", "rider"]
    z_offset_mm: float = Field(description="Height of this part's z=0 in the assembled wand.")
    size_mm: list[float]
    url: str | None = None


class SpiralWandResult(BaseModel):
    """Output of spiral_wand.make_wand()/make_rider(); also the core of SpiralWandResponse."""

    parts: list[SpiralWandPart]
    notes: list[str] = Field(default_factory=list)
    twist_deg_per_mm: float
    total_length_mm: float | None = None
    spiral_length_mm: float | None = None
    spiral_turns: float | None = None
    rest_z_mm: float | None = None
    spiral_start_z_mm: float | None = None
    spiral_end_z_mm: float | None = None
    rider_height_mm: float | None = None


class SpiralWandResponse(SpiralWandResult):
    """POST /spiral-wand and POST /models/{model_id}/spiral-wand/rider response body."""

    model_id: str
    download_url: str


# --- Split to fit the bed (split.py) ---
# See docs/print-tools-plan.md item 1.


class SplitRequest(BaseModel):
    """POST /models/{model_id}/split request body."""

    bed_x_mm: float = Field(250.0, ge=20, le=2000, description="Printable width of the bed.")
    bed_y_mm: float = Field(250.0, ge=20, le=2000, description="Printable depth of the bed.")
    bed_z_mm: float = Field(250.0, ge=20, le=2000, description="Printable height.")
    connector: Literal["dowel", "dpeg", "none"] = Field(
        "dowel",
        description="dowel: matching holes in both pieces plus printed pins; dpeg: a one-way D-shaped peg on one "
        "piece and a socket in the other; none: plain cuts.",
    )
    connector_diameter_mm: float = Field(5.0, ge=2, le=30)
    connector_length_mm: float = Field(12.0, ge=4, le=60, description="Total pin length (half in each piece).")
    clearance_mm: float = Field(0.15, ge=0, le=1.0, description="Radial gap per side in connector holes.")
    max_pieces: int = Field(16, ge=2, le=64)
    margin_mm: float = Field(1.0, ge=0, le=20, description="Space kept free on each side of the bed.")


class SplitPart(BaseModel):
    """One STL produced by split.py (also used for tool_insert.py's single tray)."""

    filename: str
    role: Literal["piece", "pin"]
    size_mm: list[float]
    fits: bool
    print_hint: str | None = None
    cell: list[int] | None = Field(None, description="Grid cell [i, j, k] of a piece (for the exploded view).")
    count: int = Field(1, description="How many to print (dowel pins).")
    url: str | None = None


class SplitResult(BaseModel):
    """Output of split.split_to_fit(); also the core of SplitResponse."""

    parts: list[SplitPart]
    notes: list[str] = Field(default_factory=list)
    grid: list[int]
    connector_count: int
    model_size_mm: list[float]


class SplitResponse(SplitResult):
    """POST /models/{model_id}/split response body."""

    model_id: str
    download_url: str


class OverhangResponse(BaseModel):
    """GET /models/{model_id}/overhangs response body (geometry.overhang_report)."""

    overhang_area_mm2: float
    total_area_mm2: float
    overhang_fraction: float = Field(description="Share of the surface that needs support, 0..1.")
    overhang_face_count: int
    angle_deg: float


# --- Tool insert from a photo (tool_insert.py) ---
# See docs/print-tools-plan.md item 3.


class TracedTool(BaseModel):
    """One shape traced from the photo."""

    outline_px: list[list[float]] = Field(description="Outline in preview-image pixels (y down).")
    outline_mm: list[list[float]] | None = Field(
        None, description="Outline in mm (y up, origin at the paper's bottom-left); null when the scale is unknown."
    )


class TraceResult(BaseModel):
    """POST /tool-insert/trace response body."""

    mm_per_px: float | None = Field(description="Scale of the preview image; null when no calibration was found.")
    paper_found: bool
    square_px: list[list[float]] | None = Field(None, description="Corners of the calibration square, in pixels.")
    width_px: int
    height_px: int
    preview: str = Field(description="The perspective-corrected photo as a JPEG data URL.")
    tools: list[TracedTool]
    notes: list[str] = Field(default_factory=list)


class Notch(BaseModel):
    """A round finger notch added to a tool's pocket."""

    x: float = Field(allow_inf_nan=False)
    y: float = Field(allow_inf_nan=False)
    d: float = Field(20.0, ge=5, le=60, description="Notch diameter in mm.")


class ToolOutline(BaseModel):
    """One tool's (edited) outline for POST /tool-insert."""

    outline_mm: list[list[float]] = Field(min_length=3, max_length=5000)
    notches: list[Notch] = Field(default_factory=list, max_length=20)


class ToolInsertRequest(BaseModel):
    """POST /tool-insert request body."""

    tools: list[ToolOutline] = Field(min_length=1, max_length=20)
    clearance_mm: float = Field(1.0, ge=0, le=5, description="Gap around each tool: 0.5 snug, 1 normal, 2 loose.")
    depth_mm: float = Field(15.0, ge=2, le=150, description="Pocket depth.")
    floor_mm: float = Field(2.0, ge=0.6, le=20, description="Material under the pockets.")
    wall_mm: float = Field(4.0, ge=1, le=50, description="Minimum material between pockets and the tray edge.")
    tray: Literal["fit", "gridfinity"] = "fit"
    corner_radius_mm: float = Field(4.0, ge=0, le=30)
    chamfer_mm: float = Field(1.0, ge=0, le=5, description="Stepped chamfer on the pocket's top edge.")
    height_units: int | None = Field(None, ge=2, le=30, description="Gridfinity height (7 mm units); null = auto.")


class ToolInsertResult(BaseModel):
    """Output of tool_insert.make_insert(); also the core of ToolInsertResponse."""

    parts: list[SplitPart]
    tray: str
    grid_units: list[int] | None = Field(None, description="Gridfinity [x units, y units, height units].")
    size_mm: list[float]
    pocket_depth_mm: float
    floor_mm: float
    pocket_area_mm2: float
    notes: list[str] = Field(default_factory=list)


class ToolInsertResponse(ToolInsertResult):
    """POST /tool-insert response body."""

    model_id: str


# --- Rule-based templates (gridfinity.py, gear.py, lithophane.py, qr_tag.py, terrain.py) ---
# See docs/print-tools-plan.md item 4.


class TemplatePart(BaseModel):
    """One STL produced by a template."""

    filename: str
    size_mm: list[float]
    print_hint: str | None = None
    url: str | None = None


class TemplateResult(BaseModel):
    """Output of every template builder; also the core of TemplateResponse."""

    parts: list[TemplatePart]
    notes: list[str] = Field(default_factory=list)
    report: dict = Field(default_factory=dict, description="Template-specific numbers (sizes, gear mesh check, ...).")


class TemplateResponse(TemplateResult):
    """Response body of every POST /templates/... route."""

    model_id: str


class GridfinityBinRequest(BaseModel):
    """POST /templates/gridfinity-bin request body."""

    units_x: int = Field(2, ge=1, le=8, description="Width in 42 mm grid units.")
    units_y: int = Field(1, ge=1, le=8, description="Depth in 42 mm grid units.")
    height_units: int = Field(6, ge=2, le=20, description="Body height in 7 mm units (the lip adds 4.75 mm).")
    compartments_x: int = Field(1, ge=1, le=10)
    compartments_y: int = Field(1, ge=1, le=10)
    wall_mm: float = Field(1.2, ge=0.8, le=5)
    divider_mm: float = Field(1.2, ge=0.8, le=5)
    floor_mm: float = Field(1.2, ge=0.6, le=10, description="Floor above the feet.")
    stacking_lip: bool = True
    label_tab: bool = False
    magnet_holes: bool = False


class MinisBinRequest(BaseModel):
    """POST /templates/minis-bin request body: a Gridfinity bin with a socket per miniature base."""

    units_x: int = Field(2, ge=1, le=8, description="Width in 42 mm grid units.")
    units_y: int = Field(2, ge=1, le=8, description="Depth in 42 mm grid units.")
    height_units: int = Field(8, ge=3, le=20, description="Body height in 7 mm units; set it from the tallest figure.")
    base_diameter_mm: float = Field(25.0, ge=10, le=160, description="Round base: 25, 28, 32, 40, 50, 60 ...")
    base_clearance_mm: float = Field(0.6, ge=0, le=3, description="Added to the base so it drops in.")
    socket_depth_mm: float = Field(3.5, ge=1, le=10, description="How deep each base sits.")
    socket_gap_mm: float = Field(1.6, ge=0.8, le=20, description="Solid floor between two sockets.")
    wall_mm: float = Field(1.2, ge=0.8, le=5)
    magnet_diameter_mm: float = Field(0.0, ge=0, le=30, description="Magnet hole under each socket; 0 = none.")
    magnet_depth_mm: float = Field(2.2, ge=0.5, le=6)
    stacking_lip: bool = True


class TerrainRequest(BaseModel):
    """POST /templates/terrain request body: one tabletop terrain tile from a seeded height field."""

    style: Literal["hills", "ridges", "craters", "mesa"] = "hills"
    width_mm: float = Field(152.4, ge=30, le=400, description="152.4 = 6 in, 304.8 = 12 in.")
    depth_mm: float = Field(152.4, ge=30, le=400)
    base_mm: float = Field(4.0, ge=1.5, le=20, description="Solid slab under the terrain; also the edge height.")
    max_height_mm: float = Field(25.0, ge=1, le=150, description="Highest point above the base.")
    feature_mm: float = Field(50.0, ge=10, le=300, description="Rough size of one hill or valley.")
    roughness: float = Field(0.45, ge=0.2, le=0.8, description="0.3 smooth .. 0.7 rocky.")
    terrace_mm: float = Field(0.0, ge=0, le=50, description="Step the surface into flat levels this tall; 0 = smooth.")
    edge_fade_mm: float = Field(12.0, ge=0, le=100, description="Ease down to the base near the edges so tiles join.")
    resolution_mm: float = Field(1.5, ge=0.5, le=5, description="Height-field grid spacing.")
    seed: int = Field(1, ge=0, le=2**31 - 1, description="Same seed + settings = same tile.")
    magnet_holes: bool = False
    magnet_diameter_mm: float = Field(6.2, ge=2, le=20)
    magnet_depth_mm: float = Field(2.2, ge=0.5, le=10)


class GearRequest(BaseModel):
    """POST /templates/gear request body."""

    module: float = Field(2.0, ge=0.5, le=10, description="Tooth size in mm (pitch diameter / teeth).")
    teeth: int = Field(20, ge=8, le=200)
    pressure_angle_deg: float = Field(20.0, ge=14.5, le=30)
    backlash_mm: float = Field(0.15, ge=0, le=1, description="Total play of a pair along the pitch circle.")
    face_width_mm: float = Field(8.0, ge=1, le=100)
    helix_angle_deg: float = Field(0.0, ge=-45, le=45, description="0 = spur gear.")
    bore: Literal["none", "round", "d", "hex"] = "round"
    bore_diameter_mm: float = Field(5.0, ge=0, le=100, description="Bore size (across flats for hex).")
    mate_teeth: int | None = Field(None, ge=8, le=200, description="Also build a mating gear with this many teeth.")
    rack_teeth: int | None = Field(None, ge=2, le=200, description="Also build a rack with this many teeth.")
    rack_height_mm: float = Field(6.0, ge=1, le=50, description="Rack body below the tooth roots.")


class LithophaneRequest(BaseModel):
    """Form fields of POST /templates/lithophane (the image is the multipart `file`)."""

    shape: Literal["flat", "curved", "cylinder"] = "flat"
    width_mm: float = Field(
        100.0, ge=20, le=400, description="Panel width; arc length for curved, circumference for cylinder."
    )
    min_thickness_mm: float = Field(0.8, ge=0.4, le=5, description="Thickness at white.")
    max_thickness_mm: float = Field(3.0, ge=0.8, le=10, description="Thickness at black.")
    resolution_mm: float = Field(0.25, ge=0.1, le=2, description="Panel size of one image pixel.")
    frame_mm: float = Field(3.0, ge=0, le=30, description="Solid border (top and bottom only on a cylinder).")
    curve_deg: float = Field(120.0, ge=10, le=300, description="Arc of a curved panel.")


class QrTagRequest(BaseModel):
    """POST /templates/qr-tag request body. A Wi-Fi password is never logged or stored in the spec."""

    kind: Literal["text", "wifi"] = "text"
    text: str = Field("", max_length=1000, description="Text or URL (kind=text).")
    ssid: str = Field("", max_length=64, description="Wi-Fi network name (kind=wifi).")
    password: str = Field("", max_length=128, description="Wi-Fi password (kind=wifi); ends up in the printed code.")
    security: Literal["WPA", "WEP", "nopass"] = "WPA"
    error_correction: Literal["l", "m", "q", "h"] = "m"
    module_mm: float = Field(2.0, ge=0.8, le=10, description="Size of one QR square.")
    quiet_zone: int = Field(2, ge=1, le=6, description="Empty border in modules (4 is the standard).")
    plate_mm: float = Field(3.0, ge=1, le=10)
    code_height_mm: float = Field(1.0, ge=0.2, le=5)
    corner_radius_mm: float = Field(3.0, ge=0, le=20)
    keyring_hole: bool = True
    nfc_pocket: bool = False
    nfc_diameter_mm: float = Field(25.0, ge=10, le=60)


# --- Multi-colour 3MF export (threemf.py) ---
# See docs/print-tools-plan.md item 5.


class ColourPart(BaseModel):
    """One STL of a model and the filament it prints with."""

    filename: str = Field(max_length=64)
    color: str = Field("#F2F2F2", pattern="^#[0-9A-Fa-f]{6}$")
    extruder: int = Field(1, ge=1, le=16, description="Filament slot (AMS / CANVAS), 1-based.")


class Export3mfRequest(BaseModel):
    """POST /models/{model_id}/export-3mf request body."""

    parts: list[ColourPart] | None = Field(
        None, max_length=64, description="Parts and filaments; null = every part of the model, one slot each."
    )
    assembled: bool = Field(True, description="One object with all parts in place, or each part separately.")
    slicer: Literal["orca", "prusa"] = Field(
        "orca",
        description="orca: Bambu Studio / OrcaSlicer / Elegoo Slicer / Anycubic Slicer Next; prusa: PrusaSlicer "
        "(they read multi-part objects differently).",
    )


class Export3mfResponse(BaseModel):
    """POST /models/{model_id}/export-3mf response body."""

    model_id: str
    download_url: str
    objects: int
    parts: int
    triangles: int
    slicer: str
    filaments: list[dict]
