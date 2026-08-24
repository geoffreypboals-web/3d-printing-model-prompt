"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/src/threedprompt/main.py
Description: FastAPI application wiring together the classifier, the two
    generation backends (OpenSCAD for simple parts, Blender for complex
    ones), and the wall-thickness endpoints. This is the only HTTP-facing
    module - it does no CAD/LLM work itself, only routing, validation,
    and translating domain errors into HTTP responses.
Inputs: HTTP requests (see models.py for request/response schemas).
Outputs: HTTP responses; STL files written under settings.output_dir as
    a side effect.
Troubleshooting:
    - 503 from any endpoint means an external tool (openscad, blender, or
      the LLM) is unavailable or misconfigured - check GET /health first,
      it reports each dependency's status individually.
    - 413 on /thicken means the uploaded file exceeded MAX_UPLOAD_BYTES;
      raise that env var if you intentionally need larger uploads.
    - Run locally with: uvicorn threedprompt.main:app --reload
      (see README.md at /home/user/3d-printing-model-prompt/README.md
      for the full command including PYTHONPATH setup).
"""

from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from threedprompt import blender_generator, openscad_generator, storage, thickness
from threedprompt.blender_generator import BlenderGenerationError
from threedprompt.classifier import classify_prompt
from threedprompt.config import settings
from threedprompt.llm_client import LLMError, get_llm_client
from threedprompt.logging_config import configure_logging, get_logger
from threedprompt.models import (
    Backend,
    Complexity,
    GenerateRequest,
    GenerateResponse,
    HealthResponse,
    ThickenRequest,
    ThickenResponse,
)
from threedprompt.openscad_generator import OpenScadGenerationError
from threedprompt.thickness import ThicknessError

configure_logging()
logger = get_logger(__name__)

app = FastAPI(title="3D Printing Model Prompt", version="0.1.0")

_ALLOWED_UPLOAD_SUFFIXES = {".stl", ".obj"}


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
