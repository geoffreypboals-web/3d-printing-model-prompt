"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/src/threedprompt/main.py
Description: FastAPI application wiring together the classifier, the two
    generation backends (OpenSCAD for simple parts, Blender for complex
    ones), the wall-thickness endpoints, the mold-generation endpoints,
    the spiral wand endpoints (twisted wand + figurine riders), the
    split-to-fit endpoint, the tool-insert-from-a-photo endpoints, the
    rule-based templates (/templates/...), multi-colour 3MF export, and the
    watertight analysis/repair endpoints. This is the only
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
    - 503 from /transcribe means local speech-to-text couldn't run -
      see transcription.py's header (missing faster-whisper, or the
      Whisper model couldn't download on first use).
    - 503 from /thumbnail on a .step/.stp upload specifically can mean
      either Blender or FreeCAD is unavailable - STEP thumbnails go
      through both (FreeCAD converts to a mesh first, Blender renders it).
    - /tags/suggest never 503s - if the LLM is unreachable or its response
      is unparseable, it falls back to filename-derived heuristic tags and
      reports method="heuristic_fallback" (check GET /health's llm field
      to tell "unreachable" apart from "reachable but replied
      unparseable"). See tag_suggester.py's header for the text-only vs.
      vision-based tagging tradeoff.
    - 422 from /models/{id}/overhangs means the model file can't be read
      as a mesh (or angle_deg isn't between 0 and 90).
    - 422 from /models/{id}/split carries split.py's message (no grid
      within max_pieces fits the bed, or an unusable mesh).
    - 422 from /tool-insert/trace or /tool-insert carries tool_insert.py's
      message (unreadable photo, no tools found, a bad outline); 413 means
      the photo is over MAX_IMAGE_UPLOAD_BYTES (25 MB).
    - 422 from /templates/... carries the template's own message (settings
      that can't make a printable part, content too long for a QR code,
      an unreadable lithophane image).
    - 422 from /models/{id}/export-3mf: a part name that isn't one of the
      model's STLs, or a model with no parts (export a template, split,
      wand or insert build, or a model with model.stl).
    - 422 from /spiral-wand or /models/{id}/spiral-wand/rider carries
      spiral_wand.py's own message (impossible dimensions, or a mesh
      that isn't a closed solid - repair it in /watertight.html first).
    - Run locally with: uvicorn threedprompt.main:app --reload
      (see README.md at /home/user/3d-printing-model-prompt/README.md
      for the full command including PYTHONPATH setup).
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import trimesh
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from threedprompt import (
    blender_generator,
    cadquery_gen,
    draft_analysis,
    freecad_cad,
    gear,
    geometry,
    gridfinity,
    lithophane,
    mold,
    openscad_generator,
    qr_tag,
    spiral_wand,
    split,
    storage,
    tag_suggester,
    terrain,
    thickness,
    threemf,
    thumbnail,
    tool_insert,
    transcription,
    watertight,
)
from threedprompt.blender_generator import BlenderGenerationError
from threedprompt.cadquery_gen import CadQueryGenerationError
from threedprompt.classifier import classify_prompt
from threedprompt.config import settings
from threedprompt.draft_analysis import DraftAnalysisError
from threedprompt.freecad_cad import FreeCADCADError
from threedprompt.geometry import TemplateError
from threedprompt.llm_client import LLMError, get_llm_client
from threedprompt.logging_config import configure_logging, get_logger
from threedprompt.models import (
    AnalyzeResponse,
    Backend,
    ClassificationMethod,
    ClassificationResult,
    Complexity,
    DraftCheckRequest,
    DraftCheckResponse,
    DraftReport,
    Export3mfRequest,
    Export3mfResponse,
    FlippedNormalIsland,
    GearRequest,
    GenerateRequest,
    GenerateResponse,
    GridfinityBinRequest,
    HealthResponse,
    Hole,
    HoleClassification,
    LithophaneRequest,
    MinisBinRequest,
    MoldRequest,
    MoldResponse,
    OverhangResponse,
    QrTagRequest,
    RepairRequest,
    RepairResponse,
    RepairSolidResponse,
    SpiralRiderRequest,
    SpiralWandRequest,
    SpiralWandResponse,
    SpiralWandResult,
    SplitRequest,
    SplitResponse,
    SplitResult,
    TagSuggestRequest,
    TagSuggestResponse,
    TemplateResponse,
    TemplateResult,
    TerrainRequest,
    ThickenRequest,
    ThickenResponse,
    ToolInsertRequest,
    ToolInsertResponse,
    TraceResult,
    TranscribeResponse,
    UploadResponse,
)
from threedprompt.mold import MoldError
from threedprompt.openscad_generator import OpenScadGenerationError
from threedprompt.spiral_wand import SpiralWandError
from threedprompt.split import SplitError
from threedprompt.thickness import ThicknessError
from threedprompt.tool_insert import ToolInsertError
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


def _use_cadquery(prompt: str, use_templates: bool = True) -> bool:
    """CadQuery writes simple parts no OpenSCAD template covers, when configured and the sandbox can run here."""
    if settings.simple_llm_backend != "cadquery" or (use_templates and openscad_generator.has_template(prompt)):
        return False
    if not cadquery_gen.available():
        logger.warning("SIMPLE_LLM_BACKEND=cadquery but the sandbox can't run here; using OpenSCAD.")
        return False
    return True


@app.post("/generate", response_model=GenerateResponse)
def generate(request: GenerateRequest) -> GenerateResponse:
    """Classify a prompt and generate a model: OpenSCAD template or CadQuery/OpenSCAD (simple), Blender (complex)."""
    prompt = request.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=422, detail="prompt must not be empty")
    if len(prompt) > settings.max_prompt_length:
        raise HTTPException(
            status_code=422,
            detail=f"prompt exceeds MAX_PROMPT_LENGTH ({settings.max_prompt_length} characters)",
        )

    if request.backend is Backend.CADQUERY and not cadquery_gen.available():
        raise HTTPException(status_code=422, detail="backend cadquery was asked for, but its sandbox can't run here")

    cached_model_id = storage.lookup_cached_model(prompt, request.wall_thickness_mm) if request.use_templates else None
    cached_spec = (storage.load_spec(cached_model_id) or {}) if cached_model_id else {}
    if cached_model_id and request.backend and cached_spec.get("backend") != request.backend.value:
        cached_model_id = None  # made by another backend: make it again with the one asked for
    if cached_model_id:
        spec = cached_spec
        return GenerateResponse(
            model_id=cached_model_id,
            backend=Backend(spec.get("backend", Backend.OPENSCAD.value)),
            classification_label=Complexity(spec.get("classification_label", Complexity.SIMPLE.value)),
            classification_confidence=spec.get("classification_confidence", 0.0),
            classification_method=spec.get("classification_method", "cache"),
            classification_reasoning="Served from cache: identical prompt already generated.",
            download_url=f"/models/{cached_model_id}/download",
        )

    if request.backend:
        classification = ClassificationResult(
            Complexity.COMPLEX if request.backend is Backend.BLENDER else Complexity.SIMPLE,
            1.0,
            ClassificationMethod.REQUESTED,
            f"The caller asked for {request.backend.value}.",
        )
    else:
        classification = classify_prompt(prompt)

    no_templates = {} if request.use_templates else {"use_templates": False}  # only passed when it's off
    model_id, model_directory = storage.new_model_dir()
    try:
        if request.backend is Backend.CADQUERY:
            result = cadquery_gen.generate(prompt, model_directory, request.wall_thickness_mm)
        elif request.backend is Backend.OPENSCAD:
            result = openscad_generator.generate(prompt, model_directory, request.wall_thickness_mm, **no_templates)
        elif classification.label is Complexity.SIMPLE and _use_cadquery(prompt, request.use_templates):
            result = cadquery_gen.generate(prompt, model_directory, request.wall_thickness_mm)
        elif classification.label is Complexity.SIMPLE:
            result = openscad_generator.generate(prompt, model_directory, request.wall_thickness_mm, **no_templates)
        else:
            result = blender_generator.generate(prompt, model_directory)
    except (OpenScadGenerationError, BlenderGenerationError, CadQueryGenerationError, LLMError) as exc:
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
_THUMBNAIL_VIEW_FORM = Form("iso")


@app.post("/thumbnail")
async def render_thumbnail(
    file: UploadFile = _UPLOAD_FILE, size: int = _THUMBNAIL_SIZE_FORM, view: str = _THUMBNAIL_VIEW_FORM
) -> FileResponse:
    """
    Render a PNG thumbnail of an uploaded mesh or STEP file via headless
    Blender (STEP first converted to a mesh via FreeCAD), returning the
    image directly - one round trip, mirroring /thicken's shape. Intended
    for a caller like the farm-manager sibling repo's library scanner,
    which has no CAD tooling of its own. `view` (iso, top, bottom, front)
    picks the camera, so a checker can see faces the iso view hides.
    """
    if view not in thumbnail.VIEWS:
        raise HTTPException(status_code=422, detail=f"view must be one of {list(thumbnail.VIEWS)}")
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
        extra = {} if view == "iso" else {"view": view}  # only passed when it isn't the default
        thumbnail.render_mesh_thumbnail(upload_path, png_path, size=size, **extra)
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


@app.post("/transcribe", response_model=TranscribeResponse)
async def transcribe_audio(file: UploadFile = _UPLOAD_FILE) -> TranscribeResponse:
    """
    Transcribe a short voice recording to text with local faster-whisper
    (transcription.py). Used by the farm-manager sibling repo's AI Model
    Generator interview so answers can be spoken. 422 for a non-audio file
    type, 413 over MAX_AUDIO_UPLOAD_BYTES, 503 if the model can't run.
    """
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in transcription.SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=422,
            detail=f"unsupported audio type {suffix!r}; expected one of {sorted(transcription.SUPPORTED_EXTENSIONS)}",
        )
    content = await file.read()
    if len(content) > settings.max_audio_upload_bytes:
        raise HTTPException(
            status_code=413, detail=f"recording exceeds MAX_AUDIO_UPLOAD_BYTES ({settings.max_audio_upload_bytes})"
        )
    try:
        text = await run_in_threadpool(transcription.transcribe, content)
    except transcription.TranscriptionError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return TranscribeResponse(text=text)


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


# --- Spiral wand (spiral_wand.py) ---

_MODEL_ID_RE = re.compile(r"^[0-9a-f]{32}$")
# Part files the in-process tools write; GET /models/{id}/parts/{filename} serves only these.
_PART_RE = re.compile(
    r"^(wand_body|wand_section\d{1,2}|wand_cap|rider|part_\d{2}|dowel_pin|tool_insert"
    r"|gridfinity_bin|minis_bin|terrain_tile|gear|gear_mate|rack|lithophane|qr_tag|qr_plate|qr_code)\.stl$"
)
_SPIRAL_ZIP_NAME = "spiral_wand.zip"


def _checked_model_dir(model_id: str) -> Path:
    """Resolve a model_id to its directory, rejecting anything that isn't a 32-hex id (no path tricks)."""
    if not _MODEL_ID_RE.match(model_id):
        raise HTTPException(status_code=404, detail=f"no model with id {model_id!r}")
    try:
        return storage.model_dir(model_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _mesh_source(model_id: str) -> Path:
    """
    Return the mesh file spiral_wand.py should read for model_id: model.stl
    if present, else the first model.* in a format trimesh can load.
    Raises HTTPException 404 for an unknown model_id, 422 for an unusable format.
    """
    directory = _checked_model_dir(model_id)
    candidates = sorted(directory.glob("model.*"), key=lambda p: p.suffix.lower() != ".stl")
    for candidate in candidates:
        if candidate.suffix.lower() in spiral_wand.SUPPORTED_EXTENSIONS:
            return candidate
    raise HTTPException(
        status_code=422,
        detail=f"model {model_id!r} has no mesh in a supported format {sorted(spiral_wand.SUPPORTED_EXTENSIONS)}",
    )


def _spiral_response(model_id: str, result: SpiralWandResult) -> SpiralWandResponse:
    """Zip a spiral wand/rider build's parts and wrap its result with download URLs."""
    storage.make_zip(model_id, [part.filename for part in result.parts], zip_name=_SPIRAL_ZIP_NAME)
    parts = [part.model_copy(update={"url": f"/models/{model_id}/parts/{part.filename}"}) for part in result.parts]
    return SpiralWandResponse(
        **result.model_dump(exclude={"parts"}),
        parts=parts,
        model_id=model_id,
        download_url=f"/models/{model_id}/spiral-wand.zip",
    )


@app.post("/spiral-wand", response_model=SpiralWandResponse)
def build_spiral_wand(request: SpiralWandRequest) -> SpiralWandResponse:
    """
    Build a spiral wand: handle, rest gap, twisted spiral and end cap,
    split into sections that fit max_print_height_mm. No LLM call. Set
    cap_model_id to use an uploaded or generated model as the end cap.
    """
    params = request.model_dump()
    cap_model_id = params.pop("cap_model_id")
    cap_path = _mesh_source(cap_model_id) if cap_model_id else None
    new_model_id, new_dir = storage.new_model_dir()
    try:
        result = spiral_wand.make_wand(new_dir, cap_mesh_path=cap_path, **params)
    except SpiralWandError as exc:
        shutil.rmtree(new_dir, ignore_errors=True)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    storage.save_spec(new_model_id, {"method": "spiral_wand", "params": request.model_dump()})
    return _spiral_response(new_model_id, result)


@app.post("/models/{model_id}/spiral-wand/rider", response_model=SpiralWandResponse)
def build_spiral_rider(model_id: str, request: SpiralRiderRequest) -> SpiralWandResponse:
    """
    Turn a model (generated from a prompt, or uploaded via
    /watertight/upload) into a rider: the figurine with a twisted bore
    that matches a wand built with the same rod settings.
    """
    source = _mesh_source(model_id)
    new_model_id, new_dir = storage.new_model_dir()
    try:
        result = spiral_wand.make_rider(source, new_dir, **request.model_dump())
    except SpiralWandError as exc:
        shutil.rmtree(new_dir, ignore_errors=True)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    storage.save_spec(
        new_model_id, {"parent_model_id": model_id, "method": "spiral_rider", "params": request.model_dump()}
    )
    return _spiral_response(new_model_id, result)


@app.get("/models/{model_id}/spiral-wand.zip")
def download_spiral_wand_zip(model_id: str) -> FileResponse:
    """Download every part of a spiral wand or rider build as one zip."""
    path = _checked_model_dir(model_id) / _SPIRAL_ZIP_NAME
    if not path.is_file():
        raise HTTPException(status_code=404, detail="no spiral wand build for this model_id")
    return FileResponse(path, media_type="application/zip", filename=f"{model_id}_spiral_wand.zip")


@app.get("/models/{model_id}/parts/{filename}")
def download_part(model_id: str, filename: str) -> FileResponse:
    """Download one STL part of a wand, rider, split, tool insert or template build (used by the browser viewers)."""
    if not _PART_RE.match(filename):
        raise HTTPException(status_code=404, detail=f"no part named {filename!r}")
    path = _checked_model_dir(model_id) / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"no part named {filename!r} for this model_id")
    return FileResponse(path, media_type="model/stl", filename=filename)


# --- Split to fit the bed (split.py) ---

_SPLIT_ZIP_NAME = "split.zip"


@app.post("/models/{model_id}/split", response_model=SplitResponse)
def split_model(model_id: str, request: SplitRequest) -> SplitResponse:
    """
    Cut a model too big for a printer into pieces that each fit the bed,
    with dowel holes or one-way D-pegs on every cut face. Pieces keep the
    model's coordinates, so they reassemble with no transform.
    """
    source = _mesh_source(model_id)
    new_model_id, new_dir = storage.new_model_dir()
    try:
        result: SplitResult = split.split_to_fit(source, new_dir, **request.model_dump())
    except SplitError as exc:
        shutil.rmtree(new_dir, ignore_errors=True)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    storage.save_spec(new_model_id, {"parent_model_id": model_id, "method": "split", "params": request.model_dump()})
    storage.make_zip(new_model_id, [part.filename for part in result.parts], zip_name=_SPLIT_ZIP_NAME)
    parts = [part.model_copy(update={"url": f"/models/{new_model_id}/parts/{part.filename}"}) for part in result.parts]
    return SplitResponse(
        **result.model_dump(exclude={"parts"}),
        parts=parts,
        model_id=new_model_id,
        download_url=f"/models/{new_model_id}/split.zip",
    )


@app.get("/models/{model_id}/split.zip")
def download_split_zip(model_id: str) -> FileResponse:
    """Download every piece (and the dowel pin) of a split as one zip."""
    path = _checked_model_dir(model_id) / _SPLIT_ZIP_NAME
    if not path.is_file():
        raise HTTPException(status_code=404, detail="no split for this model_id")
    return FileResponse(path, media_type="application/zip", filename=f"{model_id}_split.zip")


# --- Tool insert from a photo (tool_insert.py) ---

_MAX_IMAGE_UPLOAD_BYTES = 25 * 1024 * 1024
_SQUARE_MM_FORM = Form(20.0, gt=0, le=200)
_MM_PER_PX_FORM = Form(None, gt=0, le=10)
_PAPER_FORM = Form("a4", pattern="^(a4|letter|a3|legal|other)$")


@app.post("/tool-insert/trace", response_model=TraceResult)
async def trace_tools(
    file: UploadFile = _UPLOAD_FILE,
    square_mm: float = _SQUARE_MM_FORM,
    mm_per_px: float | None = _MM_PER_PX_FORM,
    paper: str = _PAPER_FORM,
) -> TraceResult:
    """
    Trace tool outlines from a photo of tools on white paper with a dark
    calibration square (square_mm wide); paper is the sheet size (a4,
    letter, a3, legal or other). The outlines come back for the
    user to edit; nothing is stored.
    """
    content = await file.read()
    if len(content) > _MAX_IMAGE_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"photo exceeds {_MAX_IMAGE_UPLOAD_BYTES // (1024 * 1024)} MB")
    try:
        return tool_insert.trace(content, square_mm, mm_per_px, paper)
    except ToolInsertError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/tool-insert", response_model=ToolInsertResponse)
def build_tool_insert(request: ToolInsertRequest) -> ToolInsertResponse:
    """Build a tray with a pocket for each (edited) tool outline; Gridfinity or a plain fitted tray."""
    params = request.model_dump()
    tools = params.pop("tools")
    new_model_id, new_dir = storage.new_model_dir()
    try:
        result = tool_insert.make_insert(new_dir, tools, **params)
    except ToolInsertError as exc:
        shutil.rmtree(new_dir, ignore_errors=True)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    storage.save_spec(new_model_id, {"method": "tool_insert", "params": request.model_dump()})
    parts = [part.model_copy(update={"url": f"/models/{new_model_id}/parts/{part.filename}"}) for part in result.parts]
    return ToolInsertResponse(**result.model_dump(exclude={"parts"}), parts=parts, model_id=new_model_id)


# --- Rule-based templates (gridfinity.py, gear.py, lithophane.py, qr_tag.py) ---

_LITHO_FIELDS = LithophaneRequest.model_fields


def _run_template(method: str, build, spec_params: dict) -> TemplateResponse:
    """Run a template builder in a new model dir; 422 with its message on TemplateError."""
    new_model_id, new_dir = storage.new_model_dir()
    try:
        result: TemplateResult = build(new_dir)
    except TemplateError as exc:
        shutil.rmtree(new_dir, ignore_errors=True)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    storage.save_spec(new_model_id, {"method": method, "params": spec_params})
    parts = [part.model_copy(update={"url": f"/models/{new_model_id}/parts/{part.filename}"}) for part in result.parts]
    return TemplateResponse(**result.model_dump(exclude={"parts"}), parts=parts, model_id=new_model_id)


@app.post("/templates/gridfinity-bin", response_model=TemplateResponse)
def template_gridfinity_bin(request: GridfinityBinRequest) -> TemplateResponse:
    """Gridfinity bin: grid units, height units, compartments, stacking lip, label tabs, magnet holes."""
    params = request.model_dump()
    return _run_template("gridfinity_bin", lambda d: gridfinity.make_bin(d, **params), params)


@app.post("/templates/minis-bin", response_model=TemplateResponse)
def template_minis_bin(request: MinisBinRequest) -> TemplateResponse:
    """Gridfinity bin for miniatures: a socket per round base, optional magnet holes, room for the figures."""
    params = request.model_dump()
    return _run_template("minis_bin", lambda d: gridfinity.make_minis_bin(d, **params), params)


@app.post("/templates/terrain", response_model=TemplateResponse)
def template_terrain(request: TerrainRequest) -> TemplateResponse:
    """Tabletop terrain tile: hills, ridges, craters or mesas from a seed; terraces, joinable edges, magnets."""
    params = request.model_dump()
    return _run_template("terrain", lambda d: terrain.make_terrain(d, **params), params)


@app.post("/templates/gear", response_model=TemplateResponse)
def template_gear(request: GearRequest) -> TemplateResponse:
    """Involute spur/helical gear, optional mating gear (placed in mesh) and rack, with a mesh report."""
    params = request.model_dump()
    return _run_template("gear", lambda d: gear.make_gears(d, **params), params)


@app.post("/templates/qr-tag", response_model=TemplateResponse)
def template_qr_tag(request: QrTagRequest) -> TemplateResponse:
    """Raised QR code on a plate (text/URL or Wi-Fi), plus separate plate and code STLs for two colours."""
    params = request.model_dump()
    # The Wi-Fi password is printed into the code but never stored in the spec.
    spec = {k: v for k, v in params.items() if k != "password"}
    return _run_template("qr_tag", lambda d: qr_tag.make_qr_tag(d, **params), spec)


@app.post("/templates/lithophane", response_model=TemplateResponse)
async def template_lithophane(
    file: UploadFile = _UPLOAD_FILE,
    shape: str = Form(_LITHO_FIELDS["shape"].default, pattern="^(flat|curved|cylinder)$"),
    width_mm: float = Form(_LITHO_FIELDS["width_mm"].default),
    min_thickness_mm: float = Form(_LITHO_FIELDS["min_thickness_mm"].default),
    max_thickness_mm: float = Form(_LITHO_FIELDS["max_thickness_mm"].default),
    resolution_mm: float = Form(_LITHO_FIELDS["resolution_mm"].default),
    frame_mm: float = Form(_LITHO_FIELDS["frame_mm"].default),
    curve_deg: float = Form(_LITHO_FIELDS["curve_deg"].default),
) -> TemplateResponse:
    """Lithophane from an image (darker = thicker): flat, curved or cylinder, with an optional frame."""
    try:
        request = LithophaneRequest(
            shape=shape,
            width_mm=width_mm,
            min_thickness_mm=min_thickness_mm,
            max_thickness_mm=max_thickness_mm,
            resolution_mm=resolution_mm,
            frame_mm=frame_mm,
            curve_deg=curve_deg,
        )
    except ValueError as exc:  # pydantic.ValidationError; form fields are validated here, not by FastAPI
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    content = await file.read()
    if len(content) > _MAX_IMAGE_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"image exceeds {_MAX_IMAGE_UPLOAD_BYTES // (1024 * 1024)} MB")
    params = request.model_dump()
    return await run_in_threadpool(
        _run_template, "lithophane", lambda d: lithophane.make_lithophane(content, d, **params), params
    )


# --- Multi-colour 3MF export (threemf.py) ---

_3MF_NAME = "multicolour.3mf"
# Default filaments for parts that are meant to be two colours.
_DEFAULT_COLOURS = {"qr_plate.stl": ("#F2F2F2", 1), "qr_code.stl": ("#1C1C1F", 2)}


def _model_parts(model_id: str) -> list[str]:
    """The model's STL parts (the names download_part serves), or model.stl for a plain model."""
    directory = _checked_model_dir(model_id)
    names = sorted(p.name for p in directory.glob("*.stl") if _PART_RE.match(p.name))
    if {"qr_plate.stl", "qr_code.stl", "qr_tag.stl"} <= set(names):
        names.remove("qr_tag.stl")  # the combined tag duplicates the plate + code
    if not names and (directory / "model.stl").is_file():
        names = ["model.stl"]
    return names


@app.post("/models/{model_id}/export-3mf", response_model=Export3mfResponse)
def export_multicolour_3mf(model_id: str, request: Export3mfRequest) -> Export3mfResponse:
    """
    Export a model's parts as one 3MF with a filament (colour + AMS/CANVAS
    slot) per part. `slicer` picks the layout: "orca" for Bambu Studio /
    OrcaSlicer / Elegoo Slicer, "prusa" for PrusaSlicer. Without `parts`,
    every part gets its own slot.
    """
    available = _model_parts(model_id)
    if not available:
        raise HTTPException(status_code=422, detail="this model has no STL parts to export")
    if request.parts is None:
        chosen = []
        for i, name in enumerate(available):
            color, slot = _DEFAULT_COLOURS.get(name, (threemf.PALETTE[i % len(threemf.PALETTE)], i % 16 + 1))
            chosen.append({"filename": name, "color": color, "extruder": slot})
    else:
        chosen = [p.model_dump() for p in request.parts]
        unknown = [p["filename"] for p in chosen if p["filename"] not in available]
        if unknown:
            raise HTTPException(status_code=422, detail=f"not parts of this model: {unknown}; parts: {available}")
    directory = _checked_model_dir(model_id)
    parts = []
    for p in chosen:
        try:
            mesh = trimesh.load(directory / p["filename"], force="mesh")
        except Exception as exc:  # trimesh raises many exception types for bad files
            logger.error("3MF export could not read %s/%s: %s", model_id, p["filename"], exc)
            raise HTTPException(status_code=422, detail=f"could not read {p['filename']}") from exc
        parts.append({"name": Path(p["filename"]).stem, "mesh": mesh, "color": p["color"], "extruder": p["extruder"]})
    try:
        summary = threemf.write_3mf(directory / _3MF_NAME, parts, request.assembled, request.slicer)
    except TemplateError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return Export3mfResponse(model_id=model_id, download_url=f"/models/{model_id}/{_3MF_NAME}", **summary)


@app.get("/models/{model_id}/multicolour.3mf")
def download_multicolour_3mf(model_id: str) -> FileResponse:
    """Download the last multi-colour 3MF exported for this model."""
    path = _checked_model_dir(model_id) / _3MF_NAME
    if not path.is_file():
        raise HTTPException(status_code=404, detail="no 3MF exported for this model yet; POST export-3mf first")
    return FileResponse(path, media_type="model/3mf", filename=f"{model_id}_multicolour.3mf")


@app.get("/models/{model_id}/overhangs", response_model=OverhangResponse)
def model_overhangs(model_id: str, angle_deg: float = 45.0) -> OverhangResponse:
    """
    Surface that would need support if the model is printed as oriented (Z up):
    faces pointing down more steeply than angle_deg from vertical, ignoring
    faces on the bed. The tool pages draw the same faces in red.
    """
    if not 0 < angle_deg < 90:
        raise HTTPException(status_code=422, detail="angle_deg must be between 0 and 90")
    source = _mesh_source(model_id)
    try:
        mesh = trimesh.load(source, force="mesh")
    except Exception as exc:  # trimesh raises many exception types for bad files
        logger.error("overhang check could not read %s: %s", source, exc)
        raise HTTPException(status_code=422, detail=f"could not read a mesh from {source.name}") from exc
    if not isinstance(mesh, trimesh.Trimesh) or len(mesh.faces) == 0:
        raise HTTPException(status_code=422, detail=f"could not read a mesh from {source.name}")
    return OverhangResponse(**geometry.overhang_report(mesh, angle_deg))


# Mounted last and at "/" so it only serves requests none of the API routes
# above matched; StaticFiles(html=True) serves static/index.html at "/" -
# the browser UI for picking a file to load/thicken/download (see that
# file's header comment) or generating a model from a prompt.
app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")
