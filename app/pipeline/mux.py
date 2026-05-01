"""ffmpeg compositing step: probe duration, then mux a music bed onto a
silent stitched video at the configured master gain.

Music *generation* lives in app.providers.music; this module is purely
about combining the two streams.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from langsmith import traceable

from app.config import settings

logger = logging.getLogger(__name__)


async def probe_duration(mp4: Path) -> float:
    args = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(mp4),
    ]
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {stderr.decode(errors='replace')}")
    return float(stdout.strip())


@traceable(name="pipeline.mux", run_type="tool")
async def mux(stitched_mp4: Path, music_track: Path, dest: Path) -> Path:
    """Combine the silent stitched video and the music bed into the final MP4.
    Applies the master gain from settings.music_gain_db."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    args = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(stitched_mp4),
        "-i",
        str(music_track),
        "-map",
        "0:v",
        "-map",
        "1:a",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-af",
        f"volume={settings.music_gain_db}dB",
        "-shortest",
        "-movflags",
        "+faststart",
        str(dest),
    ]
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg final mux failed: {stderr.decode(errors='replace')}")

    logger.info("final muxed -> %s", dest)
    return dest
