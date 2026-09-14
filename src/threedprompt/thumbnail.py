"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/src/threedprompt/thumbnail.py
Description: Public entry point for rendering a PNG thumbnail of a mesh
    or STEP file via headless Blender
    (blender_scripts/render_thumbnail.py) -- an auto-framed, Workbench-
    engine solid-shaded render, fast enough to run on demand per file
    rather than needing a pre-baked render pipeline (Meshory's own
    published benchmark shows why that distinction matters at library
    scale: switching from "render everything up front" to "render what's
    asked for" was a 56x first-scan speedup on their end -- see
    docs/industry-tool-review-and-recommendations.md #7 in the sibling
    3dPrinterWorkshopManager repo). STEP/STP input is first converted to a
    temporary mesh via freecad_cad.step_to_mesh() (Blender has no STEP
    importer), reusing the FreeCAD backend built for wall-thickness/STEP/
    solid-healing -- see docs/adr/0005-freecad-third-cad-backend.md.
    main.py's POST /thumbnail is this module's only caller.
Inputs: A mesh/STEP file path, an output PNG path, and a square pixel
    size, plus settings.blender_binary / settings.thumbnail_max_size_px /
    settings.cad_subprocess_timeout_seconds from config.py.
Outputs: A PNG file written to the given output path.
Troubleshooting:
    - ThumbnailError "unsupported format": .3mf is deliberately not
      handled here -- extracting its embedded slicer-preview PNG (as the
      farm-manager sibling repo's libraryAssets.ts already does) is
      cheaper and more accurate than a fresh Blender render of a
      re-triangulated mesh; .amf has no Blender importer at all, and
      isn't converted via FreeCAD either since FreeCAD's own Mesh module
      doesn't read it.
    - ThumbnailError "blender thumbnail render failed": check the
      included stdout/stderr tail first -- same subprocess/error shape as
      thickness.py's mesh_shell(), which this module's Blender invocation
      deliberately mirrors (Blender's exit code is reliable, unlike
      FreeCADCmd's -- contrast freecad_cad.py's docstring).
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from threedprompt import freecad_cad
from threedprompt.config import settings
from threedprompt.logging_config import get_logger

logger = get_logger(__name__)

_HERE = Path(__file__).resolve().parent
_BLENDER_SCRIPTS_DIR = _HERE / "blender_scripts"
_RENDER_SCRIPT = _BLENDER_SCRIPTS_DIR / "render_thumbnail.py"

# Everything _shared.import_mesh() already handles directly, plus STEP/STP
# via a FreeCAD-conversion pre-step below. Deliberately excludes .3mf (see
# module docstring) and .amf (no importer anywhere in this pipeline).
SUPPORTED_EXTENSIONS = {".stl", ".obj", ".ply", ".glb", ".gltf", ".fbx", ".3dm", ".step", ".stp"}
_STEP_EXTENSIONS = {".step", ".stp"}

DEFAULT_SIZE_PX = 512
MIN_SIZE_PX = 32


class ThumbnailError(RuntimeError):
    """Raised when a thumbnail can't be rendered: unsupported format, missing binary, or a render failure."""


def _blender_binary_path() -> str:
    """Resolve the configured blender binary, raising a clear error if it isn't on PATH (matches thickness.py)."""
    resolved = shutil.which(settings.blender_binary) or (
        settings.blender_binary if Path(settings.blender_binary).is_file() else None
    )
    if not resolved:
        raise ThumbnailError(
            f"blender binary '{settings.blender_binary}' not found on PATH. "
            "Install Blender or set BLENDER_BINARY to its full path."
        )
    return resolved


def render_mesh_thumbnail(input_path: Path, output_path: Path, *, size: int = DEFAULT_SIZE_PX) -> Path:
    """
    Render a square PNG thumbnail of input_path to output_path.

    size is clamped to [MIN_SIZE_PX, settings.thumbnail_max_size_px] as a
    defense-in-depth backstop -- main.py's /thumbnail endpoint is expected
    to validate it against the same bounds first and return a 422, so this
    clamp should only ever matter for a caller that skips that endpoint.

    Raises ThumbnailError on an unsupported format, missing Blender/
    FreeCAD binary, a STEP-to-mesh conversion failure, or a Blender
    render failure (with its stdout/stderr tail included).
    """
    suffix = input_path.suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise ThumbnailError(
            f"unsupported format for thumbnail rendering: {suffix!r}; supported: {sorted(SUPPORTED_EXTENSIONS)}"
        )
    size = max(MIN_SIZE_PX, min(size, settings.thumbnail_max_size_px))
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        mesh_path = input_path
        if suffix in _STEP_EXTENSIONS:
            converted = Path(tmp) / "converted_for_thumbnail.stl"
            try:
                freecad_cad.step_to_mesh(input_path, converted)
            except freecad_cad.FreeCADCADError as exc:
                raise ThumbnailError(f"could not convert STEP to mesh for thumbnail rendering: {exc}") from exc
            mesh_path = converted

        binary = _blender_binary_path()
        result = subprocess.run(
            [
                binary,
                "--background",
                "--python",
                str(_RENDER_SCRIPT),
                "--",
                "--input",
                str(mesh_path),
                "--output",
                str(output_path),
                "--size",
                str(size),
            ],
            capture_output=True,
            text=True,
            timeout=settings.cad_subprocess_timeout_seconds,
        )
        if result.returncode != 0 or not output_path.exists():
            tail = "\n".join(result.stdout.splitlines()[-40:] + result.stderr.splitlines()[-40:])
            raise ThumbnailError(f"blender thumbnail render failed (exit {result.returncode}): {tail}")

    logger.info("Rendered %s -> thumbnail %s (%dpx).", input_path, output_path, size)
    return output_path
