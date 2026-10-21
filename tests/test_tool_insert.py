"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/tests/test_tool_insert.py
Description: Tests for tool_insert.py and gridfinity.py: a synthetic photo
    (white sheet with a 20 mm calibration square and a "tool" rectangle,
    photographed at an angle on a grey table) traces to the right size;
    pockets are the outline plus clearance; trays are single closed
    solids with a floor under every pocket; the API round trip works.
Inputs: None (images and outlines are generated in the tests).
Outputs: Test results; temporary files under pytest's tmp_path.
Troubleshooting:
    - Size off by more than 1 %: check _find_paper() still warps the
      sheet (paper_found) - without it the tilt skews the scale.
    - ImportError for cv2: pip install -r requirements.txt
      (opencv-python-headless).
"""

from __future__ import annotations

import cv2
import manifold3d as mf
import numpy as np
import pytest
import trimesh
from fastapi.testclient import TestClient

from threedprompt import geometry, gridfinity, tool_insert
from threedprompt.main import app
from threedprompt.tool_insert import ToolInsertError

PX_PER_MM = 4.0  # resolution of the flat sheet before it is "photographed"
SHEET_MM = (297, 210)  # A4 landscape


def _photo(tilt: bool = True, square: bool = True) -> bytes:
    """A4 sheet with a 20 mm square and a 120 x 30 mm tool, warped onto a grey table as if shot at an angle."""
    w, h = int(SHEET_MM[0] * PX_PER_MM), int(SHEET_MM[1] * PX_PER_MM)
    sheet = np.full((h, w, 3), 245, np.uint8)
    if square:
        cv2.rectangle(
            sheet,
            (int(20 * PX_PER_MM), int(20 * PX_PER_MM)),
            (int(40 * PX_PER_MM) - 1, int(40 * PX_PER_MM) - 1),
            (20, 20, 20),
            -1,
        )
    cv2.rectangle(
        sheet,
        (int(100 * PX_PER_MM), int(100 * PX_PER_MM)),
        (int(220 * PX_PER_MM) - 1, int(130 * PX_PER_MM) - 1),
        (60, 50, 40),
        -1,
    )
    table = (w + 400, h + 400)
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    if tilt:
        dst = np.float32([[260, 180], [w + 120, 230], [w + 200, h + 330], [150, h + 260]])
    else:
        dst = src + 200
    photo = cv2.warpPerspective(sheet, cv2.getPerspectiveTransform(src, dst), table, borderValue=(90, 95, 100))
    ok, buf = cv2.imencode(".png", photo)
    assert ok
    return buf.tobytes()


def _extent(points: list[list[float]]) -> tuple[float, float]:
    """Width and height of a point list's bounding box."""
    pts = np.asarray(points)
    return float(np.ptp(pts[:, 0])), float(np.ptp(pts[:, 1]))


def test_trace_tilted_photo_sizes_tool_within_one_percent() -> None:
    """The traced tool is 120 x 30 mm within 1 % after perspective correction."""
    result = tool_insert.trace(_photo(), square_mm=20)
    assert result.paper_found
    assert result.square_px is not None
    assert len(result.tools) == 1
    w, h = _extent(result.tools[0].outline_mm)
    assert w == pytest.approx(120, rel=0.01)
    assert h == pytest.approx(30, rel=0.02)  # a short side: 2 % is still 0.6 mm
    assert result.preview.startswith("data:image/jpeg;base64,")


def test_trace_without_square_asks_for_manual_scale() -> None:
    """No calibration square: outlines come back in pixels only, with a note."""
    result = tool_insert.trace(_photo(tilt=False, square=False), square_mm=20)
    assert result.mm_per_px is None
    assert result.tools and result.tools[0].outline_mm is None
    assert any("square not found" in n for n in result.notes)


def test_trace_known_scale_skips_square() -> None:
    """A given mm_per_px is used as-is (the square is then traced as a shape)."""
    result = tool_insert.trace(_photo(tilt=False), square_mm=20, mm_per_px=0.5)
    assert result.mm_per_px == 0.5
    assert len(result.tools) == 2


def test_trace_rejects_non_image() -> None:
    """Bytes that aren't an image raise a readable error."""
    with pytest.raises(ToolInsertError, match="could not read"):
        tool_insert.trace(b"not an image", square_mm=20)


_RECT = [[0, 0], [120, 0], [120, 30], [0, 30]]
_FIT = {
    "clearance_mm": 1.0,
    "depth_mm": 15.0,
    "floor_mm": 2.0,
    "wall_mm": 4.0,
    "corner_radius_mm": 4.0,
    "chamfer_mm": 0.0,
}


def _load(path) -> trimesh.Trimesh:
    """Load an STL written by make_insert."""
    return trimesh.load(path, force="mesh")


def test_fit_tray_pocket_is_outline_plus_clearance(tmp_path) -> None:
    """Fit tray: size = outline + 2*(clearance + wall); pocket area = offset outline; floor intact."""
    result = tool_insert.make_insert(tmp_path, [{"outline_mm": _RECT, "notches": []}], tray="fit", **_FIT)
    assert result.size_mm == pytest.approx([120 + 2 * 5, 30 + 2 * 5, 17], abs=0.05)
    # Offset rectangle with round corners: (w+2c)(h+2c) - (4 - pi) c^2.
    assert result.pocket_area_mm2 == pytest.approx(122 * 32 - (4 - np.pi), rel=0.002)
    mesh = _load(tmp_path / tool_insert.OUTPUT_FILENAME)
    assert mesh.is_watertight
    tray_volume = (130 * 40 - (4 - np.pi) * 16) * 17
    assert mesh.volume == pytest.approx(tray_volume - result.pocket_area_mm2 * 15, rel=0.002)
    # The pocket stops at the floor: solid all the way across below z=2, pocket cut above it.
    solid = geometry.to_manifold(mesh)
    assert solid.slice(1.9).area() == pytest.approx(130 * 40 - (4 - np.pi) * 16, rel=0.002)
    assert solid.slice(2.1).area() == pytest.approx(130 * 40 - (4 - np.pi) * 16 - result.pocket_area_mm2, rel=0.002)


def test_notch_and_chamfer_grow_the_pocket(tmp_path) -> None:
    """A finger notch adds area; a chamfer widens the top of the pocket."""
    plain = tool_insert.make_insert(tmp_path, [{"outline_mm": _RECT}], tray="fit", **_FIT)
    notched = tool_insert.make_insert(
        tmp_path, [{"outline_mm": _RECT, "notches": [{"x": 60, "y": 30, "d": 20}]}], tray="fit", **_FIT
    )
    assert notched.pocket_area_mm2 > plain.pocket_area_mm2 + 100
    chamfered = tool_insert.make_insert(tmp_path, [{"outline_mm": _RECT}], tray="fit", **{**_FIT, "chamfer_mm": 1.5})
    assert chamfered.size_mm[0] == pytest.approx(plain.size_mm[0] + 3, abs=0.05)
    assert _load(tmp_path / tool_insert.OUTPUT_FILENAME).is_watertight


def test_gridfinity_tray_units_and_feet(tmp_path) -> None:
    """A 120 x 30 tool, 14 mm deep, needs a 4 x 1 Gridfinity insert 3 units tall (one closed solid)."""
    params = {**_FIT, "depth_mm": 14.0}
    result = tool_insert.make_insert(tmp_path, [{"outline_mm": _RECT}], tray="gridfinity", **params)
    assert result.grid_units == [4, 1, 3]
    assert result.size_mm == pytest.approx([4 * 42 - 0.5, 41.5, 21], abs=0.05)
    assert result.floor_mm == pytest.approx(21 - 14 - gridfinity.FOOT_HEIGHT_MM, abs=0.01)
    # One more mm of depth no longer fits under 21 mm: 4 units.
    deeper = tool_insert.make_insert(tmp_path, [{"outline_mm": _RECT}], tray="gridfinity", **_FIT)
    assert deeper.grid_units == [4, 1, 4]
    mesh = _load(tmp_path / tool_insert.OUTPUT_FILENAME)
    assert mesh.is_watertight and mesh.body_count == 1


def test_gridfinity_foot_profile() -> None:
    """One foot is 41.5 mm at the top, 35.6 mm at the bottom and 4.75 mm tall."""
    foot = gridfinity.foot()
    lo, hi = np.array(foot.bounding_box()[:3]), np.array(foot.bounding_box()[3:])
    assert (hi - lo) == pytest.approx([41.5, 41.5, 4.75], abs=0.01)
    bottom = foot.slice(0.05).bounds()
    assert bottom[2] - bottom[0] == pytest.approx(41.5 - 2 * 2.95 + 0.1, abs=0.05)
    assert foot.status() == mf.Error.NoError


def test_bad_outline_is_rejected(tmp_path) -> None:
    """A degenerate outline raises a readable error."""
    with pytest.raises(ToolInsertError):
        tool_insert.make_insert(tmp_path, [{"outline_mm": [[0, 0], [10, 0], [20, 0]]}], tray="fit", **_FIT)


def test_api_trace_and_build() -> None:
    """POST /tool-insert/trace then POST /tool-insert, and the STL downloads."""
    client = TestClient(app)
    resp = client.post(
        "/tool-insert/trace", files={"file": ("tools.png", _photo(), "image/png")}, data={"square_mm": "20"}
    )
    assert resp.status_code == 200, resp.text
    outline = resp.json()["tools"][0]["outline_mm"]
    resp = client.post("/tool-insert", json={"tools": [{"outline_mm": outline, "notches": [{"x": 0, "y": 0}]}]})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    stl = client.get(body["parts"][0]["url"])
    assert stl.status_code == 200 and len(stl.content) > 1000
    assert client.post("/tool-insert", json={"tools": []}).status_code == 422
    bad = client.post("/tool-insert/trace", files={"file": ("x.png", b"nope", "image/png")})
    assert bad.status_code == 422
