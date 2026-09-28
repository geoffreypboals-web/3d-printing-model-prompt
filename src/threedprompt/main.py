"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/src/threedprompt/main.py
Description: FastAPI application wiring together the classifier, the two
    generation backends (OpenSCAD for simple parts, Blender for complex
    ones), the wall-thickness endpoints, the mold-generation endpoints,
    and the watertight analysis/repair endpoints. This is the only
    HTTP-facing module - it does no CAD/LLM work itself, only routing,
    validation, and translating domain errors into HTTP responses. Also
    serves the browser UI (static/index.html) at "/" for picking a local
    file, modifying it, and downloading the result without needing
    curl/Swagger.
Inputs: HTTP requests (see models.py for request/response schemas).
Outputs: HTTP responses; STL files written under settings.output_dir as
    a side effect.
Troubleshooting:
    - 503 from any endpoint means an external tool (openscad, blender, or
      the LLM) is unavailable or misconfigured - check GET /health first,
      it reports each dependency's status individually.
    - 413 on /thicken or /mold means the uploaded file exceeded
      MAX_UPLOAD_BYTES; raise that env var if you intentionally need
      larger uploads.
    - 422 "exceeding MAX_MOLD_DIMENSION_MM" from /mold: the requested
      clearance/wall/flange would build a box bigger than that cap (a
      typo in mm vs cm is the usual cause) - see mold.py.
    - "/" 404s or shows raw JSON instead of the UI: the StaticFiles mount
      must be the LAST route registered (it's a catch-all at "/") - if a
      new API route is added below it by mistake, it'll never be reached.
    - 400 "hole id(s) ... not found" from /repair: hole ids are
      positional and only valid for the exact file state a prior
      /analyze reported on - re-run /analyze if the model changed
      (closing holes itself renumbers whatever's left).
    - 503 from /step/upload, /models/{id}/export-step, or
      /models/{id}/repair-solid means FreeCAD is unavailable or failed -
      check GET /health's freecad_available field first (unlike
      OpenSCAD/Blender, FreeCAD isn't required for the service overall -
      thickening still works via Blender without it, only the
      FreeCAD-specific endpoints fail).
    - 503 from /thumbnail on a .step/.stp upload specifically can mean
      either Blender or FreeCAD is unavailable - STEP thumbnails go
      through both (FreeCAD converts to a mesh first, Blender renders it).
    - /tags/suggest never 503s - if the LLM is unreachable or its response
      is unparseable, it falls back to filename-derived heuristic tags and
      reports method="heuristic_fallback" (check GET /health's llm field
      to tell "unreachable" apart from "reachable but replied
      unparseable"). See tag_suggester.py's header for the text-only vs.
      vision-based tagging tradeoff.
    - Run locally with: uvicorn threedprompt.main:app --reload
      (see README.md at /home/user/3d-printing-model-prompt/README.md
      for the full command including PYTHONPATH setup).
"""

from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from threedprompt import (
    blender_generator,
    draft_analysis,
    freecad_cad,
    mold,
    openscad_generator,
    storage,
    tag_suggester,
    thickness,
    thumbnail,
    watertight,
)
from threedprompt.blender_generator import BlenderGenerationError
from threedprompt.classifier import classify_prompt
from threedprompt.config import settings
from threedprompt.draft_analysis import DraftAnalysisError
from threedprompt.freecad_cad import FreeCADCADError
from threedprompt.llm_client import LLMError, get_llm_client
from threedprompt.logging_config import configure_logging, get_logger
from threedprompt.models import (
    AnalyzeResponse,
    Backend,
    Complexity,
    DraftCheckRequest,
    DraftCheckResponse,
    DraftReport,
    FlippedNormalIsland,
    GenerateRequest,
    GenerateResponse,
    HealthResponse,
    Hole,
    HoleClassification,
    MoldRequest,
    MoldResponse,
    RepairRequest,
    RepairResponse,
    RepairSolidResponse,
    TagSuggestRequest,
    TagSuggestResponse,
    ThickenRequest,
    ThickenResponse,
    UploadResponse,
)
from threedprompt.mold import MoldError
from threedprompt.openscad_generator import OpenScadGenerationError
from threedprompt.thickness import ThicknessError
from threedprompt.watertight import WatertightError

configure_logging()
logger = get_logger(__name__)

app = FastAPI(title="3D Printing Model Prompt", version="0.1.0")

_ALLOWED_UPLOAD_SUFFIXES = {".stl", ".obj", ".3dm"}
_VIEWER_GLB_FILENAME = "viewer.glb"


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Report availability of each external dependency (OpenSCAD, Blender, the LLM backend)."""
    openscad_available = shutil.which(settings.openscad_binary) is not None
    blender_available = shutil.which(settings.blender_binary) is not None
    freecad_available = shutil.which(settings.freecad_binary) is not None
    llm_reachable = False
    try:
        llm_reachable = get_llm_client().is_reachable()
    except LLMError as exc:
        logger.warning("LLM client unavailable during health check: %s", exc)

    # freecad_available deliberately doesn't gate "ok" vs "degraded" -
    # Blender remains the required, always-available fallback for
    # thickening (thickness.py's mesh_shell()); FreeCAD's absence only
    # disables the FreeCAD-specific endpoints (STEP conversion, solid
    # healing), which report their own 503s if called without it.
    status = "ok" if (openscad_available and blender_available and llm_reachable) else "degraded"
    return HealthResponse(
        status=status,
        openscad_available=openscad_available,
        blender_available=blender_available,
        freecad_available=freecad_available,
        llm_provider=settings.llm_provider,
        llm_reachable=llm_reachable,
    )


@app.post("/generate", response_model=GenerateResponse)
def generate(request: GenerateRequest) -> GenerateResponse:
    """Classify a prompt and generate a model via OpenSCAD (simple) or Blender (complex)."""
    prompt = request.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=422, detail="prompt must not be empty")
    if len(prompt) > settings.max_prompt_length:
        raise HTTPException(
            status_code=422,
            detail=f"prompt exceeds MAX_PROMPT_LENGTH ({settings.max_prompt_length} characters)",
        )

    cached_model_id = storage.lookup_cached_model(prompt, request.wall_thickness_mm)
    if cached_model_id:
        spec = storage.load_spec(cached_model_id) or {}
        return GenerateResponse(
            model_id=cached_model_id,
            backend=Backend(spec.get("backend", Backend.OPENSCAD.value)),
            classification_label=Complexity(spec.get("classification_label", Complexity.SIMPLE.value)),
            classification_confidence=spec.get("classification_confidence", 0.0),
            classification_method=spec.get("classification_method", "cache"),
            classification_reasoning="Served from cache: identical prompt already generated.",
            download_url=f"/models/{cached_model_id}/download",
        )

    classification = classify_prompt(prompt)

    model_id, model_directory = storage.new_model_dir()
    try:
        if classification.label is Complexity.SIMPLE:
            result = openscad_generator.generate(prompt, model_directory, request.wall_thickness_mm)
        else:
            result = blender_generator.generate(prompt, model_directory)
    except (OpenScadGenerationError, BlenderGenerationError, LLMError) as exc:
        shutil.rmtree(model_directory, ignore_errors=True)
        logger.error("Generation failed for model_id=%s: %s", model_id, exc)
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    spec = {
        "prompt": prompt,
        "wall_thickness_mm": request.wall_thickness_mm,
        "backend": result.backend.value,
        "source_path": result.source_path,
        "source_kind": result.source_kind,
        "wall_thickness_param": result.wall_thickness_param,
        "classification_label": classification.label.value,
        "classification_confidence": classification.confidence,
        "classification_method": classification.method.value,
        "classification_reasoning": classification.reasoning,
    }
    storage.save_spec(model_id, spec)
    storage.remember_cached_model(prompt, request.wall_thickness_mm, model_id)

    return GenerateResponse(
        model_id=model_id,
        backend=result.backend,
        classification_label=classification.label,
        classification_confidence=classification.confidence,
        classification_method=classification.method,
        classification_reasoning=classification.reasoning,
        download_url=f"/models/{model_id}/download",
    )


@app.get("/models/{model_id}/download")
def download_model(model_id: str) -> FileResponse:
    """Download a previously generated or thickened model's STL file."""
    try:
        path = storage.stl_path(model_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(path, media_type="model/stl", filename=f"{model_id}.stl")


@app.post("/models/{model_id}/thicken", response_model=ThickenResponse)
def thicken_existing_model(model_id: str, request: ThickenRequest) -> ThickenResponse:
    """
    Increase the wall thickness of a model this service generated.

    Prefers regenerating from OpenSCAD source (if the model has a
    recognized wall_thickness variable); falls back to shelling the mesh
    with Blender's Solidify modifier otherwise. Always produces a new
    model_id rather than mutating the original.
    """
    try:
        source_dir = storage.model_dir(model_id)
        spec = storage.load_spec(model_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if request.amount_mm > settings.max_wall_thickness_mm:
        raise HTTPException(
            status_code=422,
            detail=f"amount_mm exceeds MAX_WALL_THICKNESS_MM ({settings.max_wall_thickness_mm})",
        )

    can_regenerate = bool(
        spec
        and spec.get("source_kind") == "openscad_scad"
        and spec.get("wall_thickness_param")
        and spec.get("source_path")
        and Path(spec["source_path"]).is_file()
    )

    if can_regenerate:
        new_model_id, new_dir = storage.copy_into_new_model(source_dir)
        try:
            new_thickness = thickness.regenerate_from_source(
                new_dir / "model.scad", request.amount_mm, new_dir / "model.stl"
            )
            new_spec = dict(spec)
            new_spec["wall_thickness_mm"] = new_thickness
            new_spec["parent_model_id"] = model_id
            storage.save_spec(new_model_id, new_spec)
            return ThickenResponse(
                model_id=new_model_id, method="regenerated_from_source", download_url=f"/models/{new_model_id}/download"
            )
        except ThicknessError as exc:
            logger.warning("Regenerate-from-source failed for %s (%s); falling back to mesh shell.", model_id, exc)

    try:
        stl_source = storage.stl_path(model_id)
        new_model_id, new_dir = storage.new_model_dir()
        thickness.mesh_shell(stl_source, request.amount_mm, new_dir, quad_target_faces=request.quad_target_faces)
        storage.save_spec(new_model_id, {"parent_model_id": model_id, "method": "mesh_shell"})
        return ThickenResponse(
            model_id=new_model_id, method="mesh_shell", download_url=f"/models/{new_model_id}/download"
        )
    except (FileNotFoundError, ThicknessError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


_UPLOAD_FILE = File(...)
_AMOUNT_MM_FORM = Form(...)
_QUAD_TARGET_FACES_FORM = Form(0, ge=0, le=1_000_000)
_CLEARANCE_MM_FORM = Form(8.0, gt=0)
_POUR_BOX_WALL_MM_FORM = Form(4.0, gt=0)
_KEY_DIAMETER_MM_FORM = Form(6.0, gt=0)
_SPRUE_DIAMETER_MM_FORM = Form(10.0, gt=0)
_VENT_DIAMETER_MM_FORM = Form(4.0, gt=0)
_CLAMP_WALL_MM_FORM = Form(6.0, gt=0)
_CLAMP_FLANGE_WIDTH_MM_FORM = Form(12.0, gt=0)
_BOLT_HOLE_DIAMETER_MM_FORM = Form(4.5, gt=0)
_MOLD_MODE_FORM = Form("silicone_block")
_DIRECT_MOLD_WALL_MM_FORM = Form(6.0, gt=0)
_SHELL_THICKNESS_MM_FORM = Form(3.0, gt=0)
_SKIN_POUR_WALL_MM_FORM = Form(3.0, gt=0)
_SUPPORT_JACKET_WALL_MM_FORM = Form(5.0, gt=0)
_CAST_WALL_THICKNESS_MM_FORM = Form(4.0, gt=0)
_PARTING_AXIS_FORM = Form("z")
_PARTING_OFFSET_MM_FORM = Form(0.0)
_MATERIAL_DENSITY_G_PER_CM3_FORM = Form(None, gt=0)


@app.post("/thicken")
async def thicken_uploaded_file(
    file: UploadFile = _UPLOAD_FILE,
    amount_mm: float = _AMOUNT_MM_FORM,
    quad_target_faces: int = _QUAD_TARGET_FACES_FORM,
) -> FileResponse:
    """
    Increase the wall thickness of an arbitrary uploaded STL/OBJ file via
    mesh shelling, and return the thickened STL directly (one round trip:
    upload -> modify -> get the file back). The new model_id and method
    are also exposed as response headers (X-Model-Id, X-Thicken-Method) if
    you need them for further calls, e.g. GET /models/{model_id}/download
    to re-fetch the same result later.
    """
    if amount_mm <= 0:
        raise HTTPException(status_code=422, detail="amount_mm must be greater than 0")
    if amount_mm > settings.max_wall_thickness_mm:
        raise HTTPException(
            status_code=422,
            detail=f"amount_mm exceeds MAX_WALL_THICKNESS_MM ({settings.max_wall_thickness_mm})",
        )
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in _ALLOWED_UPLOAD_SUFFIXES:
        raise HTTPException(
            status_code=422,
            detail=f"unsupported file type '{suffix}'; expected one of {sorted(_ALLOWED_UPLOAD_SUFFIXES)}",
        )

    content = await file.read()
    if len(content) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail=f"upload exceeds MAX_UPLOAD_BYTES ({settings.max_upload_bytes})")

    upload_model_id, upload_path = storage.save_upload(file.filename or "model.stl", content)
    try:
        new_model_id, new_dir = storage.new_model_dir()
        result_path = thickness.mesh_shell(upload_path, amount_mm, new_dir, quad_target_faces=quad_target_faces)
        storage.save_spec(new_model_id, {"parent_model_id": upload_model_id, "method": "mesh_shell"})
        return FileResponse(
            result_path,
            media_type="model/stl",
            filename=f"{new_model_id}.stl",
            headers={"X-Model-Id": new_model_id, "X-Thicken-Method": "mesh_shell"},
        )
    except ThicknessError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


_MOLD_PART_FILENAMES = {
    "silicone_block": ("pour_box_bottom.stl", "pour_box_top.stl", "clamp_shell_bottom.stl", "clamp_shell_top.stl"),
    "direct_cast": ("direct_mold_bottom.stl", "direct_mold_top.stl"),
    "form_fitting": (
        "skin_pour_bottom.stl",
        "skin_pour_top.stl",
        "support_jacket_bottom.stl",
        "support_jacket_top.stl",
    ),
    "hollow_cast": ("hollow_cast_bottom.stl", "hollow_cast_top.stl", "hollow_cast_core.stl"),
}


def _draft_summary(report: DraftReport | None) -> dict | None:
    """
    Compact {'releasable', 'problem_island_count'} summary of a
    DraftReport for MoldResponse/the upload endpoint's response headers
    - full per-face detail is available via
    POST /models/{model_id}/mold/draft-check instead of duplicating it
    here on every mold-generation response.
    """
    if report is None:
        return None
    return {"releasable": report.releasable, "problem_island_count": len(report.problem_islands)}


def _mold_error_status(exc: MoldError) -> int:
    """422 for a bad request the caller can fix (bad param, box too big, unrepairable mesh); 503 for Blender failing."""
    message = str(exc)
    if (
        "must be greater than 0" in message
        or "MAX_MOLD_DIMENSION_MM" in message
        or "is not watertight" in message
        or "hole(s) remain" in message
        or "parting_axis must be" in message
        or "would put the parting plane" in message
        or "must be smaller than the cavity's own footprint" in message
    ):
        return 422
    return 503


def _estimated_mass_g(volume_cm3: float, density_g_per_cm3: float | None) -> float | None:
    """cavity_volume_cm3 * density (FR-8), or None if no density was supplied."""
    return volume_cm3 * density_g_per_cm3 if density_g_per_cm3 is not None else None


@app.post("/models/{model_id}/mold", response_model=MoldResponse)
def mold_existing_model(model_id: str, request: MoldRequest) -> MoldResponse:
    """
    Generate mold tooling (silicone_block: pour box + clamp shell, 4
    parts; direct_cast: a single rigid mold, 2 parts; form_fitting: a
    thin skin-pour tool + support jacket, 4 parts; hollow_cast: the
    direct_cast mold + an inward-offset core, 3 parts - see mold.py) for
    a model this service generated (or previously thickened/repaired).
    Always produces a new model_id; download the resulting STL parts as
    one zip via GET /models/{model_id}/mold.zip.
    """
    try:
        source_path = storage.stl_path(model_id) if _has_stl(model_id) else _any_model_file(model_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    new_model_id, new_dir = storage.new_model_dir()
    mold_params = request.model_dump()
    density = mold_params.pop("material_density_g_per_cm3")
    try:
        result = mold.make_mold(source_path, new_dir, **mold_params)
    except MoldError as exc:
        shutil.rmtree(new_dir, ignore_errors=True)
        raise HTTPException(status_code=_mold_error_status(exc), detail=str(exc)) from exc

    storage.save_spec(new_model_id, {"parent_model_id": model_id, "method": "mold"})
    storage.make_zip(new_model_id, list(_MOLD_PART_FILENAMES[request.mode]))
    return MoldResponse(
        model_id=new_model_id,
        download_url=f"/models/{new_model_id}/mold.zip",
        repaired_hole_ids=result.repaired_hole_ids,
        draft_check=_draft_summary(result.draft_check),
        cavity_volume_cm3=result.cavity_volume_cm3,
        estimated_cast_mass_g=_estimated_mass_g(result.cavity_volume_cm3, density),
    )


@app.post("/mold")
async def mold_uploaded_file(
    file: UploadFile = _UPLOAD_FILE,
    mode: str = _MOLD_MODE_FORM,
    clearance_mm: float = _CLEARANCE_MM_FORM,
    pour_box_wall_mm: float = _POUR_BOX_WALL_MM_FORM,
    key_diameter_mm: float = _KEY_DIAMETER_MM_FORM,
    sprue_diameter_mm: float = _SPRUE_DIAMETER_MM_FORM,
    vent_diameter_mm: float = _VENT_DIAMETER_MM_FORM,
    clamp_wall_mm: float = _CLAMP_WALL_MM_FORM,
    clamp_flange_width_mm: float = _CLAMP_FLANGE_WIDTH_MM_FORM,
    bolt_hole_diameter_mm: float = _BOLT_HOLE_DIAMETER_MM_FORM,
    direct_mold_wall_mm: float = _DIRECT_MOLD_WALL_MM_FORM,
    shell_thickness_mm: float = _SHELL_THICKNESS_MM_FORM,
    skin_pour_wall_mm: float = _SKIN_POUR_WALL_MM_FORM,
    support_jacket_wall_mm: float = _SUPPORT_JACKET_WALL_MM_FORM,
    cast_wall_thickness_mm: float = _CAST_WALL_THICKNESS_MM_FORM,
    parting_axis: str = _PARTING_AXIS_FORM,
    parting_offset_mm: float = _PARTING_OFFSET_MM_FORM,
    material_density_g_per_cm3: float | None = _MATERIAL_DENSITY_G_PER_CM3_FORM,
) -> FileResponse:
    """
    Generate a mold for an arbitrary uploaded mesh this service didn't
    generate (silicone_block: pour box + clamp shell, 4 parts;
    direct_cast: a single rigid mold, 2 parts; form_fitting: a thin
    skin-pour tool + support jacket, 4 parts; hollow_cast: the
    direct_cast mold + an inward-offset core, 3 parts - see mold.py), and
    return the resulting STL parts as one zip directly - same
    one-round-trip shape as POST /thicken.
    """
    if mode not in _MOLD_PART_FILENAMES:
        raise HTTPException(status_code=422, detail=f"mode must be one of {sorted(_MOLD_PART_FILENAMES)}")
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in _ALLOWED_UPLOAD_SUFFIXES:
        raise HTTPException(
            status_code=422,
            detail=f"unsupported file type '{suffix}'; expected one of {sorted(_ALLOWED_UPLOAD_SUFFIXES)}",
        )
    content = await file.read()
    if len(content) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail=f"upload exceeds MAX_UPLOAD_BYTES ({settings.max_upload_bytes})")

    upload_model_id, upload_path = storage.save_upload(file.filename or "model.stl", content)
    new_model_id, new_dir = storage.new_model_dir()
    try:
        result = mold.make_mold(
            upload_path,
            new_dir,
            mode=mode,
            clearance_mm=clearance_mm,
            pour_box_wall_mm=pour_box_wall_mm,
            key_diameter_mm=key_diameter_mm,
            sprue_diameter_mm=sprue_diameter_mm,
            vent_diameter_mm=vent_diameter_mm,
            clamp_wall_mm=clamp_wall_mm,
            clamp_flange_width_mm=clamp_flange_width_mm,
            bolt_hole_diameter_mm=bolt_hole_diameter_mm,
            direct_mold_wall_mm=direct_mold_wall_mm,
            shell_thickness_mm=shell_thickness_mm,
            skin_pour_wall_mm=skin_pour_wall_mm,
            support_jacket_wall_mm=support_jacket_wall_mm,
            cast_wall_thickness_mm=cast_wall_thickness_mm,
            parting_axis=parting_axis,
            parting_offset_mm=parting_offset_mm,
        )
    except MoldError as exc:
        shutil.rmtree(new_dir, ignore_errors=True)
        raise HTTPException(status_code=_mold_error_status(exc), detail=str(exc)) from exc

    storage.save_spec(new_model_id, {"parent_model_id": upload_model_id, "method": "mold"})
    zip_path = storage.make_zip(new_model_id, list(_MOLD_PART_FILENAMES[mode]))
    headers = {
        "X-Model-Id": new_model_id,
        "X-Repaired-Hole-Ids": ",".join(str(i) for i in result.repaired_hole_ids),
        "X-Cavity-Volume-Cm3": str(result.cavity_volume_cm3),
    }
    if result.draft_check is not None:
        headers["X-Draft-Releasable"] = str(result.draft_check.releasable).lower()
        headers["X-Draft-Problem-Island-Count"] = str(len(result.draft_check.problem_islands))
    mass = _estimated_mass_g(result.cavity_volume_cm3, material_density_g_per_cm3)
    if mass is not None:
        headers["X-Estimated-Cast-Mass-G"] = str(mass)
    return FileResponse(zip_path, media_type="application/zip", filename=f"{new_model_id}_mold.zip", headers=headers)


@app.get("/models/{model_id}/mold.zip")
def download_mold_zip(model_id: str) -> FileResponse:
    """Download the generated mold STL parts (4 for silicone_block/form_fitting, 3 for hollow_cast, 2 for
    direct_cast) as one zip."""
    path = storage.model_dir(model_id) / "mold.zip"
    if not path.is_file():
        raise HTTPException(
            status_code=404, detail="No mold generated yet for this model_id - call POST /models/{model_id}/mold first"
        )
    return FileResponse(path, media_type="application/zip", filename=f"{model_id}_mold.zip")


def _problem_island_to_dict(i) -> dict:
    """Convert a ProblemFaceIsland dataclass into a JSON-safe dict for an HTTP response."""
    return {
        "id": i.id,
        "face_indices": i.face_indices,
        "centroid": list(i.centroid),
        "face_count": i.face_count,
        "min_draft_angle_deg": i.min_draft_angle_deg,
        "classification": i.classification,
    }


def _draft_error_status(exc: DraftAnalysisError) -> int:
    """422 for a bad request the caller can fix (bad axis/threshold); 503 for Blender itself failing."""
    message = str(exc)
    if "pull_axis must be" in message or "min_draft_angle_deg must be" in message:
        return 422
    return 503


@app.post("/models/{model_id}/mold/draft-check", response_model=DraftCheckResponse)
def check_model_draft(model_id: str, request: DraftCheckRequest) -> DraftCheckResponse:
    """
    Report which faces of a model would prevent a *rigid* two-part mold
    from releasing cleanly along request.pull_axis (FR-4) - read-only,
    generates no files. Purely informational for silicone_block
    (flexible material forgives most undercuts); direct_cast already
    runs this automatically as a warning inside POST /mold /
    POST /models/{model_id}/mold, so calling this first is mainly useful
    to try a different pull_axis/threshold before committing to a full
    mold generation.
    """
    try:
        source_path = storage.stl_path(model_id) if _has_stl(model_id) else _any_model_file(model_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    try:
        report = draft_analysis.analyze_draft(
            str(source_path), pull_axis=request.pull_axis, min_draft_angle_deg=request.min_draft_angle_deg
        )
    except DraftAnalysisError as exc:
        raise HTTPException(status_code=_draft_error_status(exc), detail=str(exc)) from exc

    return DraftCheckResponse(
        source_path=report.source_path,
        pull_axis=report.pull_axis,
        parting_coordinate=report.parting_coordinate,
        min_draft_angle_deg=report.min_draft_angle_deg,
        releasable=report.releasable,
        problem_islands=[_problem_island_to_dict(i) for i in report.problem_islands],
        vertex_count=report.vertex_count,
        face_count=report.face_count,
        blender_version=report.blender_version,
    )


_WATERTIGHT_UPLOAD_SUFFIXES = watertight.SUPPORTED_EXTENSIONS


def _hole_to_dict(h: Hole) -> dict:
    """Convert a Hole dataclass into a JSON-safe dict for an HTTP response."""
    return {
        "id": h.id,
        "vertex_indices": h.vertex_indices,
        "centroid": list(h.centroid),
        "area": h.area,
        "perimeter": h.perimeter,
        "planarity": h.planarity,
        "classification": h.classification.value,
        "confidence": h.confidence,
        "reason": h.reason,
    }


def _island_to_dict(i: FlippedNormalIsland) -> dict:
    """Convert a FlippedNormalIsland dataclass into a JSON-safe dict for an HTTP response."""
    return {"id": i.id, "face_indices": i.face_indices, "centroid": list(i.centroid), "face_count": i.face_count}


@app.post("/watertight/upload", response_model=UploadResponse)
async def upload_for_watertight_check(file: UploadFile = _UPLOAD_FILE) -> UploadResponse:
    """Upload an arbitrary mesh file to check/repair for watertightness (not generated by this service)."""
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in _WATERTIGHT_UPLOAD_SUFFIXES:
        raise HTTPException(
            status_code=422,
            detail=f"unsupported file type {suffix!r}; supported: {sorted(_WATERTIGHT_UPLOAD_SUFFIXES)}",
        )
    content = await file.read()
    if len(content) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail=f"upload exceeds MAX_UPLOAD_BYTES ({settings.max_upload_bytes})")

    model_id, _path = storage.save_upload(file.filename or "model.stl", content)
    return UploadResponse(model_id=model_id, filename=file.filename or "model.stl")


@app.post("/models/{model_id}/analyze", response_model=AnalyzeResponse)
def analyze_model_watertightness(model_id: str) -> AnalyzeResponse:
    """
    Analyze a model (generated or uploaded via /watertight/upload) for
    watertightness: find boundary-edge holes and inverted-normal
    islands, and classify each hole as a likely intentional opening or a
    likely defect.
    """
    try:
        source_path = storage.stl_path(model_id) if _has_stl(model_id) else _any_model_file(model_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    viewer_path = storage.model_dir(model_id) / _VIEWER_GLB_FILENAME
    try:
        report = watertight.analyze_mesh(str(source_path), viewer_output=str(viewer_path))
    except WatertightError as exc:
        logger.error("Watertight analysis failed for model_id=%s: %s", model_id, exc)
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return AnalyzeResponse(
        model_id=model_id,
        is_watertight=report.is_watertight,
        vertex_count=report.vertex_count,
        face_count=report.face_count,
        total_surface_area=report.total_surface_area,
        bounding_box={"min": report.bounding_box.min, "max": report.bounding_box.max},
        holes=[_hole_to_dict(h) for h in report.holes],
        flipped_normal_islands=[_island_to_dict(i) for i in report.flipped_normal_islands],
        nonmanifold_junction_edge_count=report.nonmanifold_junction_edge_count,
        self_intersection_count=report.self_intersection_count,
        mesh_volume_cm3=(report.mesh_volume_mm3 / 1000.0) if report.mesh_volume_mm3 is not None else None,
        blender_version=report.blender_version,
        viewer_glb_url=f"/models/{model_id}/viewer.glb" if report.viewer_path else None,
    )


@app.post("/models/{model_id}/repair", response_model=RepairResponse)
def repair_model_holes(model_id: str, request: RepairRequest) -> RepairResponse:
    """
    Close the given hole ids (from a prior /analyze call on this exact
    model_id) and re-check watertightness. Overwrites this model_id's
    STL in place (unlike /thicken, which always produces a new model_id)
    since a repair is a correction to the same model, not a variant.

    With auto_repair=true (and no hole_ids), the model is re-analyzed and
    only holes classified LIKELY_DEFECT are closed; intentional-looking and
    ambiguous openings are left alone and listed in skipped_hole_ids. If
    there's nothing to fix (no defect holes, no flipped-normal islands) the
    file is left untouched and changed=false is returned.
    """
    try:
        source_path = storage.stl_path(model_id) if _has_stl(model_id) else _any_model_file(model_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    viewer_path = storage.model_dir(model_id) / _VIEWER_GLB_FILENAME
    hole_ids = request.hole_ids
    skipped_hole_ids: list[int] = []
    if request.auto_repair:
        try:
            report = watertight.analyze_mesh(str(source_path))
        except WatertightError as exc:
            logger.error("Auto-repair analysis failed for model_id=%s: %s", model_id, exc)
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        hole_ids = [h.id for h in report.holes if h.classification == HoleClassification.LIKELY_DEFECT]
        skipped_hole_ids = [h.id for h in report.holes if h.classification != HoleClassification.LIKELY_DEFECT]
        if not hole_ids and not report.flipped_normal_islands:
            logger.info("Auto-repair of model_id=%s: nothing to fix", model_id)
            return RepairResponse(
                model_id=model_id,
                closed_hole_ids=[],
                is_watertight=report.is_watertight,
                remaining_holes=[_hole_to_dict(h) for h in report.holes],
                viewer_glb_url=f"/models/{model_id}/viewer.glb" if viewer_path.is_file() else None,
                download_url=f"/models/{model_id}/download",
                changed=False,
                skipped_hole_ids=skipped_hole_ids,
            )

    try:
        result = watertight.repair_mesh(
            str(source_path),
            hole_ids,
            str(storage.model_dir(model_id) / "model.stl"),
            viewer_output=str(viewer_path),
            quad_target_faces=request.quad_target_faces,
            allow_normals_only=request.auto_repair,
        )
    except WatertightError as exc:
        logger.error("Watertight repair failed for model_id=%s: %s", model_id, exc)
        raise HTTPException(status_code=422 if "not found" in str(exc) else 503, detail=str(exc)) from exc

    return RepairResponse(
        model_id=model_id,
        closed_hole_ids=result.closed_hole_ids,
        is_watertight=result.is_watertight,
        remaining_holes=[_hole_to_dict(h) for h in result.remaining_holes],
        viewer_glb_url=f"/models/{model_id}/viewer.glb" if result.viewer_path else None,
        download_url=f"/models/{model_id}/download",
        skipped_hole_ids=skipped_hole_ids,
    )


@app.get("/models/{model_id}/viewer.glb")
def download_viewer_glb(model_id: str) -> FileResponse:
    """Serve the web-viewable GLB preview of a model, produced by a prior /analyze or /repair call."""
    path = storage.model_dir(model_id) / _VIEWER_GLB_FILENAME
    if not path.is_file():
        raise HTTPException(
            status_code=404, detail="No viewer preview yet - call POST /models/{model_id}/analyze first"
        )
    return FileResponse(path, media_type="model/gltf-binary")


_STEP_UPLOAD_SUFFIXES = {".step", ".stp"}


@app.post("/step/upload", response_model=UploadResponse)
async def upload_step_file(file: UploadFile = _UPLOAD_FILE) -> UploadResponse:
    """
    Upload a STEP file and convert it to a mesh (model.stl) via FreeCAD,
    so it can be downloaded, thickened, or analyzed the same way as any
    other model. Returns the *converted* model_id, not the raw upload's.
    """
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in _STEP_UPLOAD_SUFFIXES:
        raise HTTPException(
            status_code=422,
            detail=f"unsupported file type {suffix!r}; expected one of {sorted(_STEP_UPLOAD_SUFFIXES)}",
        )
    content = await file.read()
    if len(content) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail=f"upload exceeds MAX_UPLOAD_BYTES ({settings.max_upload_bytes})")

    raw_model_id, raw_path = storage.save_upload(file.filename or "model.step", content)
    try:
        model_id, model_directory = storage.new_model_dir()
        freecad_cad.step_to_mesh(raw_path, model_directory / "model.stl")
        storage.save_spec(model_id, {"parent_model_id": raw_model_id, "method": "step_import"})
    except FreeCADCADError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return UploadResponse(model_id=model_id, filename=file.filename or "model.step")


@app.post("/models/{model_id}/export-step")
def export_model_as_step(model_id: str) -> FileResponse:
    """
    Convert a model's mesh into a STEP solid and return it (a
    best-effort B-rep wrap of the mesh's triangles, not true
    reverse-engineered parametric CAD - see freecad_cad.mesh_to_step()'s
    docstring).
    """
    try:
        source_path = storage.stl_path(model_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    step_path = storage.model_dir(model_id) / "model.step"
    try:
        freecad_cad.mesh_to_step(source_path, step_path)
    except FreeCADCADError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return FileResponse(step_path, media_type="model/step", filename=f"{model_id}.step")


@app.post("/models/{model_id}/repair-solid", response_model=RepairSolidResponse)
def repair_model_solid(model_id: str) -> RepairSolidResponse:
    """
    Heal malformed B-rep topology via FreeCAD's ShapeFix (a different
    class of repair than /repair's Blender-based open-boundary hole
    filling - see freecad_cad.heal_solid()'s docstring). Overwrites this
    model_id's STL in place, matching /repair's "correction, not a
    variant" convention.
    """
    try:
        source_path = storage.stl_path(model_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    try:
        result = freecad_cad.heal_solid(source_path, source_path)
    except FreeCADCADError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return RepairSolidResponse(
        model_id=model_id,
        fixed=result["fixed"],
        valid_before=result["valid_before"],
        valid_after=result["valid_after"],
        download_url=f"/models/{model_id}/download",
    )


_THUMBNAIL_SIZE_FORM = Form(thumbnail.DEFAULT_SIZE_PX, ge=thumbnail.MIN_SIZE_PX)


@app.post("/thumbnail")
async def render_thumbnail(file: UploadFile = _UPLOAD_FILE, size: int = _THUMBNAIL_SIZE_FORM) -> FileResponse:
    """
    Render a PNG thumbnail of an uploaded mesh or STEP file via headless
    Blender (STEP first converted to a mesh via FreeCAD), returning the
    image directly - one round trip, mirroring /thicken's shape. Intended
    for a caller like the farm-manager sibling repo's library scanner,
    which has no CAD tooling of its own.
    """
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in thumbnail.SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=422,
            detail=f"unsupported file type {suffix!r}; expected one of {sorted(thumbnail.SUPPORTED_EXTENSIONS)}",
        )
    if size > settings.thumbnail_max_size_px:
        raise HTTPException(
            status_code=422, detail=f"size exceeds THUMBNAIL_MAX_SIZE_PX ({settings.thumbnail_max_size_px})"
        )
    content = await file.read()
    if len(content) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail=f"upload exceeds MAX_UPLOAD_BYTES ({settings.max_upload_bytes})")

    upload_model_id, upload_path = storage.save_upload(file.filename or "model.stl", content)
    try:
        png_path = storage.model_dir(upload_model_id) / "thumbnail.png"
        thumbnail.render_mesh_thumbnail(upload_path, png_path, size=size)
    except thumbnail.ThumbnailError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return FileResponse(png_path, media_type="image/png", filename=f"{upload_model_id}.png")


@app.post("/tags/suggest", response_model=TagSuggestResponse)
def suggest_tags(request: TagSuggestRequest) -> TagSuggestResponse:
    """
    Suggest descriptive tags for a library file from its name/metadata via
    the configured LLM backend (Ollama-first). Text-only reasoning, not
    vision -- see tag_suggester.py's header for the upgrade path. Never
    fails: falls back to filename-derived heuristic tags (method field
    reports which path was used) rather than returning an error.
    """
    tags, method = tag_suggester.suggest_tags(request)
    return TagSuggestResponse(tags=tags, method=method)


def _has_stl(model_id: str) -> bool:
    """Return True if this model_id's directory has a model.stl (the common case: generated or .stl upload)."""
    try:
        storage.stl_path(model_id)
        return True
    except FileNotFoundError:
        return False


def _any_model_file(model_id: str) -> Path:
    """
    Return this model_id's mesh file when it isn't model.stl (e.g. an
    upload kept its original .obj/.ply/... extension). Raises
    FileNotFoundError if the model_id doesn't exist or has no mesh file.
    """
    directory = storage.model_dir(model_id)
    for candidate in directory.glob("model.*"):
        if candidate.suffix.lower() in watertight.SUPPORTED_EXTENSIONS:
            return candidate
    raise FileNotFoundError(f"model {model_id!r} has no supported mesh file")


# Mounted last and at "/" so it only serves requests none of the API routes
# above matched; StaticFiles(html=True) serves static/index.html at "/" -
# the browser UI for picking a file to load/thicken/download (see that
# file's header comment) or generating a model from a prompt.
app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")
