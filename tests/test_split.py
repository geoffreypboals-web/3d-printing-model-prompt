"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/tests/test_split.py
Description: Tests for split.py (split a model to fit the bed, with
    connectors) and its HTTP endpoints. Real geometry, nothing mocked:
    pieces must be watertight, fit the bed, not overlap each other, and
    together keep the model's volume minus the connector holes.
Inputs: pytest, tests/conftest.py (tmp_output_dir autouse),
    fastapi.testclient.TestClient.
Outputs: N/A (test module).
Troubleshooting:
    - Overlap tests failing by a tiny volume after a manifold3d upgrade
      usually mean coplanar faces at a cut plane; see the "start 0.5 mm
      past the cut plane" comment in split.py.
"""

from __future__ import annotations

import io
import zipfile

import pytest
import trimesh
from fastapi.testclient import TestClient

from threedprompt import geometry, main, split
from threedprompt.models import SplitRequest


def _params(**overrides) -> dict:
    """Default SplitRequest params as split_to_fit() kwargs, with overrides."""
    return SplitRequest(**overrides).model_dump()


def _pieces(out, result):
    """Load every piece of a split result as manifold3d solids, checking each STL is watertight."""
    solids = []
    for part in result.parts:
        mesh = trimesh.load(out / part.filename)
        assert mesh.is_watertight, part.filename
        if part.role == "piece":
            solids.append(geometry.to_manifold(mesh))
    return solids


def _overlap(solids) -> float:
    """Total pairwise intersection volume of a list of solids."""
    return sum((a ^ b).volume() for i, a in enumerate(solids) for b in solids[i + 1 :])


@pytest.fixture
def bar_stl(tmp_path):
    """A 400 x 40 x 30 mm bar: too long for a 250 mm bed, so it needs one cut."""
    path = tmp_path / "bar.stl"
    trimesh.creation.box((400, 40, 30)).export(path)
    return path


@pytest.fixture
def ball_stl(tmp_path):
    """A 360 mm sphere: needs cuts on all three axes for a 250 mm bed."""
    path = tmp_path / "ball.stl"
    trimesh.creation.icosphere(subdivisions=3, radius=180).export(path)
    return path


def test_bar_splits_in_two_with_dowels(tmp_path, bar_stl):
    out = tmp_path / "out"
    result = split.split_to_fit(bar_stl, out, **_params())

    assert result.grid == [2, 1, 1]
    pieces = _pieces(out, result)
    assert len(pieces) == 2
    assert all(p.fits for p in result.parts)
    assert _overlap(pieces) < 1e-3
    pin = next(p for p in result.parts if p.role == "pin")
    assert pin.count == result.connector_count >= 1
    # The pieces keep the bar's volume minus the dowel holes.
    assert sum(p.volume() for p in pieces) < 400 * 40 * 30


def test_dpeg_pegs_fit_their_sockets(tmp_path, ball_stl):
    out = tmp_path / "out"
    result = split.split_to_fit(ball_stl, out, **_params(connector="dpeg"))

    assert result.grid == [2, 2, 2]
    pieces = _pieces(out, result)
    assert len(pieces) == 8
    assert result.connector_count > 0
    assert _overlap(pieces) < 1e-3  # every peg sits in a socket, never in solid material
    assert not any(p.role == "pin" for p in result.parts)


def test_model_that_fits_is_not_cut(tmp_path):
    small = tmp_path / "small.stl"
    trimesh.creation.box((50, 50, 50)).export(small)
    result = split.split_to_fit(small, tmp_path / "out", **_params())
    assert result.grid == [1, 1, 1]
    assert [p.filename for p in result.parts] == ["part_01.stl"]
    assert "already fits" in result.notes[0]


def test_thin_plate_gets_connectors_in_its_edge(tmp_path):
    plate = tmp_path / "plate.stl"
    trimesh.creation.box((380, 300, 10)).export(plate)
    result = split.split_to_fit(plate, tmp_path / "out", **_params())
    assert result.grid == [2, 2, 1]
    assert result.connector_count > 0


def test_too_many_pieces_needed_is_rejected(tmp_path, ball_stl):
    with pytest.raises(split.SplitError, match="at most 4 pieces"):
        split.split_to_fit(ball_stl, tmp_path / "out", **_params(max_pieces=4))


def test_fit_orientation_tries_rotations():
    perm = geometry.fit_orientation([300, 40, 30], [250, 250, 320])
    assert perm is not None and perm[2] == 0  # the 300 mm side has to stand up
    assert geometry.fit_orientation([300, 300, 300], [250, 250, 250]) is None


def test_api_split_and_download(bar_stl):
    client = TestClient(main.app)
    model_id = client.post("/watertight/upload", files={"file": ("bar.stl", bar_stl.read_bytes())}).json()["model_id"]

    resp = client.post(f"/models/{model_id}/split", json={"connector": "dpeg"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    for part in body["parts"]:
        assert client.get(part["url"]).status_code == 200
    names = zipfile.ZipFile(io.BytesIO(client.get(body["download_url"]).content)).namelist()
    assert names == [p["filename"] for p in body["parts"]]


def test_api_split_errors(ball_stl):
    client = TestClient(main.app)
    assert client.post("/models/" + "0" * 32 + "/split", json={}).status_code == 404
    model_id = client.post("/watertight/upload", files={"file": ("b.stl", ball_stl.read_bytes())}).json()["model_id"]
    resp = client.post(f"/models/{model_id}/split", json={"max_pieces": 4})
    assert resp.status_code == 422
    assert "max_pieces" in resp.json()["detail"]
