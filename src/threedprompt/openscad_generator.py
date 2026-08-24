"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/src/threedprompt/openscad_generator.py
Description: Simple/parametric-part backend. Prefers a small library of
    deterministic, no-LLM-call OpenSCAD templates (bracket, box/enclosure,
    plate, spacer) for common parts; falls back to asking the configured
    LLM to author OpenSCAD source for anything else. Either way, renders
    the resulting .scad file to STL via the `openscad` CLI binary.
Inputs: A prompt string, optional wall_thickness_mm, an output directory
    to write into, an optional LLMClient (see llm_client.py).
Outputs: A GenerationResult (see models.py) pointing at the rendered
    .stl and the .scad source kept alongside it for later regeneration
    (see thickness.py).
Troubleshooting:
    - "openscad binary not found": install OpenSCAD or set
      OPENSCAD_BINARY to its full path; the Dockerfile installs it via
      apt so this should only bite local (non-Docker) runs.
    - If LLM-authored .scad fails to render, we retry once by feeding the
      openscad error back to the LLM and asking it to fix the file; a
      second failure raises OpenScadGenerationError with the original
      compiler output so it's actionable, not a stack trace.
    - Every template's wall/plate thickness variable is named exactly
      `wall_thickness` - thickness.py depends on that name to regenerate
      a thicker part from source instead of shelling the mesh.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from threedprompt.config import settings
from threedprompt.llm_client import LLMClient, LLMError
from threedprompt.logging_config import get_logger
from threedprompt.models import Backend, GenerationResult

logger = get_logger(__name__)

WALL_THICKNESS_VAR = "wall_thickness"


class OpenScadGenerationError(RuntimeError):
    """Raised when a part can't be produced via OpenSCAD (missing binary, bad .scad, etc.)."""


_DIMENSION_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(mm|cm|in|inch|inches)?", re.IGNORECASE)


def _first_dimension_mm(prompt: str, default: float) -> float:
    """Pull the first numeric dimension out of the prompt, converting to mm; falls back to default."""
    match = _DIMENSION_RE.search(prompt)
    if not match:
        return default
    value = float(match.group(1))
    unit = (match.group(2) or "mm").lower()
    if unit in ("in", "inch", "inches"):
        return value * 25.4
    if unit == "cm":
        return value * 10.0
    return value


_BRACKET_TEMPLATE = """\
// Auto-generated parametric L-bracket
{wall_var} = {wall_thickness};
arm_length = {arm_length};
width = {width};
hole_diameter = {hole_diameter};

module l_bracket() {{
    difference() {{
        union() {{
            cube([arm_length, width, {wall_var}]);
            cube([{wall_var}, width, arm_length]);
        }}
        translate([arm_length / 2, width / 2, {wall_var} / 2])
            cylinder(h = {wall_var} + 1, d = hole_diameter, center = true, $fn = 32);
        translate([{wall_var} / 2, width / 2, arm_length / 2])
            rotate([0, 90, 0])
            cylinder(h = {wall_var} + 1, d = hole_diameter, center = true, $fn = 32);
    }}
}}

l_bracket();
"""

_BOX_TEMPLATE = """\
// Auto-generated parametric enclosure
{wall_var} = {wall_thickness};
outer_x = {outer_x};
outer_y = {outer_y};
outer_z = {outer_z};

module enclosure() {{
    difference() {{
        cube([outer_x, outer_y, outer_z]);
        translate([{wall_var}, {wall_var}, {wall_var}])
            cube([outer_x - 2 * {wall_var}, outer_y - 2 * {wall_var}, outer_z - {wall_var}]);
    }}
}}

enclosure();
"""

_PLATE_TEMPLATE = """\
// Auto-generated flat plate
{wall_var} = {wall_thickness};
plate_x = {plate_x};
plate_y = {plate_y};

cube([plate_x, plate_y, {wall_var}]);
"""

_SPACER_TEMPLATE = """\
// Auto-generated cylindrical spacer / standoff
{wall_var} = {wall_thickness};
outer_diameter = {outer_diameter};
bore_diameter = {bore_diameter};
height = {height};

difference() {{
    cylinder(h = height, d = outer_diameter, $fn = 64);
    translate([0, 0, -1])
        cylinder(h = height + 2, d = bore_diameter, $fn = 64);
}}
// Note: outer_diameter - bore_diameter defines the effective {wall_var} of this ring.
"""

_TEMPLATE_KEYWORDS = {
    "bracket": _BRACKET_TEMPLATE,
    "box": _BOX_TEMPLATE,
    "enclosure": _BOX_TEMPLATE,
    "case": _BOX_TEMPLATE,
    "plate": _PLATE_TEMPLATE,
    "spacer": _SPACER_TEMPLATE,
    "standoff": _SPACER_TEMPLATE,
}


def _detect_template(prompt: str) -> str | None:
    """Return the first matching built-in template name found in the prompt, or None."""
    lowered = prompt.lower()
    for keyword, template in _TEMPLATE_KEYWORDS.items():
        if keyword in lowered:
            return template
    return None


def _render_template(template: str, prompt: str, wall_thickness_mm: float | None) -> str:
    """Fill a template's dimension placeholders from the prompt (or sensible defaults)."""
    wall_thickness = wall_thickness_mm or 3.0
    dims = {
        "wall_var": WALL_THICKNESS_VAR,
        "wall_thickness": wall_thickness,
        "arm_length": _first_dimension_mm(prompt, 50.0),
        "width": 20.0,
        "hole_diameter": 5.0,
        "outer_x": _first_dimension_mm(prompt, 60.0),
        "outer_y": 40.0,
        "outer_z": 20.0,
        "plate_x": _first_dimension_mm(prompt, 80.0),
        "plate_y": 60.0,
        "outer_diameter": _first_dimension_mm(prompt, 10.0),
        "bore_diameter": 4.0,
        "height": 10.0,
    }
    return template.format(**dims)


_LLM_SYSTEM_PROMPT = (
    "You write valid OpenSCAD source code for simple, printable mechanical "
    "parts (brackets, mounts, spacers, plates, enclosures) built from basic "
    "CSG primitives (cube, cylinder, sphere, difference, union, intersection). "
    f"If the part has a wall or material thickness, declare it as a variable "
    f"named exactly `{WALL_THICKNESS_VAR}` near the top of the file and use "
    "that variable everywhere the thickness applies, so it can be tuned "
    "later. Respond with ONLY the .scad source code, no markdown fences, no "
    "explanation."
)


def _llm_generate_scad(prompt: str, llm_client: LLMClient, previous_error: str | None = None) -> str:
    """Ask the LLM to author OpenSCAD source for an arbitrary simple-part prompt."""
    user_prompt = f"Write OpenSCAD source for: {prompt}"
    if previous_error:
        user_prompt += (
            f"\n\nThe previous attempt failed to compile with this OpenSCAD error, "
            f"fix it and return the corrected full source:\n{previous_error}"
        )
    raw = llm_client.generate(prompt=user_prompt, system=_LLM_SYSTEM_PROMPT)
    return _strip_markdown_fences(raw)


def _strip_markdown_fences(text: str) -> str:
    """Remove ```scad / ``` fences an LLM sometimes wraps code in, despite instructions not to."""
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        return "\n".join(lines)
    return stripped


def _openscad_binary_path() -> str:
    """Resolve the configured openscad binary, raising a clear error if it isn't on PATH."""
    resolved = shutil.which(settings.openscad_binary) or (
        settings.openscad_binary if Path(settings.openscad_binary).is_file() else None
    )
    if not resolved:
        raise OpenScadGenerationError(
            f"openscad binary '{settings.openscad_binary}' not found on PATH. "
            "Install OpenSCAD or set OPENSCAD_BINARY to its full path."
        )
    return resolved


def render_scad_to_stl(scad_path: Path, stl_path: Path) -> None:
    """Invoke the openscad CLI to render a .scad file to .stl, raising with compiler output on failure."""
    binary = _openscad_binary_path()
    result = subprocess.run(
        [binary, "-o", str(stl_path), str(scad_path)],
        capture_output=True,
        text=True,
        timeout=settings.cad_subprocess_timeout_seconds,
    )
    if result.returncode != 0 or not stl_path.exists():
        raise OpenScadGenerationError(
            f"openscad failed to render {scad_path.name} (exit {result.returncode}): {result.stderr.strip()}"
        )


def generate(
    prompt: str,
    output_dir: Path,
    wall_thickness_mm: float | None = None,
    llm_client: LLMClient | None = None,
) -> GenerationResult:
    """
    Generate a simple/parametric part via OpenSCAD.

    Tries a deterministic built-in template first (no LLM call, no cost);
    falls back to LLM-authored OpenSCAD source for anything unrecognized,
    retrying once with the compiler error fed back in if the first
    attempt fails to render.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    scad_path = output_dir / "model.scad"
    stl_path = output_dir / "model.stl"

    template = _detect_template(prompt)
    if template is not None:
        scad_source = _render_template(template, prompt, wall_thickness_mm)
        scad_path.write_text(scad_source)
        render_scad_to_stl(scad_path, stl_path)
        logger.info("Generated %s via built-in OpenSCAD template (no LLM call).", stl_path)
        return GenerationResult(
            backend=Backend.OPENSCAD,
            stl_path=str(stl_path),
            source_path=str(scad_path),
            source_kind="openscad_scad",
            wall_thickness_param=WALL_THICKNESS_VAR,
        )

    _openscad_binary_path()  # fail fast on a missing binary rather than burning an LLM call first

    if llm_client is None:
        from threedprompt.llm_client import get_llm_client

        llm_client = get_llm_client()

    last_error: str | None = None
    for attempt in range(2):
        try:
            scad_source = _llm_generate_scad(prompt, llm_client, previous_error=last_error)
        except LLMError as exc:
            raise OpenScadGenerationError(f"LLM failed to author OpenSCAD source: {exc}") from exc
        scad_path.write_text(scad_source)
        try:
            render_scad_to_stl(scad_path, stl_path)
            has_wall_var = WALL_THICKNESS_VAR in scad_source
            logger.info("Generated %s via LLM-authored OpenSCAD (attempt %d).", stl_path, attempt + 1)
            return GenerationResult(
                backend=Backend.OPENSCAD,
                stl_path=str(stl_path),
                source_path=str(scad_path),
                source_kind="openscad_scad",
                wall_thickness_param=WALL_THICKNESS_VAR if has_wall_var else None,
            )
        except OpenScadGenerationError as exc:
            last_error = str(exc)
            logger.warning("OpenSCAD render attempt %d failed: %s", attempt + 1, last_error)

    raise OpenScadGenerationError(f"OpenSCAD generation failed after retry: {last_error}")
