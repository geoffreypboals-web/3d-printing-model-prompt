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
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class Complexity(str, Enum):
    """Which backend a prompt should be routed to."""

    SIMPLE = "simple"
    COMPLEX = "complex"


class ClassificationMethod(str, Enum):
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


class Backend(str, Enum):
    """Which generation engine actually produced a model."""

    OPENSCAD = "openscad"
    BLENDER = "blender"


@dataclass(frozen=True)
class GenerationResult:
    """Output of an OpenSCAD or Blender generation backend."""

    backend: Backend
    stl_path: str
    source_path: str | None
    """Path to the .scad source (OpenSCAD) or .py build script (Blender), if kept for regeneration."""
    source_kind: Literal["openscad_scad", "blender_bpy_script"] | None
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


class HealthResponse(BaseModel):
    """GET /health response body."""

    status: Literal["ok", "degraded"]
    openscad_available: bool
    blender_available: bool
    llm_provider: str
    llm_reachable: bool


# --- Watertight analysis/repair (hole detection & classification) ---
# Added alongside thickness.py's mesh-shell path: both features share the
# same "load into headless Blender, do bmesh work, export" shape, but
# watertight.py's job is finding/closing gaps rather than adding wall
# thickness. See docs/adr/0004-watertight-hole-detection-and-repair.md.


class HoleClassification(str, Enum):
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
    blender_version: str
    viewer_glb_url: str | None = None


class RepairRequest(BaseModel):
    """POST /models/{model_id}/repair request body."""

    hole_ids: list[int] = Field(..., min_length=1, description="Hole ids (from a prior /analyze) to close.")
    quad_target_faces: int = Field(
        0,
        ge=0,
        le=1_000_000,
        description="0 (default) disables. When > 0, retopologizes the repaired mesh into roughly this "
        "many quad-dominant faces via QuadriFlow after hole-filling -- mainly a topology/cosmetic pass. "
        "Pick a value proportional to the mesh's real complexity: a target at or below its natural face "
        "count can leave it not watertight even with the repair pass that runs afterward.",
    )


class RepairResponse(BaseModel):
    """POST /models/{model_id}/repair response body."""

    model_id: str
    closed_hole_ids: list[int]
    is_watertight: bool
    remaining_holes: list[dict]
    viewer_glb_url: str | None = None
    download_url: str


class UploadResponse(BaseModel):
    """POST /watertight/upload response body."""

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
