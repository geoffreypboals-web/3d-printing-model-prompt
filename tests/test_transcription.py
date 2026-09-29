"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/tests/test_transcription.py
Description: Tests for POST /transcribe and transcription.py - the Whisper
    model is replaced with a fake so no model download or audio decoding
    happens.
Inputs: FastAPI TestClient; monkeypatched transcription._get_model.
Outputs: pytest assertions.
Troubleshooting:
    - If these start downloading a model, a test is calling the real
      _get_model - make sure it goes through the fake_model fixture.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from threedprompt import transcription
from threedprompt.config import settings
from threedprompt.main import app

client = TestClient(app)


class _FakeModel:
    """Stands in for faster_whisper.WhisperModel; records what it was asked to transcribe."""

    def __init__(self, texts):
        self.texts = texts
        self.calls = []

    def transcribe(self, audio, **kwargs):
        self.calls.append((audio.read(), kwargs))
        return iter(SimpleNamespace(text=t) for t in self.texts), None


@pytest.fixture
def fake_model(monkeypatch):
    model = _FakeModel([" A small gear ", "with twelve teeth.", "  "])
    monkeypatch.setattr(transcription, "_get_model", lambda: model)
    return model


def test_transcribe_returns_joined_segments(fake_model):
    response = client.post("/transcribe", files={"file": ("answer.webm", b"fake-audio", "audio/webm")})
    assert response.status_code == 200
    assert response.json() == {"text": "A small gear with twelve teeth."}
    assert fake_model.calls[0][0] == b"fake-audio"
    assert fake_model.calls[0][1]["vad_filter"] is True


def test_transcribe_rejects_non_audio_extension(fake_model):
    response = client.post("/transcribe", files={"file": ("model.stl", b"solid", "model/stl")})
    assert response.status_code == 422
    assert not fake_model.calls


def test_transcribe_rejects_oversized_recording(fake_model, monkeypatch):
    monkeypatch.setattr(settings, "max_audio_upload_bytes", 4)
    response = client.post("/transcribe", files={"file": ("answer.webm", b"too-long", "audio/webm")})
    assert response.status_code == 413


def test_transcribe_returns_503_when_model_unavailable(monkeypatch):
    def broken():
        raise transcription.TranscriptionError("faster-whisper is not installed")

    monkeypatch.setattr(transcription, "_get_model", broken)
    response = client.post("/transcribe", files={"file": ("answer.webm", b"audio", "audio/webm")})
    assert response.status_code == 503
    assert "faster-whisper" in response.json()["detail"]


def test_empty_recording_is_empty_text_without_loading_model(monkeypatch):
    monkeypatch.setattr(transcription, "_get_model", lambda: pytest.fail("model should not load"))
    assert transcription.transcribe(b"") == ""


def test_model_dir_defaults_under_output_dir(monkeypatch, tmp_output_dir):
    monkeypatch.setattr(settings, "whisper_model_dir", "")
    assert transcription._model_dir() == str(tmp_output_dir / "whisper-models")
