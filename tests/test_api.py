"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/tests/test_api.py
Description: End-to-end tests for the FastAPI routes in main.py, with the
    classifier, generator backends, thickness strategies, and LLM
    reachability check all mocked out - these test routing, validation,
    and error translation, not the CAD tools themselves (those are
    covered by their own unit test modules).
Inputs: pytest, tests/conftest.py fixtures, fastapi.testclient.TestClient.
Outputs: N/A (test module).
Troubleshooting:
    - If a test gets a 503 you didn't expect, check the corresponding
      generator/thickness function was actually monkeypatched on the
      *module* object main imported (e.g. `main.openscad_generator.generate`),
      not on the original defining module.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from threedprompt import blender_generator, main, openscad_generator, storage, thickness, watertight
from threedprompt.models import (
    Backend,
    BoundingBox,
    ClassificationMethod,
    ClassificationResult,
    Complexity,
    GenerationResult,
    Hole,
    HoleClassification,
    RepairResult,
    WatertightReport,
)


@pytest.fixture
def client():
    return TestClient(main.app)


def _fake_openscad_generate(prompt, output_dir, wall_thickness_mm=None, llm_client=None):
    output_dir.mkdir(parents=True, exist_ok=True)
    stl_path = output_dir / "model.stl"
    scad_path = output_dir / "model.scad"
    stl_path.write_bytes(b"solid fake\nendsolid fake\n")
    scad_path.write_text(f"wall_thickness = {wall_thickness_mm or 3.0};\ncube([10,10,wall_thickness]);\n")
    return GenerationResult(
        backend=Backend.OPENSCAD,
        stl_path=str(stl_path),
        source_path=str(scad_path),
        source_kind="openscad_scad",
        wall_thickness_param="wall_thickness",
    )


def _fake_blender_generate(prompt, output_dir, llm_client=None):
    output_dir.mkdir(parents=True, exist_ok=True)
    stl_path = output_dir / "model.stl"
    script_path = output_dir / "build.py"
    stl_path.write_bytes(b"solid fake\nendsolid fake\n")
    script_path.write_text("def build_scene():\n    pass\n")
    return GenerationResult(
        backend=Backend.BLENDER,
        stl_path=str(stl_path),
        source_path=str(script_path),
        source_kind="blender_bpy_script",
        wall_thickness_param=None,
    )


def test_root_serves_browser_ui(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "<title>3D Printing Model Prompt</title>" in resp.text


def test_static_mount_does_not_shadow_api_routes(client):
    # The StaticFiles catch-all is mounted at "/" last - confirm a real API
    # route still resolves correctly rather than falling through to a 404
    # from the static file handler.
    resp = client.get("/models/does-not-exist/download")
    assert resp.status_code == 404
    assert "text/html" not in resp.headers["content-type"]


def test_health_reports_dependency_status(monkeypatch, client):
    monkeypatch.setattr(main.shutil, "which", lambda name: "/usr/bin/x")
    monkeypatch.setattr(main, "get_llm_client", lambda: type("C", (), {"is_reachable": lambda self: True})())

    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["openscad_available"] is True
    assert body["blender_available"] is True
    assert body["llm_reachable"] is True


def test_generate_golden_path_simple_routes_to_openscad(monkeypatch, client):
    monkeypatch.setattr(
        main,
        "classify_prompt",
        lambda prompt: ClassificationResult(Complexity.SIMPLE, 0.9, ClassificationMethod.HEURISTIC, "bracket"),
    )
    monkeypatch.setattr(openscad_generator, "generate", _fake_openscad_generate)

    resp = client.post("/generate", json={"prompt": "a mounting bracket", "wall_thickness_mm": 3.0})
    assert resp.status_code == 200
    body = resp.json()
    assert body["backend"] == "openscad"
    assert body["classification_label"] == "simple"
    assert body["download_url"] == f"/models/{body['model_id']}/download"


def test_generate_golden_path_complex_routes_to_blender(monkeypatch, client):
    monkeypatch.setattr(
        main,
        "classify_prompt",
        lambda prompt: ClassificationResult(Complexity.COMPLEX, 0.9, ClassificationMethod.HEURISTIC, "creature"),
    )
    monkeypatch.setattr(blender_generator, "generate", _fake_blender_generate)

    resp = client.post("/generate", json={"prompt": "a dwarf sitting under a mushroom"})
    assert resp.status_code == 200
    assert resp.json()["backend"] == "blender"


def test_generate_repeated_prompt_hits_cache(monkeypatch, client):
    monkeypatch.setattr(
        main,
        "classify_prompt",
        lambda prompt: ClassificationResult(Complexity.SIMPLE, 0.9, ClassificationMethod.HEURISTIC, "bracket"),
    )
    calls = {"n": 0}

    def counting_generate(prompt, output_dir, wall_thickness_mm=None, llm_client=None):
        calls["n"] += 1
        return _fake_openscad_generate(prompt, output_dir, wall_thickness_mm, llm_client)

    monkeypatch.setattr(openscad_generator, "generate", counting_generate)

    first = client.post("/generate", json={"prompt": "a mounting bracket"})
    second = client.post("/generate", json={"prompt": "a mounting bracket"})
    assert first.json()["model_id"] == second.json()["model_id"]
    assert calls["n"] == 1  # second request served from cache, no regeneration


def test_generate_empty_prompt_returns_422(client):
    resp = client.post("/generate", json={"prompt": "   "})
    assert resp.status_code == 422


def test_generate_backend_failure_returns_503(monkeypatch, client):
    monkeypatch.setattr(
        main,
        "classify_prompt",
        lambda prompt: ClassificationResult(Complexity.SIMPLE, 0.9, ClassificationMethod.HEURISTIC, "bracket"),
    )

    def failing_generate(*args, **kwargs):
        raise openscad_generator.OpenScadGenerationError("openscad binary not found on PATH")

    monkeypatch.setattr(openscad_generator, "generate", failing_generate)

    resp = client.post("/generate", json={"prompt": "a mounting bracket"})
    assert resp.status_code == 503
    assert "not found on PATH" in resp.json()["detail"]


def test_download_missing_model_returns_404(client):
    resp = client.get("/models/does-not-exist/download")
    assert resp.status_code == 404


def test_download_existing_model_returns_file(client):
    model_id, path = storage.new_model_dir()
    (path / "model.stl").write_bytes(b"solid fake\nendsolid fake\n")

    resp = client.get(f"/models/{model_id}/download")
    assert resp.status_code == 200
    assert resp.content == b"solid fake\nendsolid fake\n"


def test_thicken_existing_model_prefers_regenerate_from_source(monkeypatch, client):
    model_id, path = storage.new_model_dir()
    (path / "model.stl").write_bytes(b"solid fake\nendsolid fake\n")
    (path / "model.scad").write_text("wall_thickness = 3.0;\ncube([10,10,wall_thickness]);\n")
    storage.save_spec(
        model_id,
        {
            "backend": "openscad",
            "source_path": str(path / "model.scad"),
            "source_kind": "openscad_scad",
            "wall_thickness_param": "wall_thickness",
        },
    )

    def fake_regenerate(scad_path, amount_mm, output_stl_path):
        output_stl_path.write_bytes(b"solid thicker\nendsolid thicker\n")
        return 4.5

    monkeypatch.setattr(thickness, "regenerate_from_source", fake_regenerate)

    resp = client.post(f"/models/{model_id}/thicken", json={"amount_mm": 1.5})
    assert resp.status_code == 200
    body = resp.json()
    assert body["method"] == "regenerated_from_source"
    assert body["model_id"] != model_id


def test_thicken_existing_model_without_source_falls_back_to_mesh_shell(monkeypatch, client):
    model_id, path = storage.new_model_dir()
    (path / "model.stl").write_bytes(b"solid fake\nendsolid fake\n")
    storage.save_spec(model_id, {"backend": "blender", "source_kind": "blender_bpy_script"})

    def fake_mesh_shell(input_path, amount_mm, output_dir):
        output_dir.mkdir(parents=True, exist_ok=True)
        result = output_dir / "model.stl"
        result.write_bytes(b"solid shelled\nendsolid shelled\n")
        return result

    monkeypatch.setattr(thickness, "mesh_shell", fake_mesh_shell)

    resp = client.post(f"/models/{model_id}/thicken", json={"amount_mm": 2.0})
    assert resp.status_code == 200
    assert resp.json()["method"] == "mesh_shell"


def test_thicken_missing_model_returns_404(client):
    resp = client.post("/models/does-not-exist/thicken", json={"amount_mm": 1.0})
    assert resp.status_code == 404


def test_thicken_upload_golden_path(monkeypatch, client):
    def fake_mesh_shell(input_path, amount_mm, output_dir):
        output_dir.mkdir(parents=True, exist_ok=True)
        result = output_dir / "model.stl"
        result.write_bytes(b"solid shelled\nendsolid shelled\n")
        return result

    monkeypatch.setattr(thickness, "mesh_shell", fake_mesh_shell)

    resp = client.post(
        "/thicken",
        files={"file": ("uploaded.stl", b"solid fake\nendsolid fake\n", "application/octet-stream")},
        data={"amount_mm": "2.0"},
    )
    assert resp.status_code == 200
    assert resp.content == b"solid shelled\nendsolid shelled\n"
    assert resp.headers["x-thicken-method"] == "mesh_shell"
    assert resp.headers["x-model-id"]  # non-empty


def test_thicken_upload_rejects_unsupported_extension(client):
    resp = client.post(
        "/thicken",
        files={"file": ("uploaded.txt", b"not a mesh", "text/plain")},
        data={"amount_mm": "2.0"},
    )
    assert resp.status_code == 422


def test_thicken_upload_rejects_oversized_file(monkeypatch, client):
    from threedprompt.config import settings

    monkeypatch.setattr(settings, "max_upload_bytes", 10)
    resp = client.post(
        "/thicken",
        files={"file": ("uploaded.stl", b"solid fake\nendsolid fake\n", "application/octet-stream")},
        data={"amount_mm": "2.0"},
    )
    assert resp.status_code == 413


def _fake_watertight_report(holes=None, is_watertight=False):
    return WatertightReport(
        source_path="model.stl",
        is_watertight=is_watertight,
        vertex_count=8,
        face_count=11,
        total_surface_area=24.0,
        bounding_box=BoundingBox(min=(-1.0, -1.0, -1.0), max=(1.0, 1.0, 1.0)),
        holes=holes or [],
        viewer_path="/fake/viewer.glb",
    )


def test_watertight_upload_golden_path(client):
    resp = client.post(
        "/watertight/upload",
        files={"file": ("model.stl", b"solid fake\nendsolid fake\n", "application/octet-stream")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["model_id"]
    assert body["filename"] == "model.stl"


def test_watertight_upload_rejects_unsupported_extension(client):
    resp = client.post(
        "/watertight/upload",
        files={"file": ("model.txt", b"not a mesh", "text/plain")},
    )
    assert resp.status_code == 422


def test_analyze_model_golden_path(monkeypatch, client):
    upload_resp = client.post(
        "/watertight/upload",
        files={"file": ("model.stl", b"solid fake\nendsolid fake\n", "application/octet-stream")},
    )
    model_id = upload_resp.json()["model_id"]

    hole = Hole(
        id=0,
        vertex_indices=[0, 1, 2],
        centroid=(0.0, 0.0, 1.0),
        area=3.0,
        perimeter=6.14,
        planarity=1.0,
        classification=HoleClassification.LIKELY_DEFECT,
        confidence=0.9,
        reason="a stray gap",
    )
    monkeypatch.setattr(watertight, "analyze_mesh", lambda *a, **k: _fake_watertight_report(holes=[hole]))

    resp = client.post(f"/models/{model_id}/analyze")
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_watertight"] is False
    assert len(body["holes"]) == 1
    assert body["holes"][0]["classification"] == "likely_defect"
    assert body["viewer_glb_url"] == f"/models/{model_id}/viewer.glb"


def test_analyze_model_missing_model_returns_404(client):
    resp = client.post("/models/does-not-exist/analyze")
    assert resp.status_code == 404


def test_analyze_model_blender_failure_returns_503(monkeypatch, client):
    upload_resp = client.post(
        "/watertight/upload",
        files={"file": ("model.stl", b"solid fake\nendsolid fake\n", "application/octet-stream")},
    )
    model_id = upload_resp.json()["model_id"]

    def _raise(*a, **k):
        raise watertight.WatertightError("blender binary not found on PATH")

    monkeypatch.setattr(watertight, "analyze_mesh", _raise)
    resp = client.post(f"/models/{model_id}/analyze")
    assert resp.status_code == 503


def test_repair_model_golden_path(monkeypatch, client):
    upload_resp = client.post(
        "/watertight/upload",
        files={"file": ("model.stl", b"solid fake\nendsolid fake\n", "application/octet-stream")},
    )
    model_id = upload_resp.json()["model_id"]

    def fake_repair_mesh(input_path, hole_ids, output_path, *, viewer_output=None):
        from pathlib import Path

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"solid repaired\nendsolid repaired\n")
        return RepairResult(
            output_path=output_path,
            closed_hole_ids=hole_ids,
            is_watertight=True,
            remaining_holes=[],
            viewer_path=viewer_output or "",
        )

    monkeypatch.setattr(watertight, "repair_mesh", fake_repair_mesh)
    resp = client.post(f"/models/{model_id}/repair", json={"hole_ids": [0]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_watertight"] is True
    assert body["closed_hole_ids"] == [0]
    assert body["download_url"] == f"/models/{model_id}/download"


def test_repair_model_rejects_empty_hole_ids(client):
    upload_resp = client.post(
        "/watertight/upload",
        files={"file": ("model.stl", b"solid fake\nendsolid fake\n", "application/octet-stream")},
    )
    model_id = upload_resp.json()["model_id"]
    resp = client.post(f"/models/{model_id}/repair", json={"hole_ids": []})
    assert resp.status_code == 422


def test_viewer_glb_missing_returns_404(client):
    upload_resp = client.post(
        "/watertight/upload",
        files={"file": ("model.stl", b"solid fake\nendsolid fake\n", "application/octet-stream")},
    )
    model_id = upload_resp.json()["model_id"]
    resp = client.get(f"/models/{model_id}/viewer.glb")
    assert resp.status_code == 404
