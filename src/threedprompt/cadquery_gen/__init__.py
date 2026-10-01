"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/src/threedprompt/cadquery_gen/__init__.py
Description: Parametric-part backend using CadQuery (Python CAD on the
    OpenCASCADE kernel). Asks the configured LLM to write CadQuery code,
    runs it in a sandboxed subprocess (runner.py: Landlock + seccomp +
    rlimits + audit hook, stripped environment), exports STL, then runs the
    existing watertight check (watertight.py). One retry with the error
    fed back to the LLM, like the OpenSCAD and Blender backends.
Inputs: A prompt string, an output directory, an optional LLMClient;
    settings.cad_subprocess_timeout_seconds and
    settings.cadquery_memory_limit_mb from config.py.
Outputs: A GenerationResult (see models.py) pointing at model.stl and
    the part.py source kept alongside it.
Troubleshooting:
    - CadQueryGenerationError "Landlock unavailable": the backend only
      runs on a Linux kernel with Landlock (5.13+). It never falls back
      to running LLM code unsandboxed; on Windows/macOS use Docker.
    - "No module named 'cadquery'": `pip install -r requirements.txt`
      (cadquery pulls the cadquery-ocp OpenCASCADE wheel, ~100 MB).
    - "not watertight" after both attempts: the LLM's solid has open
      faces; the watertight report in the error names the holes.
    - Known ceiling: Landlock binds only the thread that calls it, so
      native threads started during `import cadquery` aren't
      write-restricted; they run library code only, never part code.
      (The seccomp filter uses TSYNC and does cover them.)
"""

from __future__ import annotations

import ctypes
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

from threedprompt.config import settings
from threedprompt.llm_client import LLMClient, LLMError
from threedprompt.logging_config import get_logger
from threedprompt.models import Backend, GenerationResult
from threedprompt.openscad_generator import _strip_markdown_fences
from threedprompt.watertight import WatertightError, analyze_mesh

logger = get_logger(__name__)

RUNNER = Path(__file__).resolve().parent / "runner.py"


class CadQueryGenerationError(RuntimeError):
    """Raised when a part can't be produced via CadQuery (no sandbox, bad code, not watertight, etc.)."""


_LLM_SYSTEM_PROMPT = (
    "You write CadQuery Python code for simple, printable mechanical parts "
    "(brackets, boxes, enclosures, mounts, plates). `cq` is already imported; "
    "you may also `import math`. Build one solid with cq.Workplane operations "
    "(box, cylinder, extrude, hole, shell, fillet, union, cut) in millimeters, "
    "and assign the finished part to a variable named `result`. If the part "
    "has a wall thickness, declare it as `wall_thickness` near the top. Do not "
    "read or write files, export, print, or import anything else. Respond "
    "with ONLY the Python code, no markdown fences, no explanation."
)


def landlock_abi() -> int:
    """Return the kernel's Landlock ABI version, or 0 where Landlock isn't available (non-Linux included)."""
    if not sys.platform.startswith("linux"):
        return 0
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    return max(0, libc.syscall(444, None, ctypes.c_size_t(0), ctypes.c_uint32(1)))


def available() -> bool:
    """True when this host can run the backend: Landlock in the kernel and cadquery installed."""
    return landlock_abi() >= 1 and importlib.util.find_spec("cadquery") is not None


def run_sandboxed(code_path: Path, output_dir: Path) -> Path:
    """Run part code in runner.py's sandbox; return model.stl or raise with the child's stderr."""
    stl_path = output_dir / "model.stl"
    stl_path.unlink(missing_ok=True)
    timeout = settings.cad_subprocess_timeout_seconds
    env = {"PATH": os.defpath, "HOME": str(output_dir), "LANG": "C.UTF-8"}  # no API keys reach the child
    cmd = [sys.executable, "-I", str(RUNNER), str(code_path), str(output_dir)]
    cmd += [str(timeout), str(settings.cadquery_memory_limit_mb)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env, cwd=output_dir)
    except subprocess.TimeoutExpired as exc:
        raise CadQueryGenerationError(f"part code ran longer than {timeout}s") from exc
    if proc.returncode != 0 or not stl_path.exists():
        tail = "\n".join(proc.stderr.splitlines()[-30:])
        raise CadQueryGenerationError(f"part code failed (exit {proc.returncode}): {tail}")
    return stl_path


def _llm_generate_code(prompt: str, llm_client: LLMClient, previous_error: str | None) -> str:
    """Ask the LLM for CadQuery code, feeding back the previous attempt's error if there was one."""
    user_prompt = f"Write CadQuery code for: {prompt}"
    if previous_error:
        user_prompt += (
            f"\n\nThe previous attempt failed with this error, fix it and return the full code:\n{previous_error}"
        )
    return _strip_markdown_fences(llm_client.generate(prompt=user_prompt, system=_LLM_SYSTEM_PROMPT))


def generate(
    prompt: str,
    output_dir: Path,
    wall_thickness_mm: float | None = None,
    llm_client: LLMClient | None = None,
) -> GenerationResult:
    """Generate a parametric part via LLM-written CadQuery code, sandboxed, checked watertight."""
    if landlock_abi() < 1:  # fail before spending an LLM call
        raise CadQueryGenerationError("Landlock unavailable: the CadQuery backend needs a Linux kernel with Landlock")
    if wall_thickness_mm:
        prompt += f" (wall_thickness = {wall_thickness_mm} mm)"
    output_dir.mkdir(parents=True, exist_ok=True)
    code_path = output_dir / "part.py"
    if llm_client is None:
        from threedprompt.llm_client import get_llm_client

        llm_client = get_llm_client()

    last_error: str | None = None
    for attempt in range(2):
        try:
            code = _llm_generate_code(prompt, llm_client, last_error)
        except LLMError as exc:
            raise CadQueryGenerationError(f"LLM failed to write CadQuery code: {exc}") from exc
        code_path.write_text(code, encoding="utf-8")
        try:
            stl_path = run_sandboxed(code_path, output_dir)
            report = analyze_mesh(str(stl_path))
        except (CadQueryGenerationError, WatertightError) as exc:
            last_error = str(exc)
            logger.warning("CadQuery attempt %d failed: %s", attempt + 1, last_error)
            continue
        if not report.is_watertight:
            last_error = f"the exported mesh is not watertight: {len(report.holes)} hole(s)"
            logger.warning("CadQuery attempt %d: %s", attempt + 1, last_error)
            continue
        logger.info("Generated %s via sandboxed CadQuery (attempt %d).", stl_path, attempt + 1)
        return GenerationResult(
            backend=Backend.CADQUERY,
            stl_path=str(stl_path),
            source_path=str(code_path),
            source_kind="cadquery_py",
            wall_thickness_param="wall_thickness" if "wall_thickness" in code else None,
        )
    raise CadQueryGenerationError(f"CadQuery generation failed after retry: {last_error}")
