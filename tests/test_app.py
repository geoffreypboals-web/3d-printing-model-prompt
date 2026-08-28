"""Tests for app.py's HTTP surface: health, the golden generate path, and
its validation/backend-failure edges."""
from fastapi.testclient import TestClient

import app as app_module
from src.llm_client import LLMError

client = TestClient(app_module.app)


def test_health_returns_ok():
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def test_index_serves_html():
    res = client.get("/")
    assert res.status_code == 200
    assert "3D Printing Model Prompt" in res.text


def test_generate_golden_path(monkeypatch):
    class FakeClient:
        async def generate(self, prompt_text):
            return "a printable hook design"

    monkeypatch.setattr(app_module, "get_client", lambda settings: FakeClient())
    res = client.post("/api/generate", json={"text": "a wall-mounted hook"})
    assert res.status_code == 200
    body = res.json()
    assert body["result"] == "a printable hook design"
    assert "a wall-mounted hook" in body["prompt"]


def test_generate_rejects_empty_text():
    res = client.post("/api/generate", json={"text": "   "})
    assert res.status_code == 400
    assert "empty" in res.json()["detail"]


def test_generate_reports_backend_failure_as_502(monkeypatch):
    class FailingClient:
        async def generate(self, prompt_text):
            raise LLMError("Unable to reach Ollama at http://x: connection refused")

    monkeypatch.setattr(app_module, "get_client", lambda settings: FailingClient())
    res = client.post("/api/generate", json={"text": "a hook"})
    assert res.status_code == 502
    assert "Unable to reach Ollama" in res.json()["detail"]


def test_generate_with_custom_max_size(monkeypatch):
    captured = {}

    class FakeClient:
        async def generate(self, prompt_text):
            captured["prompt"] = prompt_text
            return "ok"

    monkeypatch.setattr(app_module, "get_client", lambda settings: FakeClient())
    res = client.post("/api/generate", json={"text": "a small gear", "max_size_mm": 80})
    assert res.status_code == 200
    assert "80mm" in captured["prompt"]
