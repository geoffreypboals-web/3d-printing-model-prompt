"""OllamaClient turns thinking off: qwen3.x otherwise reasons for minutes before
answering, and builds hit the read timeout (lambda02, 2026-10-28)."""

from threedprompt import llm_client
from threedprompt.llm_client import OllamaClient


class _Resp:
    def raise_for_status(self):
        pass

    def json(self):
        return {"response": "cube([1, 1, 1]);"}


def test_generate_sends_think_false(monkeypatch):
    sent = {}

    def fake_post(url, json, timeout):
        sent.update(json)
        return _Resp()

    monkeypatch.setattr(llm_client.requests, "post", fake_post)
    assert OllamaClient("http://x", "qwen3.6:27b", 5).generate("a cube", system="be brief") == "cube([1, 1, 1]);"
    assert sent["think"] is False and sent["stream"] is False and sent["system"] == "be brief"
