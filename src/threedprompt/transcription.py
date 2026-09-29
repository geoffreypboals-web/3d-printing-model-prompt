"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/src/threedprompt/transcription.py
Description: Local speech-to-text for voice answers, via faster-whisper
    (MIT; runs on CPU, no cloud API, rule 6). Called over HTTP by the
    sibling 3dPrinterWorkshopManager repo so its AI Model Generator
    interview can be answered by voice -- the same local-whisper approach
    AI_Story_Writer's dictation uses. The Whisper model loads lazily on the
    first request and stays in memory; the weights download once into
    WHISPER_MODEL_DIR (default: under OUTPUT_DIR, so the Docker output
    volume keeps them across container recreates).
Inputs: Recorded audio bytes (webm/ogg/wav/mp3/m4a -- anything FFmpeg
    decodes; PyAV bundles FFmpeg, so no system ffmpeg is needed), plus env
    vars WHISPER_MODEL (default "base.en"), WHISPER_DEVICE ("cpu"),
    WHISPER_COMPUTE_TYPE ("int8"), WHISPER_MODEL_DIR.
Outputs: The transcript as plain text ("" when no speech was heard).
    Raises TranscriptionError when faster-whisper isn't installed, the
    model can't be loaded, or the audio can't be decoded.
Troubleshooting:
    - First request is slow (tens of seconds): the model is downloading
      (~140 MB for base.en) and loading. Later requests reuse it.
    - 503 "faster-whisper is not installed": pip install -r
      requirements.txt (the Docker image includes it).
    - 503 mentioning a download/connection error: the container needs
      internet access once to fetch the model, or pre-seed
      WHISPER_MODEL_DIR with a downloaded model.
    - Poor accuracy: try WHISPER_MODEL=small.en (slower, ~460 MB), or a
      non-.en model for other languages.
"""

from __future__ import annotations

import io
import logging
import threading
from pathlib import Path
from typing import Any

from threedprompt.config import settings

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = frozenset({".webm", ".ogg", ".oga", ".wav", ".mp3", ".m4a", ".mp4"})


class TranscriptionError(Exception):
    """Speech-to-text couldn't run (missing dependency, model load failure, or undecodable audio)."""


_model: Any = None
_model_key: tuple[str, str, str, str] | None = None
_model_lock = threading.Lock()


def _model_dir() -> str:
    """Return where Whisper weights are cached: WHISPER_MODEL_DIR, else <OUTPUT_DIR>/whisper-models."""
    return settings.whisper_model_dir or str(Path(settings.output_dir) / "whisper-models")


def _get_model() -> Any:
    """
    Load (once) and return the faster-whisper model for the current settings.

    Returns: a faster_whisper.WhisperModel. Reloads only if the settings changed.
    Raises: TranscriptionError if faster-whisper is missing or the model can't load.
    """
    global _model, _model_key
    key = (settings.whisper_model, settings.whisper_device, settings.whisper_compute_type, _model_dir())
    with _model_lock:
        if _model is not None and _model_key == key:
            return _model
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise TranscriptionError(
                "faster-whisper is not installed - pip install -r requirements.txt to enable voice answers"
            ) from exc
        logger.info("loading whisper model %s (%s/%s)", key[0], key[1], key[2])
        try:
            _model = WhisperModel(key[0], device=key[1], compute_type=key[2], download_root=key[3])
        except Exception as exc:  # download, disk, or ctranslate2 errors all surface as one 503
            raise TranscriptionError(f"could not load whisper model {key[0]!r}: {exc}") from exc
        _model_key = key
        return _model


def transcribe(audio: bytes) -> str:
    """
    Transcribe recorded speech to text.

    Args:
        audio: the raw bytes of a recording in any FFmpeg-decodable format.
    Returns:
        The transcript with segments joined by spaces, or "" if no speech was heard.
    Raises:
        TranscriptionError if the model is unavailable or the audio can't be decoded.
    """
    if not audio:
        return ""
    model = _get_model()
    try:
        # vad_filter drops silence so a long pause before Stop doesn't make
        # Whisper hallucinate filler text.
        segments, _info = model.transcribe(io.BytesIO(audio), beam_size=1, vad_filter=True)
        return " ".join(segment.text.strip() for segment in segments if segment.text.strip())
    except Exception as exc:  # PyAV decode errors, corrupt uploads
        raise TranscriptionError(f"could not transcribe the recording: {exc}") from exc
