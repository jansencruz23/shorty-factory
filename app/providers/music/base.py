"""Port for music-bed generation.

Implementations produce an audio file matching a target duration. The
caller (the music graph node) is responsible for muxing it onto the
silent stitched video — that's compositing, not music.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class MusicProvider(Protocol):
    """Build an audio file of `duration` seconds at `dest`."""

    name: str

    async def build_track(
        self,
        duration: float,
        dest: Path,
        *,
        niche: str | None = None,
        track_override: str | None = None,
        music_prompt: str | None = None,
    ) -> Path:
        """Build a music bed.

        Resolution priority for the generative path (musicgen):
          1. track_override → caller forced a specific local file (local provider only)
          2. music_prompt   → LLM-tailored prompt from the storyboard (preferred)
          3. niche          → fallback to PROMPT_FOR_NICHE dict
          4. DEFAULT_PROMPT → final safety net

        The local provider ignores music_prompt entirely (it picks files, not prompts).
        """
        ...
