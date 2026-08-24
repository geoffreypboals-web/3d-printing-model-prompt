"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/src/threedprompt/logging_config.py
Description: Configures structured, level-aware logging for the whole
    service (rule 13: no silent failures, no scattered print()).
Inputs: settings.log_level from config.py
Outputs: Mutates the root logger's handlers/formatter/level as a side
    effect; provides get_logger() for module loggers.
Troubleshooting:
    - If logs appear twice, something else (e.g. uvicorn's own logging
      config) also attached a handler to the root logger; call
      configure_logging() only once, at process startup.
    - If secrets ever show up in logs, that's a bug per rule 8/13 - grep
      the offending log call and remove the sensitive field, don't just
      lower its log level.
"""

from __future__ import annotations

import logging

from threedprompt.config import settings

_CONFIGURED = False


def configure_logging() -> None:
    """Attach a single structured stream handler to the root logger, idempotently."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a module-scoped logger, ensuring logging is configured first."""
    configure_logging()
    return logging.getLogger(name)
