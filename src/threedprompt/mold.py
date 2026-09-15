"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/src/threedprompt/mold.py
Description: Generates rigid 3D-printable mold tooling for an existing
    model via headless Blender (primitive booleans, no bisecting) - no
    LLM call, no dependency on how the model was originally generated,
    matching thickness.py's mesh_shell() and watertight.py's
    Blender-subprocess pattern. Four modes (see MoldRequest.mode in
    models.py):

    mode="silicone_block" (default) - four STL files, two rigid parts in
    two halves each:
      - Pour box (pour_box_bottom.stl / pour_box_top.stl): a box you set
        the printed model inside and pour RTV silicone into, forming the
        negative mold. Split at the model's own vertical midpoint; each
        half has interlocking hemispherical registration keys near the
        parting line, and the top half has a sprue (pour) hole and a
        vent hole through its ceiling so the second half can be poured
        with the box already assembled around the model.
      - Clamp shell (clamp_shell_bottom.stl / clamp_shell_top.stl): a
        second, thicker rigid box whose cavity is the *same* size as the
        pour box's cavity (since that's the silicone mold's own outer
        shape once cast) - it clamps around the finished two-piece
        silicone mold via a bolted flange, holding it rigid against the
        hydraulic pressure of pouring plaster of paris or cement into the
        silicone's own model-shaped cavity. Also has a sprue/vent pair.

    mode="direct_cast" - two STL files (direct_mold_bottom.stl /
    direct_mold_top.stl): a single rigid two-part mold whose cavity is
    the model's *own* mesh geometry (no physical model gets inserted
    here - there's no silicone step), for casting resin/urethane/foam
    straight into the printed mold. Bolted flange, no registration keys,
    no separate clamp shell. Only suitable for a model with no undercuts
    along the Z axis - see docs/adr/0006-direct-cast-mold-mode.md.

    mode="form_fitting" - four STL files: a thin, contour-following
    silicone skin's own pour tool (skin_pour_bottom.stl /
    skin_pour_top.stl) and a rigid support jacket
    (support_jacket_bottom.stl / support_jacket_top.stl). Both tools
    share the same cavity shape - the model's surface grown outward by
    shell_thickness_mm (see _offset_model_along_normals() in
    make_mold.py) - since that offset surface *is* the finished silicone
    shell's own outer surface. The skin-pour tool casts that thin shell
    (with registration keys, like silicone_block's pour box); the
    support jacket then bolts around the finished flexible shell to hold
    it rigid for the final pour (like silicone_block's clamp shell). For
    organic/detailed models where a rigid direct_cast mold couldn't
    release along any single axis. The offset is a per-vertex normal
    push, not a true Minkowski offset: flat interior regions grow by
    exactly shell_thickness_mm, but sharp corners/edges are under-grown
    (their vertex normal is an average of adjacent face normals, not
    perpendicular to any one face) - see
    docs/adr/0008-form-fitting-thin-shell-mold.md. Negligible for
    organic/rounded models; a boxy model with sharp corners will have a
    thinner-than-requested shell right at those corners.

    mode="hollow_cast" - three STL files: the same rigid
    outer mold as direct_cast (hollow_cast_bottom.stl /
    hollow_cast_top.stl) plus a separate core (hollow_cast_core.stl) -
    the model's own surface shrunk inward by cast_wall_thickness_mm (the
    same _offset_model_along_normals() helper form_fitting uses, run
    with a negative offset). Seat the core inside the bottom half's
    cavity before closing the top half and pouring: the core displaces
    the cavity's center, so the cast forms a hollow shell (a vessel wall)
    instead of a solid block. No boolean cut against the core - it's
    just a smaller object nested inside the same model-shaped cavity,
    naturally centered by the resulting shell-thickness gap on every
    side. Shares direct_cast's release-tolerance and undercut
    limitations, plus form_fitting's offset-technique limitations
    (corner-rounding, concave self-intersection risk) applied inward
    instead of outward - see docs/adr/0009-hollow-vessel-inner-core-mode.md.
    Before generating any mode, the input mesh is checked for
    watertightness via watertight.analyze_mesh() and, if needed,
    auto-repaired via watertight.repair_mesh() - see
    _ensure_watertight_input()'s docstring. A rigid boolean cavity cut
    needs a genuinely closed manifold mesh; feeding it broken geometry
    produces opaque Blender failures rather than a clear error.

    After generating a direct_cast mold, draft_analysis.analyze_draft()
    runs automatically as a non-blocking warning (FR-4) - a rigid mold
    can't flex to release an undercut the way silicone_block's silicone
    step can, so any undercut/insufficient-draft area is surfaced via
    MoldResult.draft_check rather than silently shipped. Purely
    informational for silicone_block (not run automatically there); call
    draft_analysis.analyze_draft() directly, or
    POST /models/{model_id}/mold/draft-check, for that mode or for full
    per-face detail. See docs/adr/0007-draft-undercut-analysis.md.
Inputs: A model mesh file path (STL/OBJ/etc, anything blender_scripts/
    _shared.import_mesh supports), and the mold parameters below (all in
    millimeters).
Outputs: A MoldResult (see models.py) with paths to whichever STL files
    the chosen mode produces (other modes' fields are None),
    repaired_hole_ids listing any watertight defects that were
    auto-repaired before generation, (direct_cast only) draft_check, and
    cavity_volume_cm3 (FR-8 - the actual cast/pour material volume; see
    docs/adr/0010-configurable-parting-axis-and-volume-reporting.md for
    what "cavity" means per mode).
Troubleshooting:
    - "is not watertight ... none of its defects are confidently
      auto-repairable" or "... hole(s) remain": the input mesh has holes
      hole_classifier.py doesn't confidently call defects (ambiguous or
      likely-intentional openings) or non-manifold junction edges (which
      repair_mesh() can't close) - inspect it via
      POST /models/{model_id}/analyze and repair manually, or fix the
      source mesh, before retrying.
    - "resulting mold would be ...mm ... exceeding MAX_MOLD_DIMENSION_MM":
      lower clearance_mm/pour_box_wall_mm/clamp_wall_mm/
      clamp_flange_width_mm/direct_mold_wall_mm, or raise
      MAX_MOLD_DIMENSION_MM if the part is genuinely large - this cap
      exists so a typo (e.g. clearance_mm meant as cm) doesn't hang a
      headless Blender boolean pass building a multi-meter box.
    - A key bump/socket pair not aligning: they're placed at the four
      corners of the cavity's footprint, inset by half the pour box wall
      thickness - if pour_box_wall_mm is very thin (<2mm) the key radius
      is clamped to 40% of it and may end up too small to feel secure by
      hand; increase pour_box_wall_mm rather than key_diameter_mm.
    - direct_cast mode has no release tolerance yet - the cast part fits
      the model's own dimensions exactly, so a tight print may need mold
      release or light sanding to separate cleanly.
    - form_fitting mode's shell thickness is only exact on flat/rounded
      regions; sharp corners/edges come out thinner than
      shell_thickness_mm (the per-vertex offset technique's known
      limitation - see docs/adr/0008-form-fitting-thin-shell-mold.md).
      Also has no self-intersection guard for deeply concave models -
      a large shell_thickness_mm on a model with tight concave features
      can fold the offset surface back on itself.
    - hollow_cast mode's core has the identical corner-rounding and
      self-intersection risks as form_fitting's offset (same helper, run
      inward instead of outward), plus no registration/support geometry
      of its own in v1 - it simply rests inside the bottom half's cavity
      by hand before the top half closes and the pour happens. See
      docs/adr/0009-hollow-vessel-inner-core-mode.md.
    - parting_axis 'x'/'y' rotates the model internally before building
      (see make_mold.py's _rotate_axis_to_z()) and every exported STL
      stays in that rotated frame - it is NOT rotated back to match the
      original upload's orientation. See
      docs/adr/0010-configurable-parting-axis-and-volume-reporting.md.
    - "parting_offset_mm=... would put the parting plane at ..., outside
      the model's own range": the requested offset pushed the split past
      one of the model's own bounding-box extremes - reduce it.
    - cavity_volume_cm3 means slightly different things per mode (see
      ADR 0010): silicone_block/direct_cast report the actual pour/cast
      volume; form_fitting/hollow_cast subtract out the model's own
      volume first, since only the thin gap around it actually fills
      with material.
    - See docs/adr/0005-two-piece-silicone-mold-and-clamp-shell.md for
      why the pour box uses keys but the clamp shell uses a bolted flange
      instead, why both share one cavity size, and how the geometry was
      verified against a real Blender install (ray-casting into the
      cavity to confirm it's genuinely open, not just "0 boundary
      edges" - which turned out not to mean what it sounds like once a
      boolean result's cut face has a hole in it); 0006 for direct_cast
      mode's own decisions (model-mesh-as-cavity-tool, no draft/
      tolerance/keys in v1).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from threedprompt.config import settings
from threedprompt.draft_analysis import DraftAnalysisError
from threedprompt.draft_analysis import analyze_draft as _analyze_draft
from threedprompt.logging_config import get_logger
from threedprompt.models import DraftReport, HoleClassification, MoldResult
from threedprompt.watertight import WatertightError
from threedprompt.watertight import analyze_mesh as _analyze_mesh
from threedprompt.watertight import repair_mesh as _repair_mesh

logger = get_logger(__name__)

_BLENDER_SCRIPTS_DIR = Path(__file__).resolve().parent / "blender_scripts"
_MAKE_MOLD_SCRIPT = _BLENDER_SCRIPTS_DIR / "make_mold.py"

_DIRECT_CAST_DRAFT_CHECK_MIN_ANGLE_DEG = 2.0

_POSITIVE_PARAMS = (
    "clearance_mm",
    "pour_box_wall_mm",
    "key_diameter_mm",
    "sprue_diameter_mm",
    "vent_diameter_mm",
    "clamp_wall_mm",
    "clamp_flange_width_mm",
    "bolt_hole_diameter_mm",
    "direct_mold_wall_mm",
    "shell_thickness_mm",
    "skin_pour_wall_mm",
    "support_jacket_wall_mm",
    "cast_wall_thickness_mm",
)


class MoldError(RuntimeError):
    """Raised when mold generation can't complete (bad params, missing Blender, or a geometry failure)."""


def _blender_binary_path() -> str:
    """Resolve the configured blender binary, raising a clear error if it isn't on PATH (matches thickness.py)."""
    resolved = shutil.which(settings.blender_binary) or (
        settings.blender_binary if Path(settings.blender_binary).is_file() else None
    )
    if not resolved:
        raise MoldError(
            f"blender binary '{settings.blender_binary}' not found on PATH. "
            "Install Blender or set BLENDER_BINARY to its full path."
        )
    return resolved


def _read_report(report_path: Path, proc: subprocess.CompletedProcess) -> dict:
    """Load the Blender script's JSON report, raising MoldError for a missing file or a reported error."""
    if not report_path.exists():
        tail = "\n".join(proc.stdout.splitlines()[-40:] + proc.stderr.splitlines()[-40:])
        raise MoldError(f"blender mold generation produced no report (exit {proc.returncode}): {tail}")
    report = json.loads(report_path.read_text())
    if "error" in report:
        raise MoldError(f"mold generation failed: {report['error']}")
    return report


def _ensure_watertight_input(input_path: Path, tmp_dir: Path) -> tuple[Path, list[int]]:
    """
    Run watertight.analyze_mesh() on input_path before handing it to
    Blender's boolean solver, which needs a genuinely closed manifold
    mesh to produce a reliable cavity cut (see ADR 0005's boundary-edge
    gotcha - a boolean pass on broken input fails opaquely rather than
    cleanly). Auto-repairs any hole the existing hole_classifier.py
    heuristic (ADR 0004) confidently calls LIKELY_DEFECT (reusing that
    classification, not a new one); anything else left open (ambiguous
    holes, intentional openings, or non-manifold junction edges, which
    repair_mesh() can't touch) fails fast with a clear MoldError instead
    of silently attempting the mold boolean on broken geometry.

    Returns (path_to_use, repaired_hole_ids) - path_to_use is input_path
    unchanged if it was already watertight, or a repaired copy under
    tmp_dir otherwise; repaired_hole_ids is empty in the unchanged case.
    """
    try:
        report = _analyze_mesh(str(input_path))
    except WatertightError as exc:
        raise MoldError(f"could not check {input_path} for watertightness: {exc}") from exc

    if report.is_watertight:
        return input_path, []

    defect_ids = [h.id for h in report.holes if h.classification == HoleClassification.LIKELY_DEFECT]
    if not defect_ids:
        raise MoldError(
            f"model at {input_path} is not watertight ({len(report.holes)} hole(s), "
            f"{len(report.flipped_normal_islands)} flipped-normal island(s)) and none of its defects are "
            "confidently auto-repairable -- inspect it via POST /models/{model_id}/analyze and either repair "
            "it manually via POST /models/{model_id}/repair or fix the source mesh before generating a mold"
        )

    repaired_path = tmp_dir / f"repaired{input_path.suffix or '.stl'}"
    try:
        repair_result = _repair_mesh(str(input_path), defect_ids, str(repaired_path))
    except WatertightError as exc:
        raise MoldError(f"auto-repair of {input_path} failed: {exc}") from exc

    if not repair_result.is_watertight:
        raise MoldError(
            f"auto-repaired {len(defect_ids)} likely-defect hole(s) in {input_path}, but "
            f"{len(repair_result.remaining_holes)} hole(s) remain and aren't confidently auto-repairable -- "
            "inspect it via POST /models/{model_id}/analyze and repair the rest manually"
        )
    logger.info("Auto-repaired %d likely-defect hole(s) in %s before mold generation", len(defect_ids), input_path)
    return repaired_path, defect_ids


def _direct_cast_draft_warning(effective_input: Path) -> DraftReport | None:
    """
    Best-effort, non-blocking draft/undercut check (FR-4) - direct_cast
    mode's cavity is rigid and can't flex to release an undercut the way
    silicone_block's silicone step can, so this surfaces a warning
    (MoldResult.draft_check) rather than failing the request. If the
    check itself fails for any reason, log and return None rather than
    let an informational check undo a mold that already generated
    successfully by the time this runs.
    """
    try:
        return _analyze_draft(
            str(effective_input), pull_axis="z", min_draft_angle_deg=_DIRECT_CAST_DRAFT_CHECK_MIN_ANGLE_DEG
        )
    except DraftAnalysisError as exc:
        logger.warning("Draft/undercut check failed for %s (non-fatal): %s", effective_input, exc)
        return None


def make_mold(
    input_path: Path,
    output_dir: Path,
    *,
    mode: str = "silicone_block",
    clearance_mm: float = 8.0,
    pour_box_wall_mm: float = 4.0,
    key_diameter_mm: float = 6.0,
    sprue_diameter_mm: float = 10.0,
    vent_diameter_mm: float = 4.0,
    clamp_wall_mm: float = 6.0,
    clamp_flange_width_mm: float = 12.0,
    bolt_hole_diameter_mm: float = 4.5,
    direct_mold_wall_mm: float = 6.0,
    shell_thickness_mm: float = 3.0,
    skin_pour_wall_mm: float = 3.0,
    support_jacket_wall_mm: float = 5.0,
    cast_wall_thickness_mm: float = 4.0,
    parting_axis: str = "z",
    parting_offset_mm: float = 0.0,
) -> MoldResult:
    """
    Generate mold tooling (see module docstring for what each mode
    produces) for the model at input_path, writing the result into
    output_dir.

    Raises MoldError if any parameter isn't positive (parting_axis/
    parting_offset_mm are exempt - see below), parting_axis isn't
    'x'/'y'/'z', Blender is missing, the resulting box would exceed
    MAX_MOLD_DIMENSION_MM, parting_offset_mm would put the parting plane
    outside the model's own bounding box, or the Blender script otherwise
    fails (its own error message is preserved).
    """
    if parting_axis not in ("x", "y", "z"):
        raise MoldError(f"parting_axis must be 'x', 'y', or 'z' (got {parting_axis!r})")
    params = {
        "clearance_mm": clearance_mm,
        "pour_box_wall_mm": pour_box_wall_mm,
        "key_diameter_mm": key_diameter_mm,
        "sprue_diameter_mm": sprue_diameter_mm,
        "vent_diameter_mm": vent_diameter_mm,
        "clamp_wall_mm": clamp_wall_mm,
        "clamp_flange_width_mm": clamp_flange_width_mm,
        "bolt_hole_diameter_mm": bolt_hole_diameter_mm,
        "direct_mold_wall_mm": direct_mold_wall_mm,
        "shell_thickness_mm": shell_thickness_mm,
        "skin_pour_wall_mm": skin_pour_wall_mm,
        "support_jacket_wall_mm": support_jacket_wall_mm,
        "cast_wall_thickness_mm": cast_wall_thickness_mm,
    }
    for name in _POSITIVE_PARAMS:
        if params[name] <= 0:
            raise MoldError(f"{name} must be greater than 0 (got {params[name]})")

    output_dir.mkdir(parents=True, exist_ok=True)
    binary = _blender_binary_path()

    with tempfile.TemporaryDirectory() as tmp:
        effective_input, repaired_hole_ids = _ensure_watertight_input(input_path, Path(tmp))
        report_path = Path(tmp) / "report.json"
        cmd = [
            binary,
            "--background",
            "--python",
            str(_MAKE_MOLD_SCRIPT),
            "--",
            "--input",
            str(effective_input),
            "--output-dir",
            str(output_dir),
            "--report-output",
            str(report_path),
            "--mode",
            mode,
            "--direct-mold-wall-mm",
            str(direct_mold_wall_mm),
            "--shell-thickness-mm",
            str(shell_thickness_mm),
            "--skin-pour-wall-mm",
            str(skin_pour_wall_mm),
            "--support-jacket-wall-mm",
            str(support_jacket_wall_mm),
            "--cast-wall-thickness-mm",
            str(cast_wall_thickness_mm),
            "--parting-axis",
            parting_axis,
            "--parting-offset-mm",
            str(parting_offset_mm),
            "--clearance-mm",
            str(clearance_mm),
            "--pour-box-wall-mm",
            str(pour_box_wall_mm),
            "--key-diameter-mm",
            str(key_diameter_mm),
            "--sprue-diameter-mm",
            str(sprue_diameter_mm),
            "--vent-diameter-mm",
            str(vent_diameter_mm),
            "--clamp-wall-mm",
            str(clamp_wall_mm),
            "--clamp-flange-width-mm",
            str(clamp_flange_width_mm),
            "--bolt-hole-diameter-mm",
            str(bolt_hole_diameter_mm),
            "--max-dimension-mm",
            str(settings.max_mold_dimension_mm),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=settings.cad_subprocess_timeout_seconds)
        report = _read_report(report_path, result)
        # Must run while effective_input (possibly the repaired copy under
        # tmp, which is deleted once this `with` block exits) still exists.
        draft_check = _direct_cast_draft_warning(effective_input) if mode == "direct_cast" else None

    paths = {key: Path(value) for key, value in report["paths"].items()}
    for key, path in paths.items():
        if not path.is_file():
            raise MoldError(f"Blender reported {key} at {path} but it wasn't written")

    logger.info("Generated mold for %s in %s: %s", input_path, output_dir, sorted(str(p) for p in paths.values()))
    return MoldResult(
        pour_box_bottom_stl=str(paths["pour_box_bottom"]) if "pour_box_bottom" in paths else None,
        pour_box_top_stl=str(paths["pour_box_top"]) if "pour_box_top" in paths else None,
        clamp_shell_bottom_stl=str(paths["clamp_shell_bottom"]) if "clamp_shell_bottom" in paths else None,
        clamp_shell_top_stl=str(paths["clamp_shell_top"]) if "clamp_shell_top" in paths else None,
        direct_mold_bottom_stl=str(paths["direct_mold_bottom"]) if "direct_mold_bottom" in paths else None,
        direct_mold_top_stl=str(paths["direct_mold_top"]) if "direct_mold_top" in paths else None,
        skin_pour_bottom_stl=str(paths["skin_pour_bottom"]) if "skin_pour_bottom" in paths else None,
        skin_pour_top_stl=str(paths["skin_pour_top"]) if "skin_pour_top" in paths else None,
        support_jacket_bottom_stl=str(paths["support_jacket_bottom"]) if "support_jacket_bottom" in paths else None,
        support_jacket_top_stl=str(paths["support_jacket_top"]) if "support_jacket_top" in paths else None,
        hollow_cast_bottom_stl=str(paths["hollow_cast_bottom"]) if "hollow_cast_bottom" in paths else None,
        hollow_cast_top_stl=str(paths["hollow_cast_top"]) if "hollow_cast_top" in paths else None,
        hollow_cast_core_stl=str(paths["hollow_cast_core"]) if "hollow_cast_core" in paths else None,
        repaired_hole_ids=repaired_hole_ids,
        draft_check=draft_check,
        cavity_volume_cm3=report["cavity_volume_mm3"] / 1000.0,
    )
