"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/tests/test_thumbnail.py
Description: Tests for thumbnail.py's plumbing (format gating, size
    clamping, Blender subprocess invocation, STEP-via-FreeCAD conversion
    routing, error translation) with the actual Blender/FreeCAD
    subprocesses mocked out, matching test_thickness.py's style.
Inputs: pytest, tests/conftest.py fixtures.
Outputs: N/A (test module).
Troubleshooting:
    - These tests patch thumbnail.shutil.which and thumbnail.subprocess.run
      directly (the names thumbnail.py imported), not the stdlib modules -
      patch the wrong target and the mock silently doesn't take effect.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from threedprompt import freecad_cad, thumbnail
from threedprompt.config import settings
from threedprompt.thumbnail import ThumbnailError, render_mesh_thumbnail


def _fake_which_blender(binary_name):
    return "/usr/bin/blender"


def _make_fake_blender_run(succeed: bool = True):
    def _fake_run(cmd, capture_output, text, timeout):
        # cmd == [binary, "--background", "--python", script, "--", "--input", path, "--output", out, "--size", n]
        output_path = Path(cmd[cmd.index("--output") + 1])
        if succeed:
            output_path.write_bytes(b"\x89PNG\r\n\x1a\nfakepng")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="render error")

    return _fake_run


def test_render_mesh_thumbnail_golden_path(monkeypatch, tmp_path):
    monkeypatch.setattr(thumbnail.shutil, "which", _fake_which_blender)
    monkeypatch.setattr(thumbnail.subprocess, "run", _make_fake_blender_run(succeed=True))

    input_stl = tmp_path / "model.stl"
    input_stl.write_bytes(b"solid fake\nendsolid fake\n")
    output_png = tmp_path / "out" / "thumbnail.png"

    result_path = render_mesh_thumbnail(input_stl, output_png, size=256)

    assert result_path == output_png
    assert output_png.is_file()


def test_render_mesh_thumbnail_unsupported_format_raises_without_calling_blender(monkeypatch, tmp_path):
    called = False

    def _should_not_run(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(thumbnail.subprocess, "run", _should_not_run)
    input_amf = tmp_path / "model.amf"
    input_amf.write_bytes(b"<amf></amf>")

    with pytest.raises(ThumbnailError, match="unsupported format"):
        render_mesh_thumbnail(input_amf, tmp_path / "out" / "thumbnail.png")
    assert called is False


def test_render_mesh_thumbnail_3mf_is_unsupported(tmp_path):
    input_3mf = tmp_path / "model.3mf"
    input_3mf.write_bytes(b"fake 3mf")

    with pytest.raises(ThumbnailError, match="unsupported format"):
        render_mesh_thumbnail(input_3mf, tmp_path / "out" / "thumbnail.png")


def test_render_mesh_thumbnail_missing_binary_raises_clear_error(monkeypatch, tmp_path):
    monkeypatch.setattr(thumbnail.shutil, "which", lambda _name: None)
    input_stl = tmp_path / "model.stl"
    input_stl.write_bytes(b"solid fake\nendsolid fake\n")

    with pytest.raises(ThumbnailError, match="not found on PATH"):
        render_mesh_thumbnail(input_stl, tmp_path / "out" / "thumbnail.png")


def test_render_mesh_thumbnail_blender_failure_raises_with_stderr(monkeypatch, tmp_path):
    monkeypatch.setattr(thumbnail.shutil, "which", _fake_which_blender)
    monkeypatch.setattr(thumbnail.subprocess, "run", _make_fake_blender_run(succeed=False))
    input_stl = tmp_path / "model.stl"
    input_stl.write_bytes(b"solid fake\nendsolid fake\n")

    with pytest.raises(ThumbnailError, match="render error"):
        render_mesh_thumbnail(input_stl, tmp_path / "out" / "thumbnail.png")


def test_render_mesh_thumbnail_clamps_size_to_configured_max(monkeypatch, tmp_path):
    monkeypatch.setattr(thumbnail.shutil, "which", _fake_which_blender)
    monkeypatch.setattr(settings, "thumbnail_max_size_px", 1024)
    seen_sizes = []

    def _fake_run(cmd, capture_output, text, timeout):
        seen_sizes.append(int(cmd[cmd.index("--size") + 1]))
        output_path = Path(cmd[cmd.index("--output") + 1])
        output_path.write_bytes(b"\x89PNG\r\n\x1a\nfakepng")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(thumbnail.subprocess, "run", _fake_run)
    input_stl = tmp_path / "model.stl"
    input_stl.write_bytes(b"solid fake\nendsolid fake\n")

    render_mesh_thumbnail(input_stl, tmp_path / "out" / "thumbnail.png", size=999_999)

    assert seen_sizes == [1024]


def test_render_mesh_thumbnail_step_input_converts_via_freecad_first(monkeypatch, tmp_path):
    monkeypatch.setattr(thumbnail.shutil, "which", _fake_which_blender)
    convert_calls = []

    def _fake_step_to_mesh(input_path, output_path):
        convert_calls.append((input_path, output_path))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"solid converted\nendsolid converted\n")
        return {"ok": True, "volume": 1000.0, "solids": 1}

    monkeypatch.setattr(freecad_cad, "step_to_mesh", _fake_step_to_mesh)
    monkeypatch.setattr(thumbnail.subprocess, "run", _make_fake_blender_run(succeed=True))

    input_step = tmp_path / "model.step"
    input_step.write_text("ISO-10303-21;\n")

    render_mesh_thumbnail(input_step, tmp_path / "out" / "thumbnail.png")

    assert len(convert_calls) == 1
    assert convert_calls[0][0] == input_step


def test_render_mesh_thumbnail_step_conversion_failure_raises_thumbnailerror(monkeypatch, tmp_path):
    def _fake_step_to_mesh(input_path, output_path):
        raise freecad_cad.FreeCADCADError("Null input shape")

    monkeypatch.setattr(freecad_cad, "step_to_mesh", _fake_step_to_mesh)
    input_step = tmp_path / "model.step"
    input_step.write_text("ISO-10303-21;\n")

    with pytest.raises(ThumbnailError, match="Null input shape"):
        render_mesh_thumbnail(input_step, tmp_path / "out" / "thumbnail.png")
