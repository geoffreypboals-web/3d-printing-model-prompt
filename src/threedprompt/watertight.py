"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/src/threedprompt/watertight.py
Description: Public entry point for the watertight analysis/repair
    feature - finds and closes gaps a model's surface shouldn't have,
    distinguishing them from openings the model was designed with (a
    cup's mouth, an open box top). Wraps the Blender-side scripts
    (blender_scripts/analyze_watertight.py, blender_scripts/close_holes.py)
    as ordinary Python functions by invoking Blender headless as a
    subprocess and parsing the JSON it writes back - main.py and any
    future caller should go through this module rather than shelling out
    to Blender directly, the same way thickness.py is the only caller of
    Blender for wall-thickness work.
Inputs: Mesh file paths (.stl/.obj/.ply/.glb/.gltf/.fbx), hole ids from a
    prior analyze_mesh() report, and settings.blender_binary /
    settings.cad_subprocess_timeout_seconds from config.py.
Outputs: WatertightReport / RepairResult dataclasses (see models.py).
Troubleshooting:
    - WatertightError "Blender executable not found": install Blender
      (the Dockerfile installs it via apt) or set BLENDER_BINARY to its
      full path - same as thickness.py's equivalent error.
    - WatertightError wrapping a Blender script's own {"error": ...}: the
      underlying cause is in that message already (this module does not
      swallow or rephrase it) - check the referenced hole ids/file path
      first, since those are the most common causes (stale ids from a
      report generated against a different copy of the file - hole ids
      are positional and renumber whenever the file changes).
    - Blender's apt package on Debian/Ubuntu links the *system* Python
      rather than bundling its own, so its glTF export addon (used for
      viewer_output below) fails with "No module named 'numpy'" unless
      python3-numpy is installed alongside it - already in the
      Dockerfile; see TROUBLESHOOTING.md if running Blender outside
      Docker.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from .config import settings
from .hole_classifier import classify_report
from .logging_config import get_logger
from .models import (
    BoundingBox,
    FlippedNormalIsland,
    Hole,
    HoleClassification,
    RepairResult,
    WatertightReport,
)

logger = get_logger(__name__)

_HERE = Path(__file__).resolve().parent
_BLENDER_SCRIPTS_DIR = _HERE / "blender_scripts"
_ANALYZE_SCRIPT = _BLENDER_SCRIPTS_DIR / "analyze_watertight.py"
_CLOSE_HOLES_SCRIPT = _BLENDER_SCRIPTS_DIR / "close_holes.py"

SUPPORTED_EXTENSIONS = {".stl", ".obj", ".ply", ".glb", ".gltf", ".fbx"}


class WatertightError(RuntimeError):
    """Raised when Blender is missing, times out, or fails to analyze/repair a mesh."""


def _blender_binary_path() -> str:
    """Resolve the configured blender binary, raising a clear error if it isn't on PATH (matches thickness.py)."""
    resolved = shutil.which(settings.blender_binary) or (
        settings.blender_binary if Path(settings.blender_binary).is_file() else None
    )
    if not resolved:
        raise WatertightError(
            f"blender binary '{settings.blender_binary}' not found on PATH. "
            "Install Blender or set BLENDER_BINARY to its full path."
        )
    return resolved


def _validate_input(path: str) -> None:
    """Raise WatertightError if path doesn't exist or isn't a supported mesh format."""
    p = Path(path)
    if not p.is_file():
        raise WatertightError(f"Input mesh file not found: {path}")
    if p.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise WatertightError(
            f"Unsupported mesh format {p.suffix!r} for {path}; supported: {sorted(SUPPORTED_EXTENSIONS)}"
        )


def _run_blender_script(script: Path, args: list[str]) -> subprocess.CompletedProcess:
    """Run one Blender background script and return the completed process (never raises on nonzero exit)."""
    binary = _blender_binary_path()
    cmd = [binary, "--background", "--python", str(script), "--", *args]
    logger.info("Running Blender script: %s", " ".join(cmd))
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=settings.cad_subprocess_timeout_seconds, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise WatertightError(
            f"Blender timed out after {settings.cad_subprocess_timeout_seconds}s running {script.name}"
        ) from exc
    if proc.returncode != 0:
        logger.warning("Blender exited %s running %s; stderr:\n%s", proc.returncode, script.name, proc.stderr)
    return proc


def _read_json_report(path: Path, proc: subprocess.CompletedProcess) -> dict:
    """Load the JSON a Blender script wrote, raising WatertightError for a missing file or a reported error."""
    if not path.exists():
        raise WatertightError(
            f"Blender produced no output (exit code {proc.returncode}). stderr:\n{proc.stderr.strip()}"
        )
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if "error" in data:
        raise WatertightError(f"Blender script reported an error: {data['error']}")
    return data


def _report_from_dict(d: dict) -> WatertightReport:
    """Parse one Blender script's JSON report dict into a WatertightReport."""
    bbox = BoundingBox(min=tuple(d["bounding_box"]["min"]), max=tuple(d["bounding_box"]["max"]))
    holes = [
        Hole(
            id=h["id"],
            vertex_indices=h["vertex_indices"],
            centroid=tuple(h["centroid"]),
            area=h["area"],
            perimeter=h["perimeter"],
            planarity=h["planarity"],
            classification=HoleClassification(h.get("classification", "ambiguous")),
            confidence=h.get("confidence", 0.0),
            reason=h.get("reason", ""),
        )
        for h in d["holes"]
    ]
    islands = [
        FlippedNormalIsland(
            id=isl["id"],
            face_indices=isl["face_indices"],
            centroid=tuple(isl["centroid"]),
            face_count=isl["face_count"],
        )
        for isl in d.get("flipped_normal_islands", [])
    ]
    return WatertightReport(
        source_path=d["source_path"],
        is_watertight=d["is_watertight"],
        vertex_count=d["vertex_count"],
        face_count=d["face_count"],
        total_surface_area=d["total_surface_area"],
        bounding_box=bbox,
        holes=holes,
        flipped_normal_islands=islands,
        nonmanifold_junction_edge_count=d.get("nonmanifold_junction_edge_count", 0),
        blender_version=d.get("blender_version", ""),
    )


def analyze_mesh(input_path: str, *, viewer_output: str | None = None) -> WatertightReport:
    """
    Analyze a mesh file for watertightness. Runs Blender headless to find
    boundary-edge holes and inverted-normal islands, then classifies each
    hole (intentional opening vs. likely defect) via hole_classifier.py.

    If viewer_output is given (a .glb path), also writes a web-viewable
    copy of the mesh there in the same Blender invocation, for the
    three.js viewer - the returned report's viewer_path is set to it.

    Raises WatertightError if Blender is missing, times out, or the file
    is unreadable/unsupported.
    """
    _validate_input(input_path)
    if viewer_output:
        Path(viewer_output).parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        output_json = Path(tmp) / "report.json"
        script_args = ["--input", str(input_path), "--output", str(output_json)]
        if viewer_output:
            script_args += ["--viewer-output", str(viewer_output)]
        proc = _run_blender_script(_ANALYZE_SCRIPT, script_args)
        report_dict = _read_json_report(output_json, proc)

    report = classify_report(_report_from_dict(report_dict))
    if viewer_output:
        report.viewer_path = str(viewer_output)
    logger.info(
        "Analyzed %s: watertight=%s holes=%d flipped_islands=%d",
        input_path,
        report.is_watertight,
        len(report.holes),
        len(report.flipped_normal_islands),
    )
    return report


def repair_mesh(
    input_path: str, hole_ids: list[int], output_path: str, *, viewer_output: str | None = None
) -> RepairResult:
    """
    Close the given hole ids (as reported by a prior analyze_mesh() call
    on this exact file) and write the repaired mesh to output_path.

    If viewer_output is given (a .glb path), also writes an updated
    web-viewable copy of the repaired mesh there, in the same Blender
    invocation.

    Raises WatertightError if Blender is missing, times out, a hole id
    doesn't exist on this file, or the output format is unsupported.
    """
    _validate_input(input_path)
    if not hole_ids:
        raise WatertightError("repair_mesh() called with no hole_ids to close")
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    if viewer_output:
        Path(viewer_output).parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        report_json = Path(tmp) / "report.json"
        script_args = [
            "--input",
            str(input_path),
            "--hole-ids",
            ",".join(str(i) for i in hole_ids),
            "--output",
            str(output_path),
            "--report-output",
            str(report_json),
        ]
        if viewer_output:
            script_args += ["--viewer-output", str(viewer_output)]
        proc = _run_blender_script(_CLOSE_HOLES_SCRIPT, script_args)
        report_dict = _read_json_report(report_json, proc)

    report = _report_from_dict(report_dict)
    if viewer_output:
        report.viewer_path = str(viewer_output)
    logger.info(
        "Repaired %s -> %s: closed=%s watertight=%s remaining_holes=%d",
        input_path,
        output_path,
        sorted(hole_ids),
        report.is_watertight,
        len(report.holes),
    )
    return RepairResult(
        output_path=str(output_path),
        closed_hole_ids=sorted(hole_ids),
        is_watertight=report.is_watertight,
        remaining_holes=report.holes,
        viewer_path=report.viewer_path,
    )
