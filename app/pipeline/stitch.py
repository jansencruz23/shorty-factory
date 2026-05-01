"""ffmpeg stitcher.

Takes N clips of arbitrary aspect ratios, normalises each to 1080x1920 with
a blurred-fill background (the standard Shorts/Reels aesthetic), concatenates
them, and burns in a single persistent POV caption overlay. Audio is dropped
here — the mux step adds the music bed afterwards.

One ffmpeg call with a complex filtergraph. Avoids intermediate files.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from langsmith import traceable

from app.config import resolve_caption_font, settings

logger = logging.getLogger(__name__)


def _build_filtergraph(num_inputs: int, caption_textfile: Path, font_path: Path) -> str:
    W, H = settings.video.width, settings.video.height
    parts: list[str] = []

    # Per-input: split into bg/fg, blur bg, scale fg to fit, overlay.
    for i in range(num_inputs):
        parts.append(
            f"[{i}:v]split=2[bg{i}][fg{i}];"
            f"[bg{i}]scale={W}:{H}:force_original_aspect_ratio=increase,"
            f"crop={W}:{H},boxblur=20:1,setsar=1[bgblur{i}];"
            f"[fg{i}]scale={W}:{H}:force_original_aspect_ratio=decrease,setsar=1[fgs{i}];"
            f"[bgblur{i}][fgs{i}]overlay=(W-w)/2:(H-h)/2:format=auto,"
            f"fps=30,format=yuv420p[v{i}]"
        )

    # Concat all normalised streams.
    concat_inputs = "".join(f"[v{i}]" for i in range(num_inputs))
    parts.append(f"{concat_inputs}concat=n={num_inputs}:v=1:a=0[concat]")

    # Persistent POV caption — text comes from a file so we don't have to
    # escape ffmpeg's drawtext metacharacters. Paths still need : escaped
    # because of Windows drive letters.
    fontfile = str(font_path).replace("\\", "/").replace(":", r"\:")
    textfile = str(caption_textfile).replace("\\", "/").replace(":", r"\:")
    parts.append(
        f"[concat]drawtext=fontfile='{fontfile}':textfile='{textfile}':"
        f"fontsize={settings.caption.font_size}:fontcolor=white:"
        # No background box — keep readability via a black stroke around
        # the glyphs (same look as pro Shorts captions).
        f"borderw=4:bordercolor=black:"
        f"x=(w-text_w)/2:y=h*0.22[out]"
    )

    return ";".join(parts)


@traceable(name="pipeline.stitch", run_type="tool")
async def stitch(clip_paths: list[Path], pov_caption: str, dest: Path) -> Path:
    """Concat + 9:16 normalize + POV caption overlay. Writes muted MP4 to `dest`."""
    if not clip_paths:
        raise ValueError("stitch() requires at least one clip")

    dest.parent.mkdir(parents=True, exist_ok=True)
    font_path = resolve_caption_font()

    # Caption goes through a textfile so ':' and quotes don't break the filter.
    caption_textfile = dest.parent / "caption.txt"
    caption_textfile.write_text(pov_caption.strip(), encoding="utf-8")

    filtergraph = _build_filtergraph(len(clip_paths), caption_textfile, font_path)

    args: list[str] = ["ffmpeg", "-y", "-loglevel", "error"]
    for clip in clip_paths:
        args += ["-i", str(clip)]
    args += [
        "-filter_complex",
        filtergraph,
        "-map",
        "[out]",
        "-an",  # drop audio; mux step adds the bed
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(dest),
    ]

    logger.info("stitching %d clips -> %s", len(clip_paths), dest)
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg stitch failed (exit {proc.returncode}):\n{stderr.decode(errors='replace')}"
        )

    return dest
