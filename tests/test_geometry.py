"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/tests/test_geometry.py
Description: Tests for geometry.py's overhang measurement and the
    GET /models/{id}/overhangs endpoint, on shapes with a known answer.
Inputs: pytest, tests/conftest.py (tmp_output_dir autouse), TestClient.
Outputs: N/A (test module).
Troubleshooting:
    - The "T" is two overlapping boxes joined with trimesh.util.concatenate
      (no boolean), so the plate's whole 900 mm2 underside counts.
"""

from __future__ import annotations

import math

import manifold3d as mf
import pytest
import trimesh
from fastapi.testclient import TestClient

from threedprompt import geometry, main


def _t_shape() -> trimesh.Trimesh:
    """A 10x10x20 column with a 30x30x5 plate on top: the plate's underside (800 mm2) overhangs."""
    column = trimesh.creation.box((10, 10, 20)).apply_translation((0, 0, 10))
    plate = trimesh.creation.box((30, 30, 5)).apply_translation((0, 0, 22.5))
    return trimesh.util.concatenate([column, plate])


def test_t_shape_overhang_area():
    report = geometry.overhang_report(_t_shape())
    # The plate's whole 30x30 underside points straight down (the column's buried top face points up).
    assert report["overhang_area_mm2"] == pytest.approx(900, abs=0.5)
    assert report["overhang_face_count"] == 2


def test_box_on_bed_needs_no_support():
    box = trimesh.creation.box((20, 20, 20)).apply_translation((0, 0, 10))
    assert geometry.overhang_report(box)["overhang_area_mm2"] == 0


def test_angle_threshold():
    # A face leaning out 30 deg from vertical: fine under the usual 45 deg rule, flagged at 20 deg.
    profile = mf.CrossSection([[(20 * math.tan(math.radians(30)), 0), (20, 0), (20, 20), (0, 20)]])
    wedge = mf.Manifold.extrude(profile, 10).rotate((90, 0, 0))  # profile's Y becomes height
    lo, _ = geometry.bbox(wedge)
    mesh = geometry.to_trimesh(wedge.translate((0, 0, -lo[2])))
    assert geometry.overhang_report(mesh, 45)["overhang_area_mm2"] == 0
    assert geometry.overhang_report(mesh, 20)["overhang_area_mm2"] > 0


def test_api_overhangs(tmp_path):
    path = tmp_path / "t.stl"
    _t_shape().export(path)
    client = TestClient(main.app)
    model_id = client.post("/watertight/upload", files={"file": ("t.stl", path.read_bytes())}).json()["model_id"]
    body = client.get(f"/models/{model_id}/overhangs").json()
    assert body["overhang_area_mm2"] == pytest.approx(900, abs=0.5)
    assert 0 < body["overhang_fraction"] < 1
    assert client.get(f"/models/{model_id}/overhangs?angle_deg=95").status_code == 422
