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

from threedprompt import (
    blender_generator,
    draft_analysis,
    main,
    mold,
    openscad_generator,
    storage,
    thickness,
    watertight,
)
from threedprompt.models import (
    Backend,
    BoundingBox,
    ClassificationMethod,
    ClassificationResult,
    Complexity,
    DraftReport,
    GenerationResult,
    Hole,
    HoleClassification,
    MoldResult,
    ProblemFaceIsland,
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

    def fake_mesh_shell(input_path, amount_mm, output_dir, *, quad_target_faces=0):
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
    def fake_mesh_shell(input_path, amount_mm, output_dir, *, quad_target_faces=0):
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


def _fake_make_mold(input_path, output_dir, **kwargs):
    output_dir.mkdir(parents=True, exist_ok=True)
    mode = kwargs.get("mode")
    if mode == "direct_cast":
        part_names = ("direct_mold_bottom", "direct_mold_top")
    elif mode == "form_fitting":
        part_names = ("skin_pour_bottom", "skin_pour_top", "support_jacket_bottom", "support_jacket_top")
    elif mode == "hollow_cast":
        part_names = ("hollow_cast_bottom", "hollow_cast_top", "hollow_cast_core")
    else:
        part_names = ("pour_box_bottom", "pour_box_top", "clamp_shell_bottom", "clamp_shell_top")
    paths = {}
    for name in part_names:
        p = output_dir / f"{name}.stl"
        p.write_bytes(b"solid fake\nendsolid fake\n")
        paths[name] = str(p)
    return MoldResult(**{f"{name}_stl": path for name, path in paths.items()})


def test_mold_existing_model_golden_path(monkeypatch, client):
    model_id, path = storage.new_model_dir()
    (path / "model.stl").write_bytes(b"solid fake\nendsolid fake\n")
    monkeypatch.setattr(mold, "make_mold", _fake_make_mold)

    resp = client.post(f"/models/{model_id}/mold", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["model_id"] != model_id
    assert body["download_url"] == f"/models/{body['model_id']}/mold.zip"

    zip_resp = client.get(body["download_url"])
    assert zip_resp.status_code == 200
    assert zip_resp.headers["content-type"] == "application/zip"
    assert body["repaired_hole_ids"] == []


def test_mold_existing_model_surfaces_repaired_hole_ids(monkeypatch, client):
    model_id, path = storage.new_model_dir()
    (path / "model.stl").write_bytes(b"solid fake\nendsolid fake\n")

    def _fake_make_mold_with_repair(input_path, output_dir, **kwargs):
        result = _fake_make_mold(input_path, output_dir, **kwargs)
        return MoldResult(**{**vars(result), "repaired_hole_ids": [0, 2]})

    monkeypatch.setattr(mold, "make_mold", _fake_make_mold_with_repair)

    resp = client.post(f"/models/{model_id}/mold", json={})
    assert resp.status_code == 200
    assert resp.json()["repaired_hole_ids"] == [0, 2]


def test_mold_existing_model_direct_cast(monkeypatch, client):
    model_id, path = storage.new_model_dir()
    (path / "model.stl").write_bytes(b"solid fake\nendsolid fake\n")
    monkeypatch.setattr(mold, "make_mold", _fake_make_mold)

    resp = client.post(f"/models/{model_id}/mold", json={"mode": "direct_cast"})
    assert resp.status_code == 200
    body = resp.json()

    zip_resp = client.get(body["download_url"])
    assert zip_resp.status_code == 200
    assert zip_resp.headers["content-type"] == "application/zip"


def test_mold_existing_model_form_fitting(monkeypatch, client):
    model_id, path = storage.new_model_dir()
    (path / "model.stl").write_bytes(b"solid fake\nendsolid fake\n")
    monkeypatch.setattr(mold, "make_mold", _fake_make_mold)

    resp = client.post(f"/models/{model_id}/mold", json={"mode": "form_fitting"})
    assert resp.status_code == 200
    body = resp.json()

    zip_resp = client.get(body["download_url"])
    assert zip_resp.status_code == 200
    assert zip_resp.headers["content-type"] == "application/zip"


def test_mold_existing_model_hollow_cast(monkeypatch, client):
    model_id, path = storage.new_model_dir()
    (path / "model.stl").write_bytes(b"solid fake\nendsolid fake\n")
    monkeypatch.setattr(mold, "make_mold", _fake_make_mold)

    resp = client.post(f"/models/{model_id}/mold", json={"mode": "hollow_cast"})
    assert resp.status_code == 200
    body = resp.json()

    zip_resp = client.get(body["download_url"])
    assert zip_resp.status_code == 200
    assert zip_resp.headers["content-type"] == "application/zip"


def test_mold_missing_model_returns_404(client):
    resp = client.post("/models/does-not-exist/mold", json={})
    assert resp.status_code == 404


def test_mold_bad_param_returns_422(monkeypatch, client):
    model_id, path = storage.new_model_dir()
    (path / "model.stl").write_bytes(b"solid fake\nendsolid fake\n")

    def failing_make_mold(*args, **kwargs):
        raise mold.MoldError("resulting mold would be 999.0mm on its largest side, exceeding MAX_MOLD_DIMENSION_MM=300")

    monkeypatch.setattr(mold, "make_mold", failing_make_mold)
    resp = client.post(f"/models/{model_id}/mold", json={"clearance_mm": 500.0})
    assert resp.status_code == 422


def test_mold_oversized_pour_hole_returns_422(monkeypatch, client):
    """FR-6/_add_pour_holes's cavity-footprint guard (make_mold.py) surfaces as a 422, not a 503."""
    model_id, path = storage.new_model_dir()
    (path / "model.stl").write_bytes(b"solid fake\nendsolid fake\n")

    def failing_make_mold(*args, **kwargs):
        raise mold.MoldError(
            "sprue_diameter_mm/vent_diameter_mm (10.0/4.0mm) must be smaller than the cavity's own "
            "footprint (1.00x1.00mm) -- a hole this large relative to the model would punch away the "
            "mold's entire ceiling instead of leaving a working pour hole"
        )

    monkeypatch.setattr(mold, "make_mold", failing_make_mold)
    resp = client.post(f"/models/{model_id}/mold", json={"mode": "direct_cast"})
    assert resp.status_code == 422


def test_mold_blender_failure_returns_503(monkeypatch, client):
    model_id, path = storage.new_model_dir()
    (path / "model.stl").write_bytes(b"solid fake\nendsolid fake\n")

    def failing_make_mold(*args, **kwargs):
        raise mold.MoldError("blender binary not found on PATH")

    monkeypatch.setattr(mold, "make_mold", failing_make_mold)
    resp = client.post(f"/models/{model_id}/mold", json={})
    assert resp.status_code == 503


def test_mold_zip_missing_returns_404(client):
    model_id, _path = storage.new_model_dir()
    resp = client.get(f"/models/{model_id}/mold.zip")
    assert resp.status_code == 404


def test_mold_upload_golden_path(monkeypatch, client):
    monkeypatch.setattr(mold, "make_mold", _fake_make_mold)

    resp = client.post(
        "/mold",
        files={"file": ("uploaded.stl", b"solid fake\nendsolid fake\n", "application/octet-stream")},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    assert resp.headers["x-model-id"]
    assert resp.headers["x-repaired-hole-ids"] == ""


def test_mold_upload_surfaces_repaired_hole_ids_header(monkeypatch, client):
    def _fake_make_mold_with_repair(input_path, output_dir, **kwargs):
        result = _fake_make_mold(input_path, output_dir, **kwargs)
        return MoldResult(**{**vars(result), "repaired_hole_ids": [1]})

    monkeypatch.setattr(mold, "make_mold", _fake_make_mold_with_repair)

    resp = client.post(
        "/mold",
        files={"file": ("uploaded.stl", b"solid fake\nendsolid fake\n", "application/octet-stream")},
    )
    assert resp.status_code == 200
    assert resp.headers["x-repaired-hole-ids"] == "1"


def test_mold_upload_rejects_unsupported_extension(client):
    resp = client.post(
        "/mold",
        files={"file": ("uploaded.txt", b"not a mesh", "text/plain")},
    )
    assert resp.status_code == 422


def test_mold_upload_direct_cast(monkeypatch, client):
    monkeypatch.setattr(mold, "make_mold", _fake_make_mold)

    resp = client.post(
        "/mold",
        files={"file": ("uploaded.stl", b"solid fake\nendsolid fake\n", "application/octet-stream")},
        data={"mode": "direct_cast"},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"


def test_mold_upload_form_fitting(monkeypatch, client):
    monkeypatch.setattr(mold, "make_mold", _fake_make_mold)

    resp = client.post(
        "/mold",
        files={"file": ("uploaded.stl", b"solid fake\nendsolid fake\n", "application/octet-stream")},
        data={"mode": "form_fitting"},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"


def test_mold_upload_hollow_cast(monkeypatch, client):
    monkeypatch.setattr(mold, "make_mold", _fake_make_mold)

    resp = client.post(
        "/mold",
        files={"file": ("uploaded.stl", b"solid fake\nendsolid fake\n", "application/octet-stream")},
        data={"mode": "hollow_cast"},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"


def test_mold_upload_rejects_bad_mode(client):
    resp = client.post(
        "/mold",
        files={"file": ("uploaded.stl", b"solid fake\nendsolid fake\n", "application/octet-stream")},
        data={"mode": "not_a_real_mode"},
    )
    assert resp.status_code == 422


def test_mold_existing_model_forwards_parting_axis_and_offset(monkeypatch, client):
    """FR-6: parting_axis/parting_offset_mm from the request body reach mold.make_mold() unchanged."""
    model_id, path = storage.new_model_dir()
    (path / "model.stl").write_bytes(b"solid fake\nendsolid fake\n")
    seen_kwargs = {}

    def _recording_make_mold(input_path, output_dir, **kwargs):
        seen_kwargs.update(kwargs)
        return _fake_make_mold(input_path, output_dir, **kwargs)

    monkeypatch.setattr(mold, "make_mold", _recording_make_mold)

    resp = client.post(
        f"/models/{model_id}/mold",
        json={"mode": "direct_cast", "parting_axis": "x", "parting_offset_mm": 0.2},
    )
    assert resp.status_code == 200
    assert seen_kwargs["parting_axis"] == "x"
    assert seen_kwargs["parting_offset_mm"] == 0.2
    # material_density_g_per_cm3 is popped out before reaching make_mold() -
    # the Blender subprocess has no reason to know about density (ADR 0010).
    assert "material_density_g_per_cm3" not in seen_kwargs


def test_mold_existing_model_reports_cavity_volume_and_estimated_mass(monkeypatch, client):
    """FR-8: cavity_volume_cm3 always comes back, estimated_cast_mass_g is volume * density when given."""
    model_id, path = storage.new_model_dir()
    (path / "model.stl").write_bytes(b"solid fake\nendsolid fake\n")

    def _fake_make_mold_with_volume(input_path, output_dir, **kwargs):
        result = _fake_make_mold(input_path, output_dir, **kwargs)
        return MoldResult(**{**vars(result), "cavity_volume_cm3": 2.0})

    monkeypatch.setattr(mold, "make_mold", _fake_make_mold_with_volume)

    resp = client.post(
        f"/models/{model_id}/mold",
        json={"mode": "direct_cast", "material_density_g_per_cm3": 1.5},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["cavity_volume_cm3"] == 2.0
    assert body["estimated_cast_mass_g"] == pytest.approx(3.0)


def test_mold_existing_model_without_density_has_no_estimated_mass(monkeypatch, client):
    model_id, path = storage.new_model_dir()
    (path / "model.stl").write_bytes(b"solid fake\nendsolid fake\n")

    def _fake_make_mold_with_volume(input_path, output_dir, **kwargs):
        result = _fake_make_mold(input_path, output_dir, **kwargs)
        return MoldResult(**{**vars(result), "cavity_volume_cm3": 2.0})

    monkeypatch.setattr(mold, "make_mold", _fake_make_mold_with_volume)

    resp = client.post(f"/models/{model_id}/mold", json={"mode": "direct_cast"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["cavity_volume_cm3"] == 2.0
    assert body["estimated_cast_mass_g"] is None


def test_mold_upload_reports_cavity_volume_and_mass_headers(monkeypatch, client):
    def _fake_make_mold_with_volume(input_path, output_dir, **kwargs):
        result = _fake_make_mold(input_path, output_dir, **kwargs)
        return MoldResult(**{**vars(result), "cavity_volume_cm3": 2.0})

    monkeypatch.setattr(mold, "make_mold", _fake_make_mold_with_volume)

    resp = client.post(
        "/mold",
        files={"file": ("uploaded.stl", b"solid fake\nendsolid fake\n", "application/octet-stream")},
        data={"mode": "direct_cast", "material_density_g_per_cm3": "1.5"},
    )
    assert resp.status_code == 200
    assert resp.headers["x-cavity-volume-cm3"] == "2.0"
    assert resp.headers["x-estimated-cast-mass-g"] == "3.0"


def test_mold_upload_without_density_has_no_mass_header(monkeypatch, client):
    def _fake_make_mold_with_volume(input_path, output_dir, **kwargs):
        result = _fake_make_mold(input_path, output_dir, **kwargs)
        return MoldResult(**{**vars(result), "cavity_volume_cm3": 2.0})

    monkeypatch.setattr(mold, "make_mold", _fake_make_mold_with_volume)

    resp = client.post(
        "/mold",
        files={"file": ("uploaded.stl", b"solid fake\nendsolid fake\n", "application/octet-stream")},
        data={"mode": "direct_cast"},
    )
    assert resp.status_code == 200
    assert resp.headers["x-cavity-volume-cm3"] == "2.0"
    assert "x-estimated-cast-mass-g" not in resp.headers


def _fake_draft_report(releasable=True, problem_islands=None):
    return DraftReport(
        source_path="model.stl",
        pull_axis="z",
        parting_coordinate=0.5,
        min_draft_angle_deg=2.0,
        releasable=releasable,
        problem_islands=problem_islands or [],
    )


def test_mold_existing_model_direct_cast_surfaces_draft_check(monkeypatch, client):
    model_id, path = storage.new_model_dir()
    (path / "model.stl").write_bytes(b"solid fake\nendsolid fake\n")

    island = ProblemFaceIsland(
        id=0,
        face_indices=[0, 1],
        centroid=(0.5, 0.5, 0.5),
        face_count=2,
        min_draft_angle_deg=-10.0,
        classification="undercut",
    )

    def _fake_make_mold_with_draft(input_path, output_dir, **kwargs):
        result = _fake_make_mold(input_path, output_dir, **kwargs)
        return MoldResult(
            **{**vars(result), "draft_check": _fake_draft_report(releasable=False, problem_islands=[island])}
        )

    monkeypatch.setattr(mold, "make_mold", _fake_make_mold_with_draft)

    resp = client.post(f"/models/{model_id}/mold", json={"mode": "direct_cast"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["draft_check"] == {"releasable": False, "problem_island_count": 1}


def test_mold_existing_model_silicone_block_has_no_draft_check(monkeypatch, client):
    model_id, path = storage.new_model_dir()
    (path / "model.stl").write_bytes(b"solid fake\nendsolid fake\n")
    monkeypatch.setattr(mold, "make_mold", _fake_make_mold)

    resp = client.post(f"/models/{model_id}/mold", json={})
    assert resp.status_code == 200
    assert resp.json()["draft_check"] is None


def test_mold_upload_direct_cast_surfaces_draft_headers(monkeypatch, client):
    def _fake_make_mold_with_draft(input_path, output_dir, **kwargs):
        result = _fake_make_mold(input_path, output_dir, **kwargs)
        return MoldResult(**{**vars(result), "draft_check": _fake_draft_report(releasable=True)})

    monkeypatch.setattr(mold, "make_mold", _fake_make_mold_with_draft)

    resp = client.post(
        "/mold",
        files={"file": ("uploaded.stl", b"solid fake\nendsolid fake\n", "application/octet-stream")},
        data={"mode": "direct_cast"},
    )
    assert resp.status_code == 200
    assert resp.headers["x-draft-releasable"] == "true"
    assert resp.headers["x-draft-problem-island-count"] == "0"


def test_mold_upload_silicone_block_has_no_draft_headers(monkeypatch, client):
    monkeypatch.setattr(mold, "make_mold", _fake_make_mold)

    resp = client.post(
        "/mold",
        files={"file": ("uploaded.stl", b"solid fake\nendsolid fake\n", "application/octet-stream")},
    )
    assert resp.status_code == 200
    assert "x-draft-releasable" not in resp.headers


def test_draft_check_golden_path(monkeypatch, client):
    model_id, path = storage.new_model_dir()
    (path / "model.stl").write_bytes(b"solid fake\nendsolid fake\n")

    island = ProblemFaceIsland(
        id=0,
        face_indices=[4, 5],
        centroid=(0.5, 0.5, 0.5),
        face_count=2,
        min_draft_angle_deg=0.0,
        classification="insufficient_draft",
    )
    monkeypatch.setattr(
        main.draft_analysis,
        "analyze_draft",
        lambda *a, **kw: _fake_draft_report(releasable=False, problem_islands=[island]),
    )

    resp = client.post(f"/models/{model_id}/mold/draft-check", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["releasable"] is False
    assert len(body["problem_islands"]) == 1
    assert body["problem_islands"][0]["classification"] == "insufficient_draft"


def test_draft_check_missing_model_returns_404(client):
    resp = client.post("/models/does-not-exist/mold/draft-check", json={})
    assert resp.status_code == 404


def test_draft_check_blender_failure_returns_503(monkeypatch, client):
    model_id, path = storage.new_model_dir()
    (path / "model.stl").write_bytes(b"solid fake\nendsolid fake\n")

    def failing_analyze(*args, **kwargs):
        raise draft_analysis.DraftAnalysisError("blender binary not found on PATH")

    monkeypatch.setattr(main.draft_analysis, "analyze_draft", failing_analyze)
    resp = client.post(f"/models/{model_id}/mold/draft-check", json={})
    assert resp.status_code == 503


def test_draft_check_rejects_bad_pull_axis(client):
    model_id, path = storage.new_model_dir()
    (path / "model.stl").write_bytes(b"solid fake\nendsolid fake\n")

    resp = client.post(f"/models/{model_id}/mold/draft-check", json={"pull_axis": "w"})
    assert resp.status_code == 422


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

    def fake_repair_mesh(input_path, hole_ids, output_path, *, viewer_output=None, quad_target_faces=0):
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
