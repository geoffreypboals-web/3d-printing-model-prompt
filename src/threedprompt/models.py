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

from dataclasses import dataclass
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
