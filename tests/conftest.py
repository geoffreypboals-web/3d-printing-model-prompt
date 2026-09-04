"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/tests/conftest.py
Description: Shared pytest fixtures - redirects storage.output_dir at a
    per-test temp directory, provides a fake LLMClient so tests never
    need a real Ollama/Claude backend or the openscad/blender binaries,
    and (for test_watertight_integration.py) a dependency-free binary
    STL writer so those fixtures don't need Blender just to be created.
Inputs: pytest's tmp_path fixture.
Outputs: fixtures `tmp_output_dir` (autouse), `fake_llm_client`,
    `watertight_cube_stl`, `cube_missing_one_triangle_stl`.
Troubleshooting:
    - If a test leaks state into another, check it isn't writing outside
      settings.output_dir - tmp_output_dir only redirects that one field.
    - If a "fixture" STL looks malformed to Blender, check
      write_binary_stl's triangle winding - each triangle's 3 vertices
      must be counter-clockwise as seen from outside the solid, or
      Blender will read it as fine geometrically but with inverted
      normals, which can confuse the flipped-normal-island detector in
      an otherwise-unrelated test.
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from threedprompt.config import settings
from threedprompt.llm_client import LLMClient


@pytest.fixture(autouse=True)
def tmp_output_dir(tmp_path, monkeypatch):
    """Point settings.output_dir at a fresh temp directory for every test."""
    monkeypatch.setattr(settings, "output_dir", str(tmp_path / "output"))
    return tmp_path / "output"


class FakeLLMClient(LLMClient):
    """Deterministic stand-in for a real LLM backend, scripted per-test with canned responses."""

    def __init__(self, responses: list[str] | None = None, reachable: bool = True):
        self._responses = list(responses or [])
        self.calls: list[tuple[str, str | None]] = []
        self._reachable = reachable

    def generate(self, prompt: str, system: str | None = None) -> str:
        self.calls.append((prompt, system))
        if not self._responses:
            raise AssertionError("FakeLLMClient.generate() called with no scripted responses left")
        return self._responses.pop(0)

    def is_reachable(self) -> bool:
        return self._reachable


@pytest.fixture
def fake_llm_client():
    return FakeLLMClient


# --- Watertight test fixtures (STL written directly, no Blender needed) ---

# The 12 triangles of a unit cube from (0,0,0) to (1,1,1), each vertex
# triple wound counter-clockwise as seen from outside the solid.
_CUBE_VERTICES = {
    "000": (0.0, 0.0, 0.0),
    "100": (1.0, 0.0, 0.0),
    "010": (0.0, 1.0, 0.0),
    "110": (1.0, 1.0, 0.0),
    "001": (0.0, 0.0, 1.0),
    "101": (1.0, 0.0, 1.0),
    "011": (0.0, 1.0, 1.0),
    "111": (1.0, 1.0, 1.0),
}
_CUBE_TRIANGLES = [
    ("000", "010", "110"),
    ("000", "110", "100"),  # bottom (z=0), normal -Z
    ("001", "101", "111"),
    ("001", "111", "011"),  # top (z=1), normal +Z
    ("000", "100", "101"),
    ("000", "101", "001"),  # front (y=0), normal -Y
    ("010", "011", "111"),
    ("010", "111", "110"),  # back (y=1), normal +Y
    ("000", "001", "011"),
    ("000", "011", "010"),  # left (x=0), normal -X
    ("100", "110", "111"),
    ("100", "111", "101"),  # right (x=1), normal +X
]


def _write_binary_stl(path: Path, triangle_keys) -> None:
    """Write a minimal binary STL from a list of 3-vertex-key triangles (see _CUBE_TRIANGLES)."""
    with open(path, "wb") as f:
        f.write(b"\x00" * 80)
        f.write(struct.pack("<I", len(triangle_keys)))
        for a, b, c in triangle_keys:
            v0, v1, v2 = _CUBE_VERTICES[a], _CUBE_VERTICES[b], _CUBE_VERTICES[c]
            f.write(struct.pack("<3f", 0.0, 0.0, 0.0))  # normal (unused by Blender's importer)
            for v in (v0, v1, v2):
                f.write(struct.pack("<3f", *v))
            f.write(struct.pack("<H", 0))


@pytest.fixture
def watertight_cube_stl(tmp_path) -> Path:
    """A fully closed unit-cube STL - 12 triangles, 0 holes."""
    path = tmp_path / "watertight_cube.stl"
    _write_binary_stl(path, _CUBE_TRIANGLES)
    return path


@pytest.fixture
def cube_missing_one_triangle_stl(tmp_path) -> Path:
    """A unit-cube STL missing one triangle - exactly one boundary-edge hole, not watertight."""
    path = tmp_path / "cube_with_gap.stl"
    _write_binary_stl(path, _CUBE_TRIANGLES[1:])  # drop one of the two bottom-face triangles
    return path
