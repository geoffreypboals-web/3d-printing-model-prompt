"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/src/threedprompt/main.py
Description: FastAPI application wiring together the classifier, the two
    generation backends (OpenSCAD for simple parts, Blender for complex
    ones), the wall-thickness endpoints, and the watertight analysis/
    repair endpoints. This is the only HTTP-facing module - it does no
    CAD/LLM work itself, only routing, validation, and translating domain
    errors into HTTP responses. Also serves the browser UI
    (static/index.html) at "/" for picking a local file, modifying it,
    and downloading the result without needing curl/Swagger.
Inputs: HTTP requests (see models.py for request/response schemas).
Outputs: HTTP responses; STL files written under settings.output_dir as
    a side effect.
Troubleshooting:
    - 503 from any endpoint means an external tool (openscad, blender, or
      the LLM) is unavailable or misconfigured - check GET /health first,
      it reports each dependency's status individually.
    - 413 on /thicken means the uploaded file exceeded MAX_UPLOAD_BYTES;
      raise that env var if you intentionally need larger uploads.
    - "/" 404s or shows raw JSON instead of the UI: the StaticFiles mount
      must be the LAST route registered (it's a catch-all at "/") - if a
      new API route is added below it by mistake, it'll never be reached.
    - 400 "hole id(s) ... not found" from /repair: hole ids are
      positional and only valid for the exact file state a prior
      /analyze reported on - re-run /analyze if the model changed
      (closing holes itself renumbers whatever's left).
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

from threedprompt import blender_generator, openscad_generator, storage, thickness, watertight
from threedprompt.blender_generator import BlenderGenerationError
from threedprompt.classifier import classify_prompt
from threedprompt.config import settings
from threedprompt.llm_client import LLMError, get_llm_client
from threedprompt.logging_config import configure_logging, get_logger
from threedprompt.models import (
    AnalyzeResponse,
    Backend,
    Complexity,
    FlippedNormalIsland,
    GenerateRequest,
    GenerateResponse,
    HealthResponse,
    Hole,
    RepairRequest,
    RepairResponse,
    ThickenRequest,
    ThickenResponse,
    UploadResponse,
)
from threedprompt.openscad_generator import OpenScadGenerationError
from threedprompt.thickness import ThicknessError
from threedprompt.watertight import WatertightError

configure_logging()
logger = get_logger(__name__)

app = FastAPI(title="3D Printing Model Prompt", version="0.1.0")

_ALLOWED_UPLOAD_SUFFIXES = {".stl", ".obj"}
_VIEWER_GLB_FILENAME = "viewer.glb"


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Report availability of each external dependency (OpenSCAD, Blender, the LLM backend)."""
    openscad_available = shutil.which(settings.openscad_binary) is not None
    blender_available = shutil.which(settings.blender_binary) is not None
    llm_reachable = False
    try:
        llm_reachable = get_llm_client().is_reachable()
    except LLMError as exc:
        logger.warning("LLM client unavailable during health check: %s", exc)

    status = "ok" if (openscad_available and blender_available and llm_reachable) else "degraded"
    return HealthResponse(
        status=status,
        openscad_available=openscad_available,
        blender_available=blender_available,
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
        thickness.mesh_shell(stl_source, request.amount_mm, new_dir)
        storage.save_spec(new_model_id, {"parent_model_id": model_id, "method": "mesh_shell"})
        return ThickenResponse(
            model_id=new_model_id, method="mesh_shell", download_url=f"/models/{new_model_id}/download"
        )
    except (FileNotFoundError, ThicknessError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


_UPLOAD_FILE = File(...)
_AMOUNT_MM_FORM = Form(...)


@app.post("/thicken")
async def thicken_uploaded_file(file: UploadFile = _UPLOAD_FILE, amount_mm: float = _AMOUNT_MM_FORM) -> FileResponse:
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
        raise HTTPException(status_code=422, detail=f"unsupported file type '{suffix}'; expected .stl or .obj")

    content = await file.read()
    if len(content) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail=f"upload exceeds MAX_UPLOAD_BYTES ({settings.max_upload_bytes})")

    upload_model_id, upload_path = storage.save_upload(file.filename or "model.stl", content)
    try:
        new_model_id, new_dir = storage.new_model_dir()
        result_path = thickness.mesh_shell(upload_path, amount_mm, new_dir)
        storage.save_spec(new_model_id, {"parent_model_id": upload_model_id, "method": "mesh_shell"})
        return FileResponse(
            result_path,
            media_type="model/stl",
            filename=f"{new_model_id}.stl",
            headers={"X-Model-Id": new_model_id, "X-Thicken-Method": "mesh_shell"},
        )
    except ThicknessError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


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
    """
    try:
        source_path = storage.stl_path(model_id) if _has_stl(model_id) else _any_model_file(model_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    viewer_path = storage.model_dir(model_id) / _VIEWER_GLB_FILENAME
    try:
        result = watertight.repair_mesh(
            str(source_path),
            request.hole_ids,
            str(storage.model_dir(model_id) / "model.stl"),
            viewer_output=str(viewer_path),
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
