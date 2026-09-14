"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/src/threedprompt/freecad_cad.py
Description: Public entry point for the three FreeCAD-backed features -
    wall-thickness shelling via Part Thickness (tried first for .stl
    input by thickness.py's mesh_shell(), falling back to Blender's
    Solidify when FreeCAD is unavailable or the input isn't solid enough
    - Blender stays the path for every other mesh format and for
      organic/mesh-only models generally), STEP import/export (mesh <->
    STEP solid, a capability Blender doesn't have at all), and solid
    healing via OCCT's ShapeFix (repairs malformed B-rep topology - a
    different class of defect than watertight.py's Blender-based
    open-boundary hole filling). See
    docs/adr/0005-freecad-third-cad-backend.md. Wraps freecad_scripts/*.py
    as ordinary Python functions by invoking FreeCADCmd headless as a
    subprocess and parsing the JSON it writes back, the same shape
    watertight.py uses to wrap blender_scripts/ - main.py and
    thickness.py are this module's only callers.
Inputs: Mesh/STEP file paths and settings.freecad_binary /
    settings.cad_subprocess_timeout_seconds from config.py.
Outputs: Converted/thickened/healed files on disk, plus small report
    dicts (see each public function's docstring for keys).
Troubleshooting:
    - FreeCADCADError "FreeCAD executable not found": install the
      freecad-python3 apt package (the Dockerfile does this) or set
      FREECAD_BINARY to its full path - same shape as thickness.py's
      Blender equivalent. Its apt package installs the headless CLI at
      /usr/bin/freecadcmd, not /usr/bin/freecad.
    - FreeCADCADError wrapping a freecad_scripts/ script's own
      {"error": ...}: the underlying cause is in that message already
      (this module does not swallow or rephrase it).
    - FreeCADCmd's exit code is always 0 regardless of what the script
      actually did (confirmed live against FreeCAD 1.0.0) and it prints
      an unrelated, harmless traceback to stderr about failing to
      auto-open one of the argv values as a document - success is
      judged purely by whether the script's --report JSON exists and
      parses cleanly, matching watertight.py's own
      Blender-return-code-ignoring _read_json_report() pattern.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from threedprompt.config import settings
from threedprompt.logging_config import get_logger

logger = get_logger(__name__)

_HERE = Path(__file__).resolve().parent
_FREECAD_SCRIPTS_DIR = _HERE / "freecad_scripts"
_THICKEN_SCRIPT = _FREECAD_SCRIPTS_DIR / "thicken.py"
_STEP_EXPORT_SCRIPT = _FREECAD_SCRIPTS_DIR / "step_export.py"
_STEP_IMPORT_SCRIPT = _FREECAD_SCRIPTS_DIR / "step_import.py"
_HEAL_SCRIPT = _FREECAD_SCRIPTS_DIR / "heal.py"

# FreeCAD's Mesh module reads more formats than this, but thickness.py's
# FreeCAD-first path is only tried for .stl today - everything else keeps
# going straight to Blender, which already handles every format this
# service accepts uploads in.
MESH_INPUT_EXTENSIONS = {".stl"}


class FreeCADCADError(RuntimeError):
    """Raised when FreeCAD is missing, times out, or fails to thicken/convert/heal a model."""


def _freecad_binary_path() -> str:
    """Resolve the configured FreeCAD binary, raising a clear error if it isn't on PATH (matches watertight.py)."""
    resolved = shutil.which(settings.freecad_binary) or (
        settings.freecad_binary if Path(settings.freecad_binary).is_file() else None
    )
    if not resolved:
        raise FreeCADCADError(
            f"FreeCAD binary '{settings.freecad_binary}' not found on PATH. "
            "Install freecad-python3 or set FREECAD_BINARY to its full path."
        )
    return resolved


def _run_freecad_script(script: Path, args: list[str]) -> subprocess.CompletedProcess:
    """Run one FreeCADCmd script, returning the completed process.

    Never raises on nonzero exit - see module docstring for why.
    """
    binary = _freecad_binary_path()
    cmd = [binary, str(script), "--", *args]
    logger.info("Running FreeCAD script: %s", " ".join(cmd))
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=settings.cad_subprocess_timeout_seconds, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise FreeCADCADError(
            f"FreeCAD timed out after {settings.cad_subprocess_timeout_seconds}s running {script.name}"
        ) from exc
    if proc.returncode != 0:
        logger.warning("FreeCAD exited %s running %s; stderr:\n%s", proc.returncode, script.name, proc.stderr)
    return proc


def _read_json_report(path: Path, proc: subprocess.CompletedProcess) -> dict:
    """Load the JSON a FreeCAD script wrote, raising FreeCADCADError for a missing file or a reported error."""
    if not path.exists():
        raise FreeCADCADError(
            f"FreeCAD produced no report (exit code {proc.returncode}). stderr:\n{proc.stderr.strip()}"
        )
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not data.get("ok", False):
        raise FreeCADCADError(f"FreeCAD script reported an error: {data.get('error', 'unknown error')}")
    return data


def thicken_mesh(input_path: Path, amount_mm: float, output_path: Path) -> dict:
    """
    Shell a mesh by amount_mm via FreeCAD's Part Thickness, picking the
    solid's largest planar face as the required opening. Raises
    FreeCADCADError on missing binary, a non-solid/non-manifold input,
    or a timeout - callers wanting a fallback (see thickness.mesh_shell)
    should catch that and try Blender's Solidify instead.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    report_path = output_path.parent / f"{output_path.stem}_thicken_report.json"
    proc = _run_freecad_script(
        _THICKEN_SCRIPT,
        [
            "--input",
            str(input_path),
            "--output",
            str(output_path),
            "--amount-mm",
            str(amount_mm),
            "--report",
            str(report_path),
        ],
    )
    result = _read_json_report(report_path, proc)
    logger.info("FreeCAD-thickened %s by %.3fmm -> %s.", input_path, amount_mm, output_path)
    return result


def mesh_to_step(input_path: Path, output_path: Path) -> dict:
    """
    Convert a mesh file into a STEP solid (best-effort B-rep wrap of the
    mesh's triangles, not true reverse-engineered parametric CAD - a
    flat face becomes one real STEP face, but there's no curve/fillet
    recovery). Raises FreeCADCADError on missing binary or a
    non-solid/non-manifold input.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    report_path = output_path.parent / f"{output_path.stem}_step_export_report.json"
    proc = _run_freecad_script(
        _STEP_EXPORT_SCRIPT,
        ["--input", str(input_path), "--output", str(output_path), "--report", str(report_path)],
    )
    result = _read_json_report(report_path, proc)
    logger.info("Exported %s -> STEP %s.", input_path, output_path)
    return result


def step_to_mesh(input_path: Path, output_path: Path) -> dict:
    """
    Convert a STEP file into a tessellated mesh file, for
    previewing/printing a STEP solid the same way as any other model.
    Raises FreeCADCADError on missing binary or an unreadable STEP file.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    report_path = output_path.parent / f"{output_path.stem}_step_import_report.json"
    proc = _run_freecad_script(
        _STEP_IMPORT_SCRIPT,
        ["--input", str(input_path), "--output", str(output_path), "--report", str(report_path)],
    )
    result = _read_json_report(report_path, proc)
    logger.info("Imported STEP %s -> mesh %s.", input_path, output_path)
    return result


def heal_solid(input_path: Path, output_path: Path, *, tolerance: float = 0.01) -> dict:
    """
    Repair malformed B-rep topology (small gaps, invalid edges/faces) via
    OCCT's ShapeFix, on either a mesh or STEP input. Always writes a
    tessellated mesh to output_path regardless of input format. This is
    a different class of repair than watertight.py's Blender-based hole
    filling (which closes genuine open boundary loops in a mesh) - use
    this for a solid that's already "closed-looking" but geometrically
    malformed (e.g. after a mesh-to-solid conversion or a messy STEP
    import), and watertight.py for a mesh with an actual missing patch.
    Raises FreeCADCADError on missing binary or an unreadable input.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    report_path = output_path.parent / f"{output_path.stem}_heal_report.json"
    proc = _run_freecad_script(
        _HEAL_SCRIPT,
        [
            "--input",
            str(input_path),
            "--output",
            str(output_path),
            "--report",
            str(report_path),
            "--tolerance",
            str(tolerance),
        ],
    )
    result = _read_json_report(report_path, proc)
    logger.info("Healed %s -> %s (valid_after=%s).", input_path, output_path, result.get("valid_after"))
    return result
