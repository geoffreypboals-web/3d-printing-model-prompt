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
