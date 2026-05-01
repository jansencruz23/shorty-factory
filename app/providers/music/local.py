"""Local-file music adapter.

Picks a track from `assets/music/<niche>/` (or `assets/music/`), loops and
trims it to the target duration with fade in/out. No Content ID risk if
the source library is licensed cleanly.
"""

from __future__ import annotations

import asyncio
import logging
import random
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)


def _pick_track(niche: str | None, override: str | None) -> Path:
    music_root = settings.assets_dir / "music"
    if override:
        candidate = music_root / override
        if not candidate.exists():
            raise FileNotFoundError(f"music_track '{override}' not found at {candidate}")
        return candidate

    search_dirs: list[Path] = []
    if niche:
        search_dirs.append(music_root / niche)
    search_dirs.append(music_root)

    for d in search_dirs:
        if not d.exists():
            continue
        tracks = sorted(
            p
            for p in d.iterdir()
            if p.is_file() and p.suffix.lower() in {".mp3", ".m4a", ".wav", ".ogg", ".flac"}
        )
        if tracks:
            return random.choice(tracks)

    raise FileNotFoundError(
        f"No music tracks found under {music_root}. Drop a few royalty-free "
        f"tracks into assets/music/ (or assets/music/<niche>/)."
    )


class LocalLibraryMusicProvider:
    name = "local"

    async def build_track(
        self,
        duration: float,
        dest: Path,
        *,
        niche: str | None = None,
        track_override: str | None = None,
    ) -> Path:
        src = _pick_track(niche, track_override)
        fade = 1.5
        # `-stream_loop -1 -i src` loops the input forever; `-t duration`
        # trims the output to the exact length we need.
        args = [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-stream_loop",
            "-1",
            "-i",
            str(src),
            "-t",
            f"{duration:.3f}",
            "-af",
            f"afade=t=in:st=0:d={fade},afade=t=out:st={duration - fade:.3f}:d={fade}",
            "-c:a",
            "libmp3lame",
            "-b:a",
            "192k",
            str(dest),
        ]
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg music import failed: {stderr.decode(errors='replace')}")
        logger.info("imported music %s -> %s (%.1fs)", src.name, dest.name, duration)
        return dest
