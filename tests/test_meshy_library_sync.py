"""
Project: 3D Printing Model Prompt
File: tests/test_meshy_library_sync.py
Description: Tests for scripts/meshy_library_sync.py's offline parts: names,
    which files a task offers, link stripping, de-duplication across lists,
    and the saved PROMPT.md / record.json for an item whose links expired.
Inputs: synthetic Meshy task records; no network.
Outputs: pass/fail.
Troubleshooting: a test that hangs made a real HTTP call; patch urlopen.
"""
import importlib.util
import json
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("meshy_library_sync", ROOT / "scripts" / "meshy_library_sync.py")
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)

TASK = {"id": "49cfa43d-4db2-431d-9a00-df22f94854dc", "type": "image-to-3d", "name": "scout canvas tent",
        "texture_prompt": "Olive canvas ridge tent", "status": "SUCCEEDED", "created_at": 1786586498795,
        "model_urls": {"glb": "https://assets.example/t/model.glb?Expires=1", "stl": "https://assets.example/t/m.stl"},
        "thumbnail_url": "https://assets.example/t/thumb.png", "texture_urls": [{"base_color": "https://assets.example/t/c.png"}],
        "_endpoint": "v1/image-to-3d"}


def test_slug_is_windows_safe():
    assert sync.slug('A "tent": v2/final?') == "a-tent-v2-final"
    assert sync.slug("") == "untitled"


def test_every_file_is_found_and_links_are_stripped():
    labels = [label for label, _ in sync.urls_of(TASK)]
    assert labels == ["model.glb", "model.stl", "thumbnail", "texture_0_base_color"]
    clean = sync.strip_urls(TASK)
    assert "model_urls" not in clean and "thumbnail_url" not in clean
    assert clean["texture_prompt"] == "Olive canvas ridge tent"


def test_tasks_listed_by_two_endpoints_are_kept_once(monkeypatch):
    def fake_get(path, key):
        if path.startswith(("v1/text-to-image", "v1/image-to-image")):
            return [{"id": "same", "status": "SUCCEEDED"}]
        return []
    monkeypatch.setattr(sync, "get_json", fake_get)
    assert [t["id"] for t in sync.list_tasks("k")] == ["same"]


def test_an_expired_item_still_saves_its_prompt(tmp_path, monkeypatch):
    def gone(url, timeout=0):
        raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)
    monkeypatch.setattr(sync.urllib.request, "urlopen", gone)
    row = sync.save_item(dict(TASK), tmp_path, refresh=False)
    folder = tmp_path / row["folder"]
    assert "Olive canvas ridge tent" in (folder / "PROMPT.md").read_text()
    record = json.loads((folder / "record.json").read_text())
    assert record["_files"] == [] and "model.glb: expired" in record["_problems"]
    assert "model_urls" not in record
    assert row["_new"] is True
    assert sync.save_item(dict(TASK), tmp_path, refresh=False)["_new"] is False
