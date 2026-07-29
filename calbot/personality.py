"""Repository-owned personality guidance for model-generated replies."""

from __future__ import annotations

import logging
from pathlib import Path


log = logging.getLogger("assistant-bot")
MAX_PERSONALITY_CHARS = 8000
PERSONALITY_PATH = Path(__file__).resolve().parent.parent / "PERSONALITY.md"
DEFAULT_PERSONALITY = "Warm, concise, natural, and helpful."


def load_personality(path: Path = PERSONALITY_PATH) -> str:
    """Load bounded tone guidance without making it an authority source."""
    try:
        personality = path.read_text(encoding="utf-8").strip()
    except OSError:
        log.warning("Could not load personality file; using the default")
        return DEFAULT_PERSONALITY
    if not personality:
        return DEFAULT_PERSONALITY
    if len(personality) > MAX_PERSONALITY_CHARS:
        log.warning(
            "Personality file exceeded %s characters and was truncated",
            MAX_PERSONALITY_CHARS,
        )
        return personality[:MAX_PERSONALITY_CHARS].rstrip()
    return personality
