"""ffmpeg stitcher.

Takes N clips of arbitrary aspect ratios, normalises each to 1080x1920 with
a blurred-fill background (the standard Shorts/Reels aesthetic), concatenates
them, and burns in a persistent POV caption overlay (auto-wrapped to fit the
9:16 frame). Audio is dropped here — the mux step adds the music bed
afterwards.

One ffmpeg call with a complex filtergraph. Avoids intermediate files.
"""

from __future__ import annotations

import asyncio
import logging
import textwrap
from pathlib import Path

from langsmith import traceable

from app.config import resolve_caption_font, settings

logger = logging.getLogger(__name__)


def _wrap_caption_lines(text: str) -> list[str]:
    """Word-wrap the POV caption to fit horizontally inside the 9:16 frame.

    Average glyph width for a bold sans-serif is ~0.5 × fontsize. We aim for
    85% of the frame width so the text never kisses the edge — that leaves
    ~7-8% margin per side, which reads cleanly on phone screens without
    forcing two-line wraps on borderline-short captions.
    """
    avg_glyph_px = settings.caption.font_size * 0.5
    usable_px = settings.video.width * 0.85
    max_chars = max(10, int(usable_px / avg_glyph_px))
    lines = textwrap.wrap(text.strip(), width=max_chars)
    return lines or [text.strip()]


def _build_filtergraph(num_inputs: int, line_textfiles: list[Path], font_path: Path) -> str:
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

    # Persistent POV caption — one drawtext filter per wrapped line so each
    # line is independently centered (text_w is computed per-filter, not
    # max across lines). Lines stack downward from h*0.22 with 1.2× leading.
    # Text comes from per-line files so ffmpeg metacharacters in captions
    # don't break the filter; paths still need ':' escaped on Windows.
    fontfile = str(font_path).replace("\\", "/").replace(":", r"\:")
    line_height = int(settings.caption.font_size * 1.2)
    drawtext_filters: list[str] = []
    for i, line_file in enumerate(line_textfiles):
        line_textfile = str(line_file).replace("\\", "/").replace(":", r"\:")
        drawtext_filters.append(
            f"drawtext=fontfile='{fontfile}':textfile='{line_textfile}':"
            f"fontsize={settings.caption.font_size}:fontcolor=white:"
            # No background box — keep readability via a black stroke around
            # the glyphs (same look as pro Shorts captions).
            f"borderw=4:bordercolor=black:"
            f"x=(w-text_w)/2:y=h*0.22+{i * line_height}"
        )
    parts.append(f"[concat]{','.join(drawtext_filters)}[out]")

    return ";".join(parts)


@traceable(name="pipeline.stitch", run_type="tool")
async def stitch(clip_paths: list[Path], pov_caption: str, dest: Path) -> Path:
    """Concat + 9:16 normalize + POV caption overlay. Writes muted MP4 to `dest`."""
    if not clip_paths:
        raise ValueError("stitch() requires at least one clip")

    dest.parent.mkdir(parents=True, exist_ok=True)
    font_path = resolve_caption_font()

    # Wrap the caption to fit the 9:16 frame, then write one textfile per
    # line so each line is rendered with its own drawtext filter (per-line
    # centering). Captions go through files so ':' and quotes in the text
    # don't break the filter expression.
    lines = _wrap_caption_lines(pov_caption)
    line_textfiles: list[Path] = []
    for i, line in enumerate(lines):
        path = dest.parent / f"caption_{i}.txt"
        path.write_text(line, encoding="utf-8")
        line_textfiles.append(path)

    filtergraph = _build_filtergraph(len(clip_paths), line_textfiles, font_path)

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

    logger.info(
        "stitching %d clips with %d-line caption -> %s",
        len(clip_paths),
        len(lines),
        dest,
    )
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
