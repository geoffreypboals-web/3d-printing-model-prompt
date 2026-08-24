"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/src/threedprompt/blender_generator.py
Description: Complex/organic-part backend. Asks the configured LLM to
    author a Python `build_scene()` function using Blender's `bpy` API
    (primitives, modifiers, booleans, metaballs) to model freeform shapes
    that don't fit OpenSCAD's CSG model (e.g. "a dwarf sitting under a
    mushroom"). Wraps that function in a fixed boilerplate script and runs
    it inside headless Blender (`blender --background --python`), which
    then exports the result to STL.
Inputs: A prompt string, an output directory, an optional LLMClient.
Outputs: A GenerationResult (see models.py) pointing at the rendered
    .stl and the .py build script kept alongside it.
Troubleshooting:
    - "blender binary not found": install Blender or set BLENDER_BINARY
      to its full path; the Dockerfile installs it via apt.
    - KNOWN LIMITATION: unlike the OpenSCAD path, there is no
      deterministic template fallback here - organic shape quality is
      entirely dependent on the LLM's ability to write correct, sensible
      bpy code from a short prompt. A small local Ollama model will
      produce much cruder geometry than a larger hosted model; this is a
      genuine case where paid-model quality helps (see CLAUDE.md rule 6)
      and that tradeoff should be a deliberate choice, not a silent
      default - if quality is disappointing, that is expected behavior
      of this approach, not a bug to chase.
    - If Blender exits with a Python traceback, it's included verbatim in
      BlenderGenerationError so the LLM's bad bpy call is visible instead
      of a bare "render failed".
    - This backend has no wall-thickness template variable (organic
      builds don't have one canonical "thickness"); thickness.py always
      uses the mesh-shell path for Blender-sourced models.
"""

from __future__ import annotations

import shutil
import subprocess
import textwrap
from pathlib import Path

from threedprompt.config import settings
from threedprompt.llm_client import LLMClient, LLMError
from threedprompt.logging_config import get_logger
from threedprompt.models import Backend, GenerationResult

logger = get_logger(__name__)


class BlenderGenerationError(RuntimeError):
    """Raised when a part can't be produced via Blender (missing binary, bad script, etc.)."""


_LLM_SYSTEM_PROMPT = (
    "You write a single Python function `build_scene()` that uses Blender's "
    "`bpy` API to model a described object as one or more mesh objects, "
    "suitable for 3D printing. Use primitives (bpy.ops.mesh.primitive_*), "
    "modifiers (Subdivision Surface, Boolean, Solidify), and simple "
    "sculpting-by-primitive composition to approximate the shape. Do not "
    "call bpy.ops.wm.save_mainfile, do not export anything yourself, and do "
    "not delete objects outside the ones you create - the caller clears the "
    "scene before calling you and exports afterward. Keep the whole model "
    "within roughly a 150mm cube. Respond with ONLY the Python function "
    "definition (imports of `bpy`, `bmesh`, `mathutils` allowed above it if "
    "needed), no markdown fences, no explanation."
)

_SCRIPT_TEMPLATE = """\
import bpy
import sys

{llm_code}

def _clear_scene():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)

def _export_stl(output_path):
    bpy.ops.object.select_all(action="SELECT")
    mesh_objects = [obj for obj in bpy.context.selected_objects if obj.type == "MESH"]
    if not mesh_objects:
        raise RuntimeError("build_scene() produced no mesh objects to export")
    for obj in mesh_objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = mesh_objects[0]
    if hasattr(bpy.ops.wm, "stl_export"):
        bpy.ops.wm.stl_export(filepath=output_path, export_selected_objects=True)
    else:
        bpy.ops.export_mesh.stl(filepath=output_path, use_selection=True)

if __name__ == "__main__":
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    output_path = argv[0]
    _clear_scene()
    build_scene()
    _export_stl(output_path)
"""


def _strip_markdown_fences(text: str) -> str:
    """Remove ```python / ``` fences an LLM sometimes wraps code in, despite instructions not to."""
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        return "\n".join(lines)
    return stripped


def _llm_generate_build_script(prompt: str, llm_client: LLMClient, previous_error: str | None = None) -> str:
    """Ask the LLM to author the build_scene() bpy function for an arbitrary complex-shape prompt."""
    user_prompt = f"Model this object: {prompt}"
    if previous_error:
        user_prompt += (
            f"\n\nThe previous attempt raised this error inside Blender, fix "
            f"it and return the corrected full build_scene() code:\n{previous_error}"
        )
    raw = llm_client.generate(prompt=user_prompt, system=_LLM_SYSTEM_PROMPT)
    return _strip_markdown_fences(raw)


def _blender_binary_path() -> str:
    """Resolve the configured blender binary, raising a clear error if it isn't on PATH."""
    resolved = shutil.which(settings.blender_binary) or (
        settings.blender_binary if Path(settings.blender_binary).is_file() else None
    )
    if not resolved:
        raise BlenderGenerationError(
            f"blender binary '{settings.blender_binary}' not found on PATH. "
            "Install Blender or set BLENDER_BINARY to its full path."
        )
    return resolved


def run_build_script(script_path: Path, stl_path: Path) -> None:
    """Invoke headless Blender to run script_path and export to stl_path, raising with traceback on failure."""
    binary = _blender_binary_path()
    result = subprocess.run(
        [binary, "--background", "--python", str(script_path), "--", str(stl_path)],
        capture_output=True,
        text=True,
        timeout=settings.cad_subprocess_timeout_seconds,
    )
    if result.returncode != 0 or not stl_path.exists():
        tail = "\n".join(result.stdout.splitlines()[-40:] + result.stderr.splitlines()[-40:])
        raise BlenderGenerationError(f"blender failed to render {script_path.name} (exit {result.returncode}): {tail}")


def generate(
    prompt: str,
    output_dir: Path,
    llm_client: LLMClient | None = None,
) -> GenerationResult:
    """
    Generate a complex/organic part via headless Blender.

    Always LLM-driven (no deterministic template exists for freeform
    shapes); retries once with the Blender traceback fed back in if the
    first attempt raises inside Blender.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    script_path = output_dir / "build.py"
    stl_path = output_dir / "model.stl"

    _blender_binary_path()  # fail fast on a missing binary rather than burning an LLM call first

    if llm_client is None:
        from threedprompt.llm_client import get_llm_client

        llm_client = get_llm_client()

    last_error: str | None = None
    for attempt in range(2):
        try:
            llm_code = _llm_generate_build_script(prompt, llm_client, previous_error=last_error)
        except LLMError as exc:
            raise BlenderGenerationError(f"LLM failed to author Blender build script: {exc}") from exc

        script_source = _SCRIPT_TEMPLATE.format(llm_code=textwrap.dedent(llm_code))
        script_path.write_text(script_source)
        try:
            run_build_script(script_path, stl_path)
            logger.info("Generated %s via Blender (attempt %d).", stl_path, attempt + 1)
            return GenerationResult(
                backend=Backend.BLENDER,
                stl_path=str(stl_path),
                source_path=str(script_path),
                source_kind="blender_bpy_script",
                wall_thickness_param=None,
            )
        except BlenderGenerationError as exc:
            last_error = str(exc)
            logger.warning("Blender render attempt %d failed: %s", attempt + 1, last_error)

    raise BlenderGenerationError(f"Blender generation failed after retry: {last_error}")
