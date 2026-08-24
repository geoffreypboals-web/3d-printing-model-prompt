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

from threedprompt import blender_generator, main, openscad_generator, storage, thickness
from threedprompt.models import (
    Backend,
    ClassificationMethod,
    ClassificationResult,
    Complexity,
    GenerationResult,
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
