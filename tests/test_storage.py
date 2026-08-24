"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/tests/test_storage.py
Description: Tests for local filesystem storage - model directories,
    spec sidecars, the prompt cache, and upload/copy helpers.
Inputs: pytest, tests/conftest.py fixtures (tmp_output_dir is autouse).
Outputs: N/A (test module).
Troubleshooting: N/A.
"""

from __future__ import annotations

import pytest

from threedprompt import storage


def test_new_model_dir_creates_directory():
    model_id, path = storage.new_model_dir()
    assert path.is_dir()
    assert path.name == model_id


def test_save_and_load_spec_round_trips():
    model_id, _ = storage.new_model_dir()
    storage.save_spec(model_id, {"backend": "openscad", "prompt": "a bracket"})
    assert storage.load_spec(model_id) == {"backend": "openscad", "prompt": "a bracket"}


def test_load_spec_returns_none_when_absent():
    model_id, _ = storage.new_model_dir()
    assert storage.load_spec(model_id) is None


def test_stl_path_missing_model_raises_file_not_found():
    with pytest.raises(FileNotFoundError):
        storage.stl_path("does-not-exist")


def test_stl_path_missing_file_raises_file_not_found():
    model_id, _ = storage.new_model_dir()
    with pytest.raises(FileNotFoundError):
        storage.stl_path(model_id)


def test_cache_miss_when_never_generated():
    assert storage.lookup_cached_model("a bracket", None) is None


def test_cache_hit_after_remember():
    model_id, path = storage.new_model_dir()
    (path / "model.stl").write_bytes(b"solid fake\nendsolid fake\n")
    storage.remember_cached_model("a bracket", 3.0, model_id)
    assert storage.lookup_cached_model("a bracket", 3.0) == model_id
    # a different wall_thickness_mm is a different cache key
    assert storage.lookup_cached_model("a bracket", 4.0) is None


def test_save_upload_stores_file_with_matching_suffix():
    model_id, path = storage.save_upload("my part.stl", b"solid fake\nendsolid fake\n")
    assert path.suffix == ".stl"
    assert path.read_bytes() == b"solid fake\nendsolid fake\n"
    assert storage.model_dir(model_id) == path.parent


def test_copy_into_new_model_copies_files_and_spec():
    model_id, path = storage.new_model_dir()
    (path / "model.stl").write_bytes(b"solid fake\nendsolid fake\n")
    (path / "model.scad").write_text("cube([1,1,1]);")
    storage.save_spec(model_id, {"backend": "openscad"})

    new_id, new_dir = storage.copy_into_new_model(path, spec={"backend": "openscad", "parent_model_id": model_id})

    assert (new_dir / "model.stl").is_file()
    assert (new_dir / "model.scad").is_file()
    assert storage.load_spec(new_id) == {"backend": "openscad", "parent_model_id": model_id}
    assert new_id != model_id
