"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/src/threedprompt/storage.py
Description: Local filesystem storage for generated models. Each model
    gets a UUID directory under OUTPUT_DIR holding model.stl, its source
    (model.scad or build.py) when kept, and a spec.json sidecar recording
    how it was made - so thickness.py can decide whether to regenerate
    from source or shell the mesh. Also implements the prompt -> model_id
    cache required by rule 10 (don't pay for the same generation twice).
Inputs: settings.output_dir from config.py; GenerationResult objects from
    the generator backends.
Outputs: Model directories/files on disk; spec dicts read back by the API
    and thickness.py.
Troubleshooting:
    - "model not found": model_id must be a value previously returned by
      POST /generate - check OUTPUT_DIR wasn't cleared/rotated between
      requests (it's a plain directory, not a database - see rule 18,
      there is no backup for it beyond whatever backs OUTPUT_DIR itself).
    - Cache hits are keyed on the exact prompt + wall_thickness_mm pair;
      trivially different wording (e.g. an extra space) is treated as a
      cache miss, since we're not doing semantic matching here.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from pathlib import Path
from typing import Any

from threedprompt.config import settings
from threedprompt.logging_config import get_logger

logger = get_logger(__name__)

_SPEC_FILENAME = "spec.json"
_CACHE_INDEX_FILENAME = "_prompt_cache_index.json"


def _output_root() -> Path:
    root = Path(settings.output_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root


def new_model_dir() -> tuple[str, Path]:
    """Allocate a fresh UUID model directory and return (model_id, path)."""
    model_id = uuid.uuid4().hex
    model_dir = _output_root() / model_id
    model_dir.mkdir(parents=True, exist_ok=False)
    return model_id, model_dir


def model_dir(model_id: str) -> Path:
    """Return the directory for an existing model_id, raising FileNotFoundError if unknown."""
    path = _output_root() / model_id
    if not path.is_dir():
        raise FileNotFoundError(f"no model with id {model_id!r}")
    return path


def stl_path(model_id: str) -> Path:
    """Return the STL path for an existing model, raising FileNotFoundError if the model or file is missing."""
    path = model_dir(model_id) / "model.stl"
    if not path.is_file():
        raise FileNotFoundError(f"model {model_id!r} has no model.stl")
    return path


def save_spec(model_id: str, spec: dict[str, Any]) -> None:
    """Persist the generation spec sidecar for a model (backend, source paths, prompt, etc.)."""
    (model_dir(model_id) / _SPEC_FILENAME).write_text(json.dumps(spec, indent=2))


def load_spec(model_id: str) -> dict[str, Any] | None:
    """Load a model's spec sidecar, or None if it was never saved (e.g. a raw user upload)."""
    spec_file = model_dir(model_id) / _SPEC_FILENAME
    if not spec_file.is_file():
        return None
    return json.loads(spec_file.read_text())


def save_upload(filename: str, content: bytes) -> tuple[str, Path]:
    """Store a user-uploaded mesh file (no spec - origin unknown) under a fresh model_id."""
    model_id, directory = new_model_dir()
    suffix = Path(filename).suffix.lower() or ".stl"
    dest = directory / f"model{suffix}"
    dest.write_bytes(content)
    return model_id, dest


def _cache_key(prompt: str, wall_thickness_mm: float | None) -> str:
    """Deterministic cache key for a (prompt, wall_thickness) pair."""
    raw = f"{prompt}|{wall_thickness_mm}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _cache_index_path() -> Path:
    return _output_root() / _CACHE_INDEX_FILENAME


def lookup_cached_model(prompt: str, wall_thickness_mm: float | None) -> str | None:
    """Return a previously generated model_id for an identical prompt, if the cache is enabled and it exists."""
    if not settings.cache_enabled:
        return None
    index_path = _cache_index_path()
    if not index_path.is_file():
        return None
    index = json.loads(index_path.read_text())
    model_id = index.get(_cache_key(prompt, wall_thickness_mm))
    if model_id and (_output_root() / model_id / "model.stl").is_file():
        logger.info("Cache hit for prompt (model_id=%s); skipping regeneration.", model_id)
        return model_id
    return None


def remember_cached_model(prompt: str, wall_thickness_mm: float | None, model_id: str) -> None:
    """Record a prompt -> model_id mapping so identical future requests skip regeneration (rule 10)."""
    if not settings.cache_enabled:
        return
    index_path = _cache_index_path()
    index: dict[str, str] = json.loads(index_path.read_text()) if index_path.is_file() else {}
    index[_cache_key(prompt, wall_thickness_mm)] = model_id
    index_path.write_text(json.dumps(index, indent=2))


def copy_into_new_model(source_dir: Path, spec: dict[str, Any] | None = None) -> tuple[str, Path]:
    """Copy an existing model directory's contents into a new model_id directory (used after thickening)."""
    model_id, dest_dir = new_model_dir()
    for item in source_dir.iterdir():
        if item.name == _SPEC_FILENAME:
            continue
        if item.is_file():
            shutil.copy2(item, dest_dir / item.name)
    if spec is not None:
        save_spec(model_id, spec)
    return model_id, dest_dir
