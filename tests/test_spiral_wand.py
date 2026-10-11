"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/tests/test_spiral_wand.py
Description: Tests for spiral_wand.py (twisted wand + figurine riders) and
    its HTTP endpoints in main.py. These run the real geometry code -
    manifold3d is in-process and fast, so nothing is mocked - and check
    the properties that matter physically: every part is a closed solid,
    sections fit the print height, a rider threads onto the rod with no
    overlap (and genuinely collides when turned out of phase), and the
    section joints / cap don't overlap.
Inputs: pytest, tests/conftest.py fixtures (tmp_output_dir autouse,
    cube_missing_one_triangle_stl), fastapi.testclient.TestClient.
Outputs: N/A (test module).
Troubleshooting:
    - An interference test failing by a tiny volume (< 0.01 mm3) after a
      manifold3d upgrade usually means its triangulation changed, not
      that the fit broke - check the clearance math in _twisted() first.
"""

from __future__ import annotations

import io
import zipfile

import pytest
import trimesh
from fastapi.testclient import TestClient

from threedprompt import main, spiral_wand
from threedprompt.models import SpiralRiderRequest, SpiralWandRequest


def _wand_params(**overrides) -> dict:
    """Default SpiralWandRequest params as make_wand() kwargs, with overrides."""
    params = SpiralWandRequest(**overrides).model_dump()
    params.pop("cap_model_id")
    return params


def _rider_params(**overrides) -> dict:
    """Default SpiralRiderRequest params as make_rider() kwargs, with overrides."""
    return SpiralRiderRequest(**overrides).model_dump()


def _load(path) -> spiral_wand.mf.Manifold:
    """Load an STL written by spiral_wand.py back as a manifold3d solid."""
    return spiral_wand._to_manifold(trimesh.load(path))


@pytest.fixture
def figurine_stl(tmp_path):
    """A Y-up 'snowman' made of overlapping, separately closed shells - like many downloaded figurines."""
    parts = [
        trimesh.creation.icosphere(subdivisions=3, radius=14).apply_translation([0, 14, 0]),
        trimesh.creation.icosphere(subdivisions=3, radius=11).apply_translation([0, 34, 0]),
        trimesh.creation.icosphere(subdivisions=3, radius=9).apply_translation([0, 49, 0]),
    ]
    path = tmp_path / "snowman.stl"
    trimesh.util.concatenate(parts).export(path)
    return path


@pytest.fixture
def client():
    return TestClient(main.app)


# --- make_wand ---


def test_wand_splits_to_fit_print_height(tmp_path):
    result = spiral_wand.make_wand(tmp_path, **_wand_params(total_length_mm=300, max_print_height_mm=250))

    names = [p.filename for p in result.parts]
    assert names == ["wand_section1.stl", "wand_section2.stl", "wand_cap.stl"]
    for part in result.parts:
        mesh = trimesh.load(tmp_path / part.filename)
        assert mesh.is_watertight
        assert part.size_mm[2] <= 250
    assert result.total_length_mm == 300
    assert result.spiral_start_z_mm == 90 + 4 + 25


def test_short_wand_is_one_body(tmp_path):
    result = spiral_wand.make_wand(tmp_path, **_wand_params(total_length_mm=200, cap_style="disc"))
    assert [p.filename for p in result.parts] == ["wand_body.stl", "wand_cap.stl"]


def test_spiral_length_sets_total(tmp_path):
    result = spiral_wand.make_wand(tmp_path, **_wand_params(spiral_length_mm=100, cap_style="none"))
    assert result.total_length_mm == 90 + 4 + 25 + 100
    assert [p.role for p in result.parts] == ["wand"]


def test_sections_and_cap_do_not_overlap(tmp_path):
    result = spiral_wand.make_wand(tmp_path, **_wand_params(total_length_mm=400, max_print_height_mm=180))
    placed = [_load(tmp_path / p.filename).translate((0, 0, p.z_offset_mm)) for p in result.parts]
    assert len(placed) == 4  # three sections + cap
    for a, b in zip(placed, placed[1:], strict=False):
        assert (a ^ b).volume() < 0.01


def test_cap_smaller_than_bore_is_rejected(tmp_path):
    with pytest.raises(spiral_wand.SpiralWandError, match="cap_diameter_mm"):
        spiral_wand.make_wand(tmp_path, **_wand_params(cap_diameter_mm=12))


def test_no_room_for_spiral_is_rejected(tmp_path):
    with pytest.raises(spiral_wand.SpiralWandError, match="left for the spiral"):
        spiral_wand.make_wand(tmp_path, **_wand_params(total_length_mm=140))


def test_shallow_helix_adds_a_note(tmp_path):
    result = spiral_wand.make_wand(tmp_path, **_wand_params(pitch_mm=40, total_length_mm=200))
    assert any("Helix angle" in n for n in result.notes)


# --- make_rider ---


@pytest.mark.parametrize("handed", ["right", "left"])
def test_rider_threads_onto_rod_without_overlap(tmp_path, figurine_stl, handed):
    params = _rider_params(handed=handed, up_axis="y", height_mm=60)
    result = spiral_wand.make_rider(figurine_stl, tmp_path, **params)
    rider = _load(tmp_path / "rider.stl")
    assert trimesh.load(tmp_path / "rider.stl").is_watertight
    assert result.rider_height_mm == pytest.approx(60, abs=0.5)

    rod = spiral_wand._rod_settings(params)
    rate = spiral_wand.twist_rate_deg_per_mm(rod)
    shaft = spiral_wand._twisted(rod, 200, 100)
    for z in (110.0, 163.7, 220.0):
        placed = rider.rotate((0, 0, rate * z)).translate((0, 0, z))
        assert (placed ^ shaft).volume() < 0.01
    # Turned out of phase it must collide - proves the bore is keyed to the spiral.
    wrong = rider.rotate((0, 0, rate * 150 + 30)).translate((0, 0, 150))
    assert (wrong ^ shaft).volume() > 1.0


def test_rider_base_and_slim_note(tmp_path, figurine_stl):
    result = spiral_wand.make_rider(
        figurine_stl, tmp_path, **_rider_params(up_axis="y", height_mm=40, base_diameter_mm=35)
    )
    assert result.rider_height_mm == pytest.approx(40 + 3, abs=0.5)
    assert any("sleeve shows" in n for n in result.notes)


def test_rider_rejects_open_mesh(tmp_path, cube_missing_one_triangle_stl, monkeypatch):
    # fill_holes would close this one-triangle gap; disable it to simulate an unrepairable mesh.
    monkeypatch.setattr(spiral_wand.trimesh.repair, "fill_holes", lambda mesh: None)
    with pytest.raises(spiral_wand.SpiralWandError, match="watertight checker"):
        spiral_wand.make_rider(cube_missing_one_triangle_stl, tmp_path, **_rider_params())


def test_rider_rejects_unsupported_format(tmp_path):
    bad = tmp_path / "model.3dm"
    bad.write_bytes(b"not a mesh")
    with pytest.raises(spiral_wand.SpiralWandError, match="unsupported mesh format"):
        spiral_wand.make_rider(bad, tmp_path / "out", **_rider_params())


# --- HTTP API ---


def test_api_build_wand_and_download_parts(client):
    resp = client.post("/spiral-wand", json={"total_length_mm": 220, "handle_gap_mm": 40})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["spiral_turns"] > 0
    for part in body["parts"]:
        stl = client.get(part["url"])
        assert stl.status_code == 200
        assert len(stl.content) > 1000
    archive = client.get(body["download_url"])
    assert archive.status_code == 200
    names = zipfile.ZipFile(io.BytesIO(archive.content)).namelist()
    assert names == [p["filename"] for p in body["parts"]]


def test_api_rider_from_uploaded_model(client, figurine_stl):
    upload = client.post("/watertight/upload", files={"file": ("snowman.stl", figurine_stl.read_bytes())})
    model_id = upload.json()["model_id"]

    resp = client.post(f"/models/{model_id}/spiral-wand/rider", json={"up_axis": "y", "height_mm": 55})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [p["filename"] for p in body["parts"]] == ["rider.stl"]
    assert client.get(body["parts"][0]["url"]).status_code == 200


def test_api_custom_cap_from_model(client, figurine_stl):
    upload = client.post("/watertight/upload", files={"file": ("topper.stl", figurine_stl.read_bytes())})
    cap_id = upload.json()["model_id"]
    resp = client.post("/spiral-wand", json={"total_length_mm": 220, "cap_model_id": cap_id})
    assert resp.status_code == 200, resp.text
    cap = next(p for p in resp.json()["parts"] if p["role"] == "cap")
    # The topper is scaled so its wider footprint side matches cap_diameter_mm.
    assert max(cap["size_mm"][:2]) == pytest.approx(26, abs=0.5)


def test_api_bad_dimensions_are_422(client):
    resp = client.post("/spiral-wand", json={"total_length_mm": 140})
    assert resp.status_code == 422
    assert "spiral" in resp.json()["detail"]


def test_api_unknown_model_and_bad_part_names_are_404(client):
    assert client.post("/models/" + "0" * 32 + "/spiral-wand/rider", json={}).status_code == 404
    assert client.post("/models/not-a-model-id/spiral-wand/rider", json={}).status_code == 404
    built = client.post("/spiral-wand", json={"total_length_mm": 200}).json()
    model_id = built["model_id"]
    assert client.get(f"/models/{model_id}/parts/spec.json").status_code == 404
    assert client.get(f"/models/{model_id}/parts/wand_section9.stl").status_code == 404


def test_api_rider_rejects_unusable_format(client):
    upload = client.post("/watertight/upload", files={"file": ("thing.3dm", b"not really rhino")})
    model_id = upload.json()["model_id"]
    resp = client.post(f"/models/{model_id}/spiral-wand/rider", json={})
    assert resp.status_code == 422
    assert "supported format" in resp.json()["detail"]
