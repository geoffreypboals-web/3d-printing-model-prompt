"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/src/threedprompt/draft_analysis.py
Description: Public entry point for FR-4's draft-angle/undercut analysis
    - reports which faces of a model would prevent a *rigid* two-part
    mold from releasing cleanly along a given pull axis, without
    modifying the mesh. Wraps blender_scripts/analyze_draft.py as an
    ordinary Python function by invoking Blender headless as a subprocess
    and parsing the JSON it writes back, the same pattern watertight.py
    already establishes for its own analysis script. Purely informational
    for the existing silicone_block mode (flexible material forgives most
    of this); mold.py calls this automatically as a warning for
    direct_cast, which can't. See docs/adr/0007-draft-undercut-analysis.md.
Inputs: A model mesh file path (anything blender_scripts/_shared.import_mesh
    supports), a pull_axis ("x"/"y"/"z", default "z" - matching
    make_mold.py's own bounding-box-midpoint parting-plane convention),
    and a min_draft_angle_deg threshold (default 2.0, per the Creality
    guide's 2-4 degree recommendation cited in
    docs/mold-production-research-and-plan.md).
Outputs: A DraftReport dataclass (see models.py).
Troubleshooting:
    - DraftAnalysisError "Blender executable not found": install Blender
      or set BLENDER_BINARY to its full path - same as thickness.py's/
      watertight.py's equivalent error.
    - A face you'd expect flagged isn't (or vice versa): the parting
      plane is always the model's own bounding-box midpoint along
      pull_axis, not necessarily its true widest cross-section - see
      analyze_draft.py's own troubleshooting note.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from threedprompt.config import settings
from threedprompt.logging_config import get_logger
from threedprompt.models import DraftReport, ProblemFaceIsland

logger = get_logger(__name__)

_BLENDER_SCRIPTS_DIR = Path(__file__).resolve().parent / "blender_scripts"
_ANALYZE_DRAFT_SCRIPT = _BLENDER_SCRIPTS_DIR / "analyze_draft.py"

_PULL_AXES = ("x", "y", "z")


class DraftAnalysisError(RuntimeError):
    """Raised when draft/undercut analysis can't complete (bad params, missing Blender, or a Blender failure)."""


def _blender_binary_path() -> str:
    """Resolve the configured blender binary, raising a clear error if it isn't on PATH (matches mold.py)."""
    resolved = shutil.which(settings.blender_binary) or (
        settings.blender_binary if Path(settings.blender_binary).is_file() else None
    )
    if not resolved:
        raise DraftAnalysisError(
            f"blender binary '{settings.blender_binary}' not found on PATH. "
            "Install Blender or set BLENDER_BINARY to its full path."
        )
    return resolved


def analyze_draft(input_path: str, *, pull_axis: str = "z", min_draft_angle_deg: float = 2.0) -> DraftReport:
    """
    Run the draft-angle/undercut heuristic against input_path and return
    a DraftReport. Does not modify input_path.

    Raises DraftAnalysisError if pull_axis isn't x/y/z, min_draft_angle_deg
    is negative, Blender is missing, or the Blender script fails (its own
    error message is preserved).
    """
    if pull_axis not in _PULL_AXES:
        raise DraftAnalysisError(f"pull_axis must be one of {_PULL_AXES} (got {pull_axis!r})")
    if min_draft_angle_deg < 0:
        raise DraftAnalysisError(f"min_draft_angle_deg must be >= 0 (got {min_draft_angle_deg})")

    binary = _blender_binary_path()

    with tempfile.TemporaryDirectory() as tmp:
        output_json = Path(tmp) / "report.json"
        cmd = [
            binary,
            "--background",
            "--python",
            str(_ANALYZE_DRAFT_SCRIPT),
            "--",
            "--input",
            str(input_path),
            "--output",
            str(output_json),
            "--pull-axis",
            pull_axis,
            "--min-draft-angle-deg",
            str(min_draft_angle_deg),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=settings.cad_subprocess_timeout_seconds)
        if not output_json.exists():
            tail = "\n".join(proc.stdout.splitlines()[-40:] + proc.stderr.splitlines()[-40:])
            raise DraftAnalysisError(f"blender draft analysis produced no report (exit {proc.returncode}): {tail}")
        report_dict = json.loads(output_json.read_text())

    if "error" in report_dict:
        raise DraftAnalysisError(f"draft analysis failed: {report_dict['error']}")

    islands = [ProblemFaceIsland(**island) for island in report_dict["problem_islands"]]
    report = DraftReport(
        source_path=report_dict["source_path"],
        pull_axis=report_dict["pull_axis"],
        parting_coordinate=report_dict["parting_coordinate"],
        min_draft_angle_deg=report_dict["min_draft_angle_deg"],
        releasable=report_dict["releasable"],
        problem_islands=islands,
        vertex_count=report_dict["vertex_count"],
        face_count=report_dict["face_count"],
        blender_version=report_dict["blender_version"],
    )
    logger.info(
        "Draft-checked %s (axis=%s, threshold=%.1fdeg): releasable=%s problem_islands=%d",
        input_path,
        pull_axis,
        min_draft_angle_deg,
        report.releasable,
        len(islands),
    )
    return report
