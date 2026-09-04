"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/src/mesh_repair/api.py
Description: Local HTTP API + static file server that backs the three.js
    viewer (../../viewer/index.html) for the "find and fix watertightness
    problems" workflow: upload a mesh, analyze it, see flagged holes
    color-coded by whether they look like a deliberate opening or a
    defect, pick which ones to close, and download the repaired file.
    Thin by design — all real work happens in service.py; this module
    only handles HTTP plumbing, job bookkeeping, and JSON shaping.
Inputs: HTTP requests (see the route docstrings below). Config via env
    vars: MESH_REPAIR_DATA_DIR (where uploaded/generated files live,
    default "data/uploads") and BLENDER_BINARY (passed through to
    service.py).
Outputs: JSON responses; mesh/GLB files served from MESH_REPAIR_DATA_DIR.
Troubleshooting:
    - 404 on GET /api/models/{job_id}/...: job ids are in-memory only
      (see _JOBS below) — they don't survive an API process restart even
      though the underlying files on disk do. Re-upload to get a fresh
      job id if the server restarted.
    - 400 "hole id ... not part of the latest analysis": the client
      tried to close a hole id from a stale report (e.g. after the mesh
      was already repaired once, which renumbers remaining holes) —
      re-fetch /analyze before repairing again.
    - Run with: `uvicorn mesh_repair.api:app --host 0.0.0.0 --port 8000`
      from the project's src/ directory (or see the Dockerfile).
"""

from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .models import FlippedNormalIsland, Hole, RepairResult, WatertightReport
from .service import SUPPORTED_EXTENSIONS, MeshRepairError, analyze_mesh, repair_mesh

logger = logging.getLogger(__name__)

DATA_DIR = Path(os.environ.get("MESH_REPAIR_DATA_DIR", "data/uploads")).resolve()
VIEWER_HTML_PATH = Path(__file__).resolve().parents[2] / "viewer" / "index.html"

app = FastAPI(
    title="3D Printing Model Prompt — Mesh Watertight Repair",
    description="Analyze a 3D-printable mesh for holes/inverted-normal defects and repair them.",
)

_VENDOR_DIR = VIEWER_HTML_PATH.parent / "vendor"
if _VENDOR_DIR.is_dir():
    # Serves the locally-vendored three.js build the viewer imports (see
    # viewer/vendor/README.md) — deliberately not a CDN <script> tag, so
    # the viewer keeps working in an offline/air-gapped Docker deployment.
    app.mount("/vendor", StaticFiles(directory=_VENDOR_DIR), name="vendor")


class _Job:
    """In-memory bookkeeping for one uploaded mesh's current state."""

    def __init__(self, job_id: str, original_path: Path, ext: str):
        """Track a new job's id, uploaded file extension, and current (original or repaired) mesh path."""
        self.job_id = job_id
        self.original_filename_ext = ext
        self.current_path = original_path
        self.latest_report: WatertightReport | None = None


_JOBS: dict[str, _Job] = {}


class RepairRequest(BaseModel):
    """Body of POST /api/models/{job_id}/repair."""

    hole_ids: list[int]


def _hole_to_json(h: Hole) -> dict:
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


def _island_to_json(i: FlippedNormalIsland) -> dict:
    """Convert a FlippedNormalIsland dataclass into a JSON-safe dict for an HTTP response."""
    return {
        "id": i.id,
        "face_indices": i.face_indices,
        "centroid": list(i.centroid),
        "face_count": i.face_count,
    }


def _report_to_json(job_id: str, r: WatertightReport) -> dict:
    """Convert a WatertightReport dataclass into the JSON body /analyze returns."""
    return {
        "job_id": job_id,
        "is_watertight": r.is_watertight,
        "vertex_count": r.vertex_count,
        "face_count": r.face_count,
        "total_surface_area": r.total_surface_area,
        "bounding_box": {
            "min": list(r.bounding_box.min),
            "max": list(r.bounding_box.max),
        },
        "holes": [_hole_to_json(h) for h in r.holes],
        "flipped_normal_islands": [_island_to_json(i) for i in r.flipped_normal_islands],
        "nonmanifold_junction_edge_count": r.nonmanifold_junction_edge_count,
        "blender_version": r.blender_version,
        "viewer_glb_url": f"/api/models/{job_id}/viewer.glb" if r.viewer_path else None,
    }


def _repair_result_to_json(job_id: str, r: RepairResult) -> dict:
    """Convert a RepairResult dataclass into the JSON body /repair returns."""
    return {
        "job_id": job_id,
        "closed_hole_ids": r.closed_hole_ids,
        "is_watertight": r.is_watertight,
        "remaining_holes": [_hole_to_json(h) for h in r.remaining_holes],
        "viewer_glb_url": f"/api/models/{job_id}/viewer.glb" if r.viewer_path else None,
        "download_url": f"/api/models/{job_id}/download",
    }


def _get_job(job_id: str) -> _Job:
    """Look up a job by id, raising a 404 HTTPException if it isn't known (e.g. after a server restart)."""
    job = _JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Unknown job id: {job_id}")
    return job


def _viewer_glb_path(job_id: str) -> Path:
    """Return the on-disk path where a job's web-viewable GLB preview is stored."""
    return DATA_DIR / job_id / "viewer.glb"


@app.get("/health")
def health() -> dict:
    """Basic liveness/readiness check (rule 21)."""
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    """Serve the three.js viewer page."""
    if not VIEWER_HTML_PATH.is_file():
        raise HTTPException(status_code=500, detail="viewer/index.html is missing")
    return VIEWER_HTML_PATH.read_text(encoding="utf-8")


@app.post("/api/models")
async def upload_model(file: UploadFile) -> dict:
    """Upload a mesh file (.stl/.obj/.ply/.glb/.gltf/.fbx) and get back a job_id."""
    ext = Path(file.filename or "").suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type {ext!r}; supported: {sorted(SUPPORTED_EXTENSIONS)}",
        )

    job_id = uuid.uuid4().hex
    job_dir = DATA_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    original_path = job_dir / f"original{ext}"

    contents = await file.read()
    original_path.write_bytes(contents)

    _JOBS[job_id] = _Job(job_id, original_path, ext)
    logger.info(
        "Uploaded model job=%s filename=%s size=%d",
        job_id,
        file.filename,
        len(contents),
    )
    return {"job_id": job_id, "filename": file.filename}


@app.post("/api/models/{job_id}/analyze")
def analyze(job_id: str) -> dict:
    """Analyze the job's current mesh and return a classified watertight report."""
    job = _get_job(job_id)
    try:
        report = analyze_mesh(str(job.current_path), viewer_output=str(_viewer_glb_path(job_id)))
    except MeshRepairError as exc:
        logger.error("Analyze failed job=%s: %s", job_id, exc)
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    job.latest_report = report
    return _report_to_json(job_id, report)


@app.post("/api/models/{job_id}/repair")
def repair(job_id: str, body: RepairRequest) -> dict:
    """Close the given hole ids (from the job's latest /analyze report) and re-check watertightness."""
    job = _get_job(job_id)
    if job.latest_report is None:
        raise HTTPException(status_code=400, detail="Call /analyze before /repair")

    valid_ids = {h.id for h in job.latest_report.holes}
    invalid = [i for i in body.hole_ids if i not in valid_ids]
    if invalid:
        raise HTTPException(
            status_code=400,
            detail=f"hole id(s) {invalid} not part of the latest analysis for this job "
            "(re-run /analyze if the mesh changed)",
        )
    if not body.hole_ids:
        raise HTTPException(status_code=400, detail="hole_ids must be a non-empty list")

    repaired_path = DATA_DIR / job_id / f"repaired{job.original_filename_ext}"
    try:
        result = repair_mesh(
            str(job.current_path),
            body.hole_ids,
            str(repaired_path),
            viewer_output=str(_viewer_glb_path(job_id)),
        )
    except MeshRepairError as exc:
        logger.error("Repair failed job=%s: %s", job_id, exc)
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    job.current_path = repaired_path
    # Re-analyze so job.latest_report's hole ids match the *new* current
    # mesh (closing holes renumbers the remaining ones) — repair_mesh's
    # own re-check already proved this file's state, so this just keeps
    # the job's cached report consistent for a following /repair call.
    job.latest_report = analyze_mesh(str(job.current_path))

    logger.info(
        "Repaired job=%s closed=%s watertight=%s",
        job_id,
        result.closed_hole_ids,
        result.is_watertight,
    )
    return _repair_result_to_json(job_id, result)


@app.get("/api/models/{job_id}/viewer.glb")
def viewer_glb(job_id: str) -> FileResponse:
    """Serve the web-viewable GLB copy of the job's current mesh (produced by /analyze)."""
    _get_job(job_id)
    path = _viewer_glb_path(job_id)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="No viewer preview yet — call /analyze first")
    return FileResponse(path, media_type="model/gltf-binary")


@app.get("/api/models/{job_id}/download")
def download(job_id: str) -> FileResponse:
    """Download the job's current mesh file (repaired copy if /repair has run, else the original)."""
    job = _get_job(job_id)
    return FileResponse(job.current_path, filename=job.current_path.name)
