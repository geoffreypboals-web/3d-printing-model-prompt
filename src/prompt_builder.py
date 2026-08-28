"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/src/prompt_builder.py
Description: Builds structured prompts for generating 3D-printable model
    descriptions from user input, and validates the resulting spec before
    it's sent to the generation backend.
Inputs: Raw user prompt text, this project's Settings (for the character
    limit and default printer size), and an optional per-request printer
    max build size in millimeters.
Outputs: A validated PromptSpec ready for src/llm_client.py, or raises
    PromptValidationError with a specific, actionable reason.
Troubleshooting:
    - If validation keeps rejecting valid input, check MAX_MODEL_SIZE_MM
      in .env matches the printer profile actually being used.
    - If the built prompt looks truncated, check MAX_PROMPT_CHARS in
      .env -- raw input longer than that is rejected outright rather
      than silently cut, so a truncated result means the LLM itself cut
      its own response short, not this module.
"""
from dataclasses import dataclass

from .config import Settings


class PromptValidationError(ValueError):
    """Raised for any invalid input. Always carries a human-readable,
    actionable reason -- app.py surfaces this message directly in a 400
    response, so it's written for a person, not a developer."""


@dataclass(frozen=True)
class PromptSpec:
    """The validated result of build_spec() -- everything src/llm_client.py
    needs, with nothing left for it to re-validate."""

    raw_input: str
    max_size_mm: float
    prompt_text: str


def build_spec(raw_input: str, settings: Settings, printer_max_size_mm: float | None = None) -> PromptSpec:
    """Validates raw_input against length and size constraints, then
    formats it into the structured prompt template the LLM backend
    expects. Never swallows bad input -- empty or oversized text raises
    immediately instead of producing an empty/truncated PromptSpec that
    downstream code would have to guess about.

    printer_max_size_mm overrides settings.max_model_size_mm for one call
    (a per-request printer profile) -- pass None to use the configured
    default."""
    text = raw_input.strip()
    if not text:
        raise PromptValidationError("Describe the object you want printed -- the prompt can't be empty.")
    if len(text) > settings.max_prompt_chars:
        raise PromptValidationError(
            f"Prompt is {len(text)} characters, over the {settings.max_prompt_chars}-character limit -- "
            "shorten the description."
        )

    max_size = printer_max_size_mm if printer_max_size_mm is not None else settings.max_model_size_mm
    if max_size <= 0:
        raise PromptValidationError("Printer max build size must be a positive number of millimeters.")

    prompt_text = (
        "You are a 3D-printable model design assistant. Produce a concise, "
        "physically printable design description for the following request. "
        f"The model must fit within a {max_size:.0f}mm cube (the target printer's "
        "build volume) -- call out any dimension that would exceed it and suggest "
        "how to split the model into printable parts instead.\n\n"
        f"Request: {text}"
    )
    return PromptSpec(raw_input=text, max_size_mm=max_size, prompt_text=prompt_text)
