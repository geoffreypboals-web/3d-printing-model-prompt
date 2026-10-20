"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/tests/test_templates.py
Description: Tests for the rule-based templates (docs/print-tools-plan.md
    item 4): Gridfinity bin sizes and options, gear pairs that mesh (no
    overlap in place, interference once turned) at the right centre
    distance, lithophane panels that are closed solids with the right
    thickness range, and QR tags whose raised code decodes back to the
    input. Plus the /templates API routes.
Inputs: None (images and content are generated in the tests).
Outputs: Test results; temporary files under pytest's tmp_path.
Troubleshooting:
    - QR decode test fails: cv2.QRCodeDetector needs a white border around
      the rasterised code; check _rasterise_code() still pads the image.
    - Gear interference test fails after a profile change: the pair is
      either not in mesh (no overlap even when turned) or overlapping in
      place (backlash sign).
"""

from __future__ import annotations

import json

import cv2
import numpy as np
import pytest
import trimesh
from fastapi.testclient import TestClient

from threedprompt import gear, geometry, gridfinity, lithophane, qr_tag, storage, terrain
from threedprompt.geometry import TemplateError
from threedprompt.main import app

_BIN = {
    "units_x": 2,
    "units_y": 1,
    "height_units": 6,
    "compartments_x": 1,
    "compartments_y": 1,
    "wall_mm": 1.2,
    "divider_mm": 1.2,
    "floor_mm": 1.2,
    "stacking_lip": True,
    "label_tab": False,
    "magnet_holes": False,
}


def _mesh(path) -> trimesh.Trimesh:
    """Load one STL as a single mesh."""
    return trimesh.load(path, force="mesh")


def test_gridfinity_bin_sizes(tmp_path) -> None:
    """A 2 x 1 x 6 bin is 83.5 x 41.5 mm, 42 mm tall plus a 4.75 mm lip, and one closed solid."""
    result = gridfinity.make_bin(tmp_path, **_BIN)
    assert result.parts[0].size_mm == pytest.approx([83.5, 41.5, 42 + 4.75], abs=0.02)
    mesh = _mesh(tmp_path / gridfinity.BIN_FILENAME)
    assert mesh.is_watertight and mesh.body_count == 1
    no_lip = gridfinity.make_bin(tmp_path, **{**_BIN, "stacking_lip": False})
    assert no_lip.parts[0].size_mm[2] == pytest.approx(42, abs=0.02)


def test_gridfinity_bin_options_stay_closed(tmp_path) -> None:
    """Compartments, label tabs and magnet holes together still give one closed solid."""
    opts = {**_BIN, "compartments_x": 3, "compartments_y": 2, "label_tab": True, "magnet_holes": True}
    result = gridfinity.make_bin(tmp_path, **opts)
    mesh = _mesh(tmp_path / gridfinity.BIN_FILENAME)
    assert mesh.is_watertight and mesh.body_count == 1
    cx, cy, _ = result.report["compartment_mm"]
    assert cx == pytest.approx((83.5 - 2 * 1.2 - 2 * 1.2) / 3, abs=0.01)
    assert cy == pytest.approx((41.5 - 2 * 1.2 - 1.2) / 2, abs=0.01)
    # Magnet holes: 8 of them, each 6.5 mm wide, in the underside.
    plain = gridfinity.make_bin(tmp_path, **{**_BIN, "compartments_x": 3, "compartments_y": 2, "label_tab": True})
    hole = np.pi * (gridfinity.MAGNET_DIAMETER_MM / 2) ** 2 * gridfinity.MAGNET_DEPTH_MM
    assert plain.report["volume_cm3"] - result.report["volume_cm3"] == pytest.approx(8 * hole / 1000, abs=0.15)


def test_gridfinity_bin_too_many_compartments(tmp_path) -> None:
    """Compartments narrower than 5 mm are refused."""
    with pytest.raises(TemplateError, match="compartments"):
        gridfinity.make_bin(tmp_path, **{**_BIN, "units_x": 1, "compartments_x": 10})


_GEAR = {
    "module": 2.0,
    "teeth": 20,
    "pressure_angle_deg": 20.0,
    "backlash_mm": 0.15,
    "face_width_mm": 8.0,
    "helix_angle_deg": 0.0,
    "bore": "round",
    "bore_diameter_mm": 5.0,
    "mate_teeth": 30,
    "rack_teeth": 10,
    "rack_height_mm": 6.0,
}


@pytest.mark.parametrize(("teeth", "mate"), [(20, 30), (13, 17), (21, 12)])
def test_gear_pair_meshes(tmp_path, teeth, mate) -> None:
    """Gear, mate and rack sit in mesh with no overlap, and the mate interferes when turned 2 deg."""
    result = gear.make_gears(tmp_path, **{**_GEAR, "teeth": teeth, "mate_teeth": mate})
    a = result.report["pair"]["centre_distance_mm"]
    assert a == pytest.approx(2.0 * (teeth + mate) / 2)
    g, m, r = (geometry.to_manifold(_mesh(tmp_path / p.filename)) for p in result.parts)
    assert (g ^ m).volume() < 1e-3
    assert (g ^ r).volume() < 1e-3
    turned = m.translate((-a, 0, 0)).rotate((0, 0, 2)).translate((a, 0, 0))
    assert (g ^ turned).volume() > 1.0
    assert result.parts[0].size_mm[0] == pytest.approx(2.0 * teeth + 4, abs=0.4)  # tip diameter
    assert 1.2 < result.report["pair"]["contact_ratio"] < 2.0
    for p in result.parts:
        assert _mesh(tmp_path / p.filename).is_watertight


def test_gear_bore_too_big(tmp_path) -> None:
    """A bore that eats into the tooth roots is refused."""
    with pytest.raises(TemplateError, match="bore"):
        gear.make_gears(tmp_path, **{**_GEAR, "bore_diameter_mm": 36.0})


def test_helical_gear_is_twisted(tmp_path) -> None:
    """A helical gear has the same outline size but its top face is turned against the bottom."""
    result = gear.make_gears(tmp_path, **{**_GEAR, "helix_angle_deg": 20.0, "mate_teeth": None, "rack_teeth": None})
    solid = geometry.to_manifold(_mesh(tmp_path / "gear.stl"))
    bottom, top = solid.slice(0.1), solid.slice(7.9)
    assert (bottom - top).area() > 5  # the teeth have moved round
    assert any("helix" in n.lower() for n in result.notes) is False  # no mate, no pairing note


def _image() -> bytes:
    """80 x 60 white PNG with a black disc in the middle."""
    img = np.full((60, 80), 255, np.uint8)
    cv2.circle(img, (40, 30), 15, 0, -1)
    return cv2.imencode(".png", img)[1].tobytes()


_LITHO = {
    "shape": "flat",
    "width_mm": 80.0,
    "min_thickness_mm": 0.8,
    "max_thickness_mm": 3.0,
    "resolution_mm": 0.5,
    "frame_mm": 0.0,
    "curve_deg": 120.0,
}


@pytest.mark.parametrize("shape", ["flat", "curved", "cylinder"])
@pytest.mark.parametrize("frame", [0.0, 3.0])
def test_lithophane_is_closed(tmp_path, shape, frame) -> None:
    """Every shape, with or without a frame, is one closed, consistently wound solid."""
    lithophane.make_lithophane(_image(), tmp_path, **{**_LITHO, "shape": shape, "frame_mm": frame})
    mesh = _mesh(tmp_path / lithophane.OUTPUT_FILENAME)
    assert mesh.is_watertight and mesh.is_winding_consistent and mesh.volume > 0


def test_flat_lithophane_thickness_range(tmp_path) -> None:
    """Flat panel: exact width, white = min thickness, black = max thickness."""
    result = lithophane.make_lithophane(_image(), tmp_path, **_LITHO)
    w, depth, h = result.parts[0].size_mm
    assert w == pytest.approx(80, abs=0.01)
    assert depth == pytest.approx(3.0, abs=0.01)
    mesh = _mesh(tmp_path / lithophane.OUTPUT_FILENAME)
    front = mesh.vertices[mesh.vertices[:, 1] < -1e-6]
    assert -front[:, 1].min() == pytest.approx(3.0, abs=0.05)
    assert -front[:, 1].max() == pytest.approx(0.8, abs=0.05)


def test_lithophane_rejects_bad_input(tmp_path) -> None:
    """Unreadable images and inverted thicknesses are refused."""
    with pytest.raises(TemplateError, match="read"):
        lithophane.make_lithophane(b"nope", tmp_path, **_LITHO)
    with pytest.raises(TemplateError, match="max_thickness"):
        lithophane.make_lithophane(_image(), tmp_path, **{**_LITHO, "max_thickness_mm": 0.5})


_QR = {
    "kind": "text",
    "text": "https://example.com/hello",
    "ssid": "",
    "password": "",
    "security": "WPA",
    "error_correction": "m",
    "module_mm": 2.0,
    "quiet_zone": 2,
    "plate_mm": 3.0,
    "code_height_mm": 1.0,
    "corner_radius_mm": 3.0,
    "keyring_hole": True,
    "nfc_pocket": True,
    "nfc_diameter_mm": 25.0,
}


def _rasterise_code(path, z: float, px_per_mm: float = 10) -> np.ndarray:
    """Top view of the raised code: black where the code is, white elsewhere, with a margin."""
    section = geometry.to_manifold(_mesh(path)).slice(z)
    lo_x, lo_y, hi_x, hi_y = section.bounds()
    margin = 30
    w = int((hi_x - lo_x) * px_per_mm) + 2 * margin
    h = int((hi_y - lo_y) * px_per_mm) + 2 * margin
    img = np.full((h, w), 255, np.uint8)
    polys = []
    for poly in section.to_polygons():
        p = np.asarray(poly)
        x = (p[:, 0] - lo_x) * px_per_mm + margin
        y = h - ((p[:, 1] - lo_y) * px_per_mm + margin)
        polys.append(np.column_stack([x, y]).round().astype(np.int32))
    cv2.fillPoly(img, polys, 0)
    return img


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ({"text": "https://example.com/hello"}, "https://example.com/hello"),
        ({"kind": "wifi", "ssid": "Workshop", "password": "example-pass"}, "WIFI:"),
    ],
)
def test_qr_tag_decodes(tmp_path, content, expected) -> None:
    """The raised code, seen from above, decodes back to the content; all three STLs are closed solids."""
    result = qr_tag.make_qr_tag(tmp_path, **{**_QR, **content})
    for p in result.parts:
        assert _mesh(tmp_path / p.filename).is_watertight
    decoded, _, _ = cv2.QRCodeDetector().detectAndDecode(_rasterise_code(tmp_path / qr_tag.CODE_FILENAME, 3.5))
    assert decoded.startswith(expected)
    side = (result.report["modules"] + 4) * 2.0
    assert result.report["plate_mm"][:2] == pytest.approx([side, side])


def test_qr_tag_needs_content(tmp_path) -> None:
    """Empty text is refused with a readable message."""
    with pytest.raises(TemplateError, match="enter"):
        qr_tag.make_qr_tag(tmp_path, **{**_QR, "text": ""})


def test_template_api_routes() -> None:
    """Each /templates route builds parts that download; bad settings give 422; the Wi-Fi password isn't stored."""
    client = TestClient(app)
    for route, body in [
        ("/templates/gridfinity-bin", {"units_x": 1, "units_y": 1, "height_units": 3}),
        ("/templates/gear", {"teeth": 16, "mate_teeth": 24}),
        ("/templates/minis-bin", {"units_x": 1, "units_y": 1, "base_diameter_mm": 32}),
        ("/templates/terrain", {"width_mm": 60, "depth_mm": 60, "resolution_mm": 2}),
        ("/templates/qr-tag", {"kind": "wifi", "ssid": "Shop", "password": "example-pass"}),
    ]:
        resp = client.post(route, json=body)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        for part in data["parts"]:
            assert client.get(part["url"]).status_code == 200
    spec = storage.load_spec(data["model_id"])
    assert spec["method"] == "qr_tag" and "example-pass" not in json.dumps(spec)
    resp = client.post(
        "/templates/lithophane", files={"file": ("x.png", _image(), "image/png")}, data={"resolution_mm": "0.5"}
    )
    assert resp.status_code == 200, resp.text
    assert client.post("/templates/gear", json={"teeth": 3}).status_code == 422
    too_big = {"units_x": 1, "units_y": 1, "base_diameter_mm": 60}
    assert client.post("/templates/minis-bin", json=too_big).status_code == 422
    assert client.post("/templates/terrain", json={"style": "volcano"}).status_code == 422
    assert client.post("/templates/qr-tag", json={"text": ""}).status_code == 422
    bad = client.post("/templates/lithophane", files={"file": ("x.png", b"no", "image/png")})
    assert bad.status_code == 422


_MINIS = {
    "units_x": 2,
    "units_y": 2,
    "height_units": 8,
    "base_diameter_mm": 25.0,
    "base_clearance_mm": 0.6,
    "socket_depth_mm": 3.5,
    "socket_gap_mm": 1.6,
    "wall_mm": 1.2,
    "magnet_diameter_mm": 5.2,
    "magnet_depth_mm": 2.2,
    "stacking_lip": True,
}


def test_minis_bin_sockets(tmp_path) -> None:
    """2 x 2 units hold 3 x 3 sockets of 25.6 mm with 5.2 mm magnet holes under them; one closed solid."""
    result = gridfinity.make_minis_bin(tmp_path, **_MINIS)
    assert result.report["sockets"] == [3, 3]
    assert result.parts[0].size_mm == pytest.approx([83.5, 83.5, 56 + 4.75], abs=0.02)
    mesh = _mesh(tmp_path / gridfinity.MINIS_BIN_FILENAME)
    assert mesh.is_watertight and mesh.body_count == 1
    # Feet 4.75 + 0.8 floor + 2.2 magnet = socket bottom at 7.75; slice inside the sockets and the magnet holes.
    socket_z, magnet_z = 7.75 + 1.75, 7.75 - 1.1

    def holes(z: float, d: float) -> int:
        loops = mesh.section(plane_origin=[0, 0, z], plane_normal=[0, 0, 1]).discrete
        return sum(1 for loop in loops if abs(np.ptp(loop[:, 0]) - d) < 0.3 and abs(np.ptp(loop[:, 1]) - d) < 0.3)

    assert holes(socket_z, 25.6) == 9 and holes(magnet_z, 5.2) == 9
    with pytest.raises(TemplateError):
        gridfinity.make_minis_bin(tmp_path, **{**_MINIS, "units_x": 1, "base_diameter_mm": 50})


_TERRAIN = {
    "style": "hills",
    "width_mm": 100.0,
    "depth_mm": 80.0,
    "base_mm": 4.0,
    "max_height_mm": 20.0,
    "feature_mm": 40.0,
    "roughness": 0.45,
    "terrace_mm": 0.0,
    "edge_fade_mm": 10.0,
    "resolution_mm": 2.0,
    "seed": 3,
    "magnet_holes": True,
    "magnet_diameter_mm": 6.2,
    "magnet_depth_mm": 2.2,
}


@pytest.mark.parametrize("style", ["hills", "ridges", "craters", "mesa"])
def test_terrain_tile_is_a_joinable_solid(tmp_path, style) -> None:
    """Every style is one closed solid of the asked size, with every edge at the base height so tiles join."""
    result = terrain.make_terrain(tmp_path, **{**_TERRAIN, "style": style})
    mesh = _mesh(tmp_path / terrain.TERRAIN_FILENAME)
    assert mesh.is_watertight and mesh.body_count == 1
    assert result.parts[0].size_mm[:2] == pytest.approx([100, 80], abs=0.01)
    assert 4 < result.report["highest_mm"] <= 24.01
    edge = mesh.vertices[(mesh.vertices[:, 0] < 0.01) | (mesh.vertices[:, 1] > 79.99)]
    assert edge[:, 2].max() == pytest.approx(4.0, abs=0.01)


def test_terrain_seed_terraces_and_magnets(tmp_path) -> None:
    """Same seed = same tile, another seed differs; terraces only leave whole steps; magnets can't go through."""
    field = {"style": "hills", "nx": 40, "ny": 30, "width_mm": 100, "depth_mm": 80, "roughness": 0.45, "feature_mm": 40}
    a, b, c = (terrain.height_field(**field, seed=s) for s in (3, 3, 4))
    assert np.array_equal(a, b) and not np.allclose(a, c)
    terrain.make_terrain(tmp_path, **{**_TERRAIN, "terrace_mm": 5.0, "magnet_holes": False})
    z = np.unique(np.round(_mesh(tmp_path / terrain.TERRAIN_FILENAME).vertices[:, 2], 3))
    assert set(z) <= {0.0, 4.0, 9.0, 14.0, 19.0, 24.0}
    smooth = terrain.make_terrain(tmp_path, **_TERRAIN)
    stepped = terrain.make_terrain(tmp_path, **{**_TERRAIN, "terrace_mm": 5.0})
    assert stepped.report["standable_percent"] > smooth.report["standable_percent"]
    with pytest.raises(TemplateError):
        terrain.make_terrain(tmp_path, **{**_TERRAIN, "base_mm": 2.0, "magnet_depth_mm": 2.2})
    with pytest.raises(TemplateError):
        terrain.make_terrain(tmp_path, **{**_TERRAIN, "style": "volcano"})
