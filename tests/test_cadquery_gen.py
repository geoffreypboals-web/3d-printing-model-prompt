"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/tests/test_cadquery_gen.py
Description: Tests for the sandboxed CadQuery backend (cadquery_gen/).
    The plumbing tests always run. The sandbox tests run the real
    runner.py and need Linux with Landlock plus cadquery installed; the
    fixture-prompt tests also need Blender for the watertight check. In
    practice: run inside the Docker image (see README.md).
Inputs: Canned CadQuery code standing in for the LLM (no network, no LLM).
Outputs: N/A (test module).
Troubleshooting:
    - Everything here skipped: check the skip reasons (`pytest -rs`). A
      green run on Windows proves only the plumbing, not the sandbox.
"""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from threedprompt import cadquery_gen
from threedprompt.cadquery_gen import CadQueryGenerationError, generate, landlock_abi, run_sandboxed
from threedprompt.models import Backend

needs_sandbox = pytest.mark.skipif(
    landlock_abi() < 1 or importlib.util.find_spec("cadquery") is None,
    reason="needs Linux with Landlock and cadquery installed",
)
needs_blender = pytest.mark.skipif(shutil.which("blender") is None, reason="blender binary not found on PATH")

FIXTURES = {
    "an L bracket 50mm with a 5mm mounting hole": """\
wall_thickness = 4
base = cq.Workplane("XY").box(50, 20, wall_thickness, centered=False)
upright = cq.Workplane("XY").box(wall_thickness, 20, 50, centered=False)
hole = cq.Workplane("XY").center(30, 10).circle(2.5).extrude(wall_thickness)
result = base.union(upright).cut(hole)
""",
    "a 60x40x20mm box with rounded vertical edges": """\
result = cq.Workplane("XY").box(60, 40, 20).edges("|Z").fillet(3)
""",
    "an 80x50x30mm enclosure, open top, 2mm walls": """\
wall_thickness = 2
result = cq.Workplane("XY").box(80, 50, 30).faces(">Z").shell(-wall_thickness)
""",
}


class FakeLLM:
    """Stands in for LLMClient: returns queued replies and records the prompts it was sent."""

    def __init__(self, *replies):
        self.replies, self.prompts = list(replies), []

    def generate(self, prompt, system=None):
        self.prompts.append(prompt)
        return self.replies.pop(0)


def _run(tmp_path: Path, code: str) -> tuple[Path, str | None]:
    """Run code in the sandbox; return (output_dir, error message or None)."""
    out = tmp_path / "out"
    out.mkdir()
    (out / "part.py").write_text(code)
    try:
        run_sandboxed(out / "part.py", out)
        return out, None
    except CadQueryGenerationError as exc:
        return out, str(exc)


# --- plumbing (always runs) ---


def test_refuses_without_landlock(tmp_path, monkeypatch):
    monkeypatch.setattr(cadquery_gen, "landlock_abi", lambda: 0)
    llm = FakeLLM("result = 1")
    with pytest.raises(CadQueryGenerationError, match="Landlock unavailable"):
        generate("a box", tmp_path, llm_client=llm)
    assert llm.prompts == []  # no LLM call spent


def test_retries_with_error_fed_back(tmp_path, monkeypatch):
    monkeypatch.setattr(cadquery_gen, "landlock_abi", lambda: 1)
    calls = []

    def fake_run(code_path, output_dir):
        calls.append(code_path.read_text())
        if len(calls) == 1:
            raise CadQueryGenerationError("NameError: name 'boxx' is not defined")
        return output_dir / "model.stl"

    monkeypatch.setattr(cadquery_gen, "run_sandboxed", fake_run)
    monkeypatch.setattr(cadquery_gen, "analyze_mesh", lambda p: SimpleNamespace(is_watertight=True, holes=[]))
    llm = FakeLLM("```python\nresult = cq.boxx()\n```", "wall_thickness = 2\nresult = cq.Workplane().box(1, 1, 1)")
    result = generate("a box", tmp_path, llm_client=llm)
    assert calls[0] == "result = cq.boxx()"  # fences stripped
    assert "boxx" in llm.prompts[1]
    assert result.backend is Backend.CADQUERY and result.wall_thickness_param == "wall_thickness"


def test_not_watertight_is_retried_then_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(cadquery_gen, "landlock_abi", lambda: 1)
    monkeypatch.setattr(cadquery_gen, "run_sandboxed", lambda c, o: o / "model.stl")
    monkeypatch.setattr(cadquery_gen, "analyze_mesh", lambda p: SimpleNamespace(is_watertight=False, holes=[1]))
    llm = FakeLLM("result = 1", "result = 2")
    with pytest.raises(CadQueryGenerationError, match="not watertight"):
        generate("a box", tmp_path, llm_client=llm)
    assert "not watertight" in llm.prompts[1]


# --- the real sandbox ---


@needs_sandbox
def test_sandbox_blocks_network(tmp_path):
    _, err = _run(tmp_path, "import socket\nsocket.create_connection(('1.1.1.1', 80), timeout=3)\nresult = None")
    assert err and "socket" in err and "blocked" in err


@needs_sandbox
def test_sandbox_blocks_subprocess(tmp_path):
    _, err = _run(tmp_path, "import subprocess\nsubprocess.run(['true'])\nresult = None")
    assert err and "subprocess.Popen blocked" in err


@needs_sandbox
def test_sandbox_blocks_ctypes(tmp_path):
    _, err = _run(tmp_path, "import ctypes\nctypes.CDLL(None)\nresult = None")
    assert err and "ctypes" in err and "blocked" in err


@needs_sandbox
def test_sandbox_blocks_python_write_outside_output(tmp_path):
    target = tmp_path / "escape.txt"
    _, err = _run(tmp_path, f"open({str(target)!r}, 'w').write('x')\nresult = None")
    assert err and "Permission denied" in err
    assert not target.exists()


@needs_sandbox
def test_sandbox_blocks_native_occt_write_outside_output(tmp_path):
    # OCCT writes through C++, invisible to Python's audit hooks: only Landlock stops this.
    target = tmp_path / "escape.stl"
    _, err = _run(tmp_path, f"cq.Workplane().box(1, 1, 1).val().exportStl({str(target)!r})\nresult = None")
    assert not target.exists()
    assert err  # result = None, so the run fails either way; the point is the file never appeared


@needs_sandbox
def test_sandbox_allows_writes_inside_output_and_strips_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-should-not-leak")
    code = (
        "import os\n"
        "open('note.txt', 'w').write(os.environ.get('ANTHROPIC_API_KEY', 'none'))\n"
        "result = cq.Workplane().box(10, 10, 10)\n"
    )
    out, err = _run(tmp_path, code)
    assert err is None
    assert (out / "note.txt").read_text() == "none"
    assert (out / "model.stl").stat().st_size > 0


@needs_sandbox
@needs_blender
@pytest.mark.parametrize("prompt", list(FIXTURES))
def test_fixture_prompts_generate_watertight_parts(tmp_path, prompt):
    result = generate(prompt, tmp_path / "model", llm_client=FakeLLM(FIXTURES[prompt]))
    assert Path(result.stl_path).stat().st_size > 0
    assert result.backend is Backend.CADQUERY and result.source_kind == "cadquery_py"
