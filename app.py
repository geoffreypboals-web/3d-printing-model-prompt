"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/app.py
Description: FastAPI web app -- takes a plain-language description of a
    3D-printable object, builds a validated, printer-aware prompt
    (src/prompt_builder.py), and sends it to the configured LLM backend
    (src/llm_client.py, Ollama by default per CLAUDE.md rule 6) to get
    back a generated model description/spec.
Inputs: HTTP requests -- GET / (the form), GET /health, POST /api/generate
    {text, max_size_mm?}; environment variables via src/config.py.
Outputs: JSON {prompt, result} on success; a 400 with an actionable
    detail message for invalid input; a 502 with an actionable detail
    message when the LLM backend is unreachable/misconfigured.
Troubleshooting:
    - GET /health always returns 200 {"status": "ok"} if the process is
      alive at all -- a failure to even reach that means the container
      itself didn't start; check `docker compose logs app`, not this file.
    - A 502 from /api/generate means the LLM backend itself failed --
      see src/llm_client.py's own troubleshooting notes for that.
"""
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.config import load_settings
from src.llm_client import LLMError, get_client
from src.prompt_builder import PromptValidationError, build_spec

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("model_prompt")

APP_ROOT = Path(__file__).resolve().parent
STATIC_DIR = APP_ROOT / "static"

app = FastAPI(title="3D Printing Model Prompt")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


class GenerateIn(BaseModel):
    text: str
    max_size_mm: float | None = None


@app.get("/")
def index():
    """Serves the one static form page -- no client-side framework, just
    fetch() against /api/generate (see static/app.js)."""
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/health")
def health():
    """Liveness only -- doesn't check the LLM backend (that's a runtime
    dependency, not this process's own health; see README.md's
    "Dependencies" section for how a down Ollama is surfaced instead)."""
    return {"status": "ok"}


@app.post("/api/generate")
async def generate(body: GenerateIn):
    """Builds and validates the prompt, then asks the configured backend
    to generate a result. A validation failure never reaches the LLM
    backend at all (fails fast, no wasted request); a backend failure is
    reported as 502 with the backend's own reason, logged at error level
    per CLAUDE.md rule 13 (never a silently swallowed exception)."""
    settings = load_settings()
    try:
        spec = build_spec(body.text, settings, printer_max_size_mm=body.max_size_mm)
    except PromptValidationError as exc:
        raise HTTPException(400, str(exc)) from exc

    try:
        client = get_client(settings)
        result = await client.generate(spec.prompt_text)
    except LLMError as exc:
        logger.error("LLM generation failed (provider=%s): %s", settings.llm_provider, exc)
        raise HTTPException(502, str(exc)) from exc

    return {"prompt": spec.prompt_text, "result": result}
