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
    ) -> Path: ...
