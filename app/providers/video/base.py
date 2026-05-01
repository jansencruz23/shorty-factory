"""Port for video-clip generation.

Concrete adapters (meta_ai, runway, pika, …) implement this Protocol. The
graph nodes only ever depend on the Protocol — they don't import any
adapter directly. The factory at app.providers.video.__init__ resolves
the configured provider name to an instance.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Protocol


ProgressCb = Callable[[int], Awaitable[None]]


class VideoProvider(Protocol):
    """Generate one clip per prompt, sequentially or concurrently — the
    adapter decides. Caller does not assume any internal model (browser,
    HTTP, queue, etc.)."""

    name: str

    async def generate_clips(
        self,
        prompts: list[str],
        clip_path_for: Callable[[int], Path],
        progress_cb: ProgressCb | None = None,
    ) -> list[Path]: ...
