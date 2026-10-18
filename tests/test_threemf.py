"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/tests/test_threemf.py
Description: Tests for the multi-colour 3MF export (threemf.py and
    POST /models/{id}/export-3mf):
      - the file opens in lib3mf (the 3MF consortium's reference library)
        with one mesh per part, a colour group each and the build items;
      - "orca" layout: an assembly object whose model_settings.config
        <part> ids are its component object ids, each with its filament
        slot - the shape OrcaSlicer's bbs_3mf.cpp loader expects;
      - "prusa" layout: one mesh with per-part triangle ranges in
        Slic3r_PE_model.config; when PrusaSlicer is installed it is sliced
        for real and must switch filament exactly where the top part starts;
      - geometry round-trips; a QR tag exports white plate + black code.
Inputs: None (meshes are generated in the tests).
Outputs: Test results; temporary files under pytest's tmp_path.
Troubleshooting:
    - ImportError lib3mf: pip install -r requirements-dev.txt (lib3mf is a
      test-only dependency; the service writes plain XML).
    - test_prusaslicer_switches_filament is skipped: install PrusaSlicer
      (apt install prusa-slicer) to run it.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
import zipfile

import lib3mf
import numpy as np
import pytest
import trimesh
from fastapi.testclient import TestClient

from threedprompt import threemf
from threedprompt.geometry import TemplateError
from threedprompt.main import app

_NS = {"m": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}


def _parts() -> list[dict]:
    """A 3 mm plate and a smaller 2 mm block sitting on it, in two colours."""
    plate = trimesh.creation.box((40, 40, 3))
    plate.apply_translation((0, 0, 1.5))
    block = trimesh.creation.box((10, 10, 2))
    block.apply_translation((5, 5, 4))
    return [
        {"name": "plate", "mesh": plate, "color": "#f2f2f2", "extruder": 1},
        {"name": "block", "mesh": block, "color": "#1C1C1F", "extruder": 3},
    ]


def _read(path) -> lib3mf.Model:
    """Open a 3MF with lib3mf."""
    model = lib3mf.get_wrapper().CreateModel()
    model.QueryReader("3mf").ReadFromFile(str(path))
    return model


def _xml(path, name: str) -> ET.Element:
    """Parse one XML file from inside a 3MF."""
    with zipfile.ZipFile(path) as zf:
        return ET.fromstring(zf.read(name))


def test_orca_layout_opens_in_lib3mf(tmp_path) -> None:
    """Orca layout: two mesh objects with their own colour group, one components object in the build."""
    out = tmp_path / "x.3mf"
    summary = threemf.write_3mf(out, _parts(), assembled=True, slicer="orca")
    assert summary["objects"] == 1 and summary["parts"] == 2
    model = _read(out)
    meshes = model.GetMeshObjects()
    names, tris = [], []
    while meshes.MoveNext():
        mesh = meshes.GetCurrentMeshObject()
        names.append(mesh.GetName())
        tris.append(mesh.GetTriangleCount())
    assert names == ["plate", "block"] and tris == [12, 12]
    comps = model.GetComponentsObjects()
    assert comps.MoveNext() and comps.GetCurrentComponentsObject().GetComponentCount() == 2
    colours = []
    groups = model.GetColorGroups()
    while groups.MoveNext():
        group = groups.GetCurrentColorGroup()
        c = group.GetColor(group.GetAllPropertyIDs()[0])
        colours.append(f"#{c.Red:02X}{c.Green:02X}{c.Blue:02X}")
    assert colours == ["#F2F2F2", "#1C1C1F"]
    items = model.GetBuildItems()
    count = 0
    while items.MoveNext():
        count += 1
    assert count == 1


def test_orca_layout_matches_loader(tmp_path) -> None:
    """model_settings.config: object id = the assembly, part ids = its component object ids, slots per part."""
    out = tmp_path / "x.3mf"
    threemf.write_3mf(out, _parts(), assembled=True, slicer="orca")
    model = _xml(out, "3D/3dmodel.model")
    bambu = _xml(out, "Metadata/model_settings.config")
    assembly = model.find("m:resources/m:object[m:components]", _NS)
    build = model.find("m:build/m:item", _NS)
    assert build.get("objectid") == assembly.get("id")
    obj = bambu.find("object")
    assert obj.get("id") == assembly.get("id")
    parts = obj.findall("part")
    assert [p.get("id") for p in parts] == [c.get("objectid") for c in assembly.find("m:components", _NS)]
    assert all(p.get("subtype") == "normal_part" for p in parts)
    assert [p.find("metadata[@key='extruder']").get("value") for p in parts] == ["1", "3"]
    with zipfile.ZipFile(out) as zf:
        assert "Metadata/Slic3r_PE_model.config" not in zf.namelist()


def test_prusa_layout_is_one_mesh_with_ranges(tmp_path) -> None:
    """Prusa layout: one mesh object, a volume (triangle range) per part with its slot."""
    out = tmp_path / "x.3mf"
    threemf.write_3mf(out, _parts(), assembled=True, slicer="prusa")
    model = _xml(out, "3D/3dmodel.model")
    objs = model.findall("m:resources/m:object", _NS)
    assert len(objs) == 1 and len(objs[0].findall("m:mesh/m:triangles/m:triangle", _NS)) == 24
    volumes = _xml(out, "Metadata/Slic3r_PE_model.config").find("object").findall("volume")
    assert [(v.get("firstid"), v.get("lastid")) for v in volumes] == [("0", "11"), ("12", "23")]
    assert [v.find("metadata[@key='extruder']").get("value") for v in volumes] == ["1", "3"]


@pytest.mark.skipif(shutil.which("prusa-slicer") is None, reason="PrusaSlicer not installed")
def test_prusaslicer_switches_filament(tmp_path) -> None:
    """Slice the prusa layout for real: one object, 5 mm tall, the second filament starts above the plate."""
    parts = _parts()
    parts[1]["extruder"] = 2
    out = tmp_path / "x.3mf"
    threemf.write_3mf(out, parts, assembled=True, slicer="prusa")
    ini = tmp_path / "two.ini"
    ini.write_text(
        "nozzle_diameter = 0.4,0.4\nfilament_diameter = 1.75,1.75\ntemperature = 210,210\n"
        "first_layer_temperature = 215,215\nretract_length = 1,1\nextruder_offset = 0x0,0x0\n"
        "wipe_tower = 0\nbed_shape = 0x0,256x0,256x256,0x256\n"
    )
    info = subprocess.run(["prusa-slicer", "--info", str(out)], capture_output=True, text=True, timeout=120)
    assert info.stdout.count("[x.3mf]") == 1 and "size_z = 5.0" in info.stdout
    gcode = tmp_path / "x.gcode"
    subprocess.run(
        ["prusa-slicer", "--load", str(ini), "--export-gcode", "-o", str(gcode), str(out)],
        capture_output=True,
        text=True,
        timeout=300,
        check=True,
    )
    z, first_t1 = None, None
    for line in gcode.read_text().splitlines():
        if line.startswith(";Z:"):
            z = float(line[3:])
        elif re.match(r"T1\b", line) and first_t1 is None:
            first_t1 = z
    assert first_t1 is not None and 3.0 < first_t1 <= 3.5


def test_geometry_round_trips(tmp_path) -> None:
    """Assembled parts keep their relative positions and the assembly sits on the bed."""
    out = tmp_path / "x.3mf"
    threemf.write_3mf(out, _parts(), assembled=True)
    pts = {}
    for obj in _xml(out, "3D/3dmodel.model").findall("m:resources/m:object[m:mesh]", _NS):
        v = obj.findall("m:mesh/m:vertices/m:vertex", _NS)
        pts[obj.get("name")] = np.array([[float(e.get(k)) for k in "xyz"] for e in v])
    assert pts["plate"][:, 2].min() == pytest.approx(0)
    assert pts["block"][:, 2].min() == pytest.approx(3)  # on top of the 3 mm plate
    assert pts["block"][:, 0].min() - pts["plate"][:, 0].min() == pytest.approx(20)  # block x 0..10, plate -20..20


def test_separate_objects_laid_out(tmp_path) -> None:
    """Not assembled: one build item per part, side by side, slots in both slicers' configs."""
    out = tmp_path / "x.3mf"
    summary = threemf.write_3mf(out, _parts(), assembled=False)
    assert summary["objects"] == 2
    model = _xml(out, "3D/3dmodel.model")
    assert len(model.findall("m:build/m:item", _NS)) == 2
    bambu = _xml(out, "Metadata/model_settings.config")
    prusa = _xml(out, "Metadata/Slic3r_PE_model.config")
    assert [o.find("metadata[@key='extruder']").get("value") for o in bambu.findall("object")] == ["1", "3"]
    assert [o.find("metadata[@key='extruder']").get("value") for o in prusa.findall("object")] == ["1", "3"]
    xs = []
    for obj in model.findall("m:resources/m:object[m:mesh]", _NS):
        v = obj.findall("m:mesh/m:vertices/m:vertex", _NS)
        xs.append((min(float(e.get("x")) for e in v), max(float(e.get("x")) for e in v)))
    assert xs[1][0] >= xs[0][1] + 4.9


def test_bad_input(tmp_path) -> None:
    """Bad colours, slots, slicers and empty lists are refused; inputs aren't modified."""
    with pytest.raises(TemplateError):
        threemf.write_3mf(tmp_path / "x.3mf", [])
    with pytest.raises(TemplateError, match="RRGGBB"):
        threemf.write_3mf(tmp_path / "x.3mf", [{**_parts()[0], "color": "red"}])
    with pytest.raises(TemplateError, match="slot"):
        threemf.write_3mf(tmp_path / "x.3mf", [{**_parts()[0], "extruder": 0}])
    with pytest.raises(TemplateError, match="slicer"):
        threemf.write_3mf(tmp_path / "x.3mf", _parts(), slicer="cura")
    parts = _parts()
    threemf.write_3mf(tmp_path / "x.3mf", parts)
    assert parts[1]["mesh"].bounds[0][2] == pytest.approx(3) and parts[0]["color"] == "#f2f2f2"


def test_api_exports_qr_tag_in_two_colours() -> None:
    """A QR tag exports plate (white, slot 1) + code (black, slot 2) by default; unknown parts give 422."""
    client = TestClient(app)
    tag = client.post("/templates/qr-tag", json={"text": "hello"}).json()
    resp = client.post(f"/models/{tag['model_id']}/export-3mf", json={})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["parts"] == 2 and body["slicer"] == "orca"
    assert [(f["extruder"], f["color"]) for f in body["filaments"]] == [(1, "#F2F2F2"), (2, "#1C1C1F")]
    file = client.get(body["download_url"])
    assert file.status_code == 200 and file.content[:2] == b"PK"
    custom = client.post(
        f"/models/{tag['model_id']}/export-3mf",
        json={"parts": [{"filename": "qr_code.stl", "color": "#FF0000", "extruder": 4}], "slicer": "prusa"},
    )
    assert custom.status_code == 200 and custom.json()["filaments"][0]["extruder"] == 4
    bad = client.post(f"/models/{tag['model_id']}/export-3mf", json={"parts": [{"filename": "../x.stl"}]})
    assert bad.status_code == 422
