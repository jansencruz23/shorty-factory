"""ffmpeg stitcher.

Takes N clips of arbitrary aspect ratios, normalises each to 1080x1920 with
a blurred-fill background (the standard Shorts/Reels aesthetic), concatenates
them, and burns in the captions described by a CaptionPlan. Audio is dropped
here — the mux step adds the music bed afterwards.

Two caption layouts are supported, picked by which fields the storyboard
populated on its CaptionPlan:

- persistent (narrative mode): one caption burned across the whole video,
  positioned at h*0.22. Same look the project has shipped from day one.

- title + per_clip (top-5 mode): the title pinned at the top of the frame
  across all clips, plus a per-clip rank caption that switches at clip
  boundaries (drawn before concat so each clip carries its own caption).

One ffmpeg call with a complex filtergraph. Avoids intermediate files."""

from __future__ import annotations

import asyncio
import logging
import textwrap
from pathlib import Path

from langsmith import traceable

from app.config import resolve_caption_font, settings
from app.graph.storyboards import CaptionPlan

logger = logging.getLogger(__name__)


def _wrap_caption_lines(text: str) -> list[str]:
    """Word-wrap a caption to fit horizontally inside the 9:16 frame.

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


def _write_textfiles(lines: list[str], dest_dir: Path, prefix: str) -> list[Path]:
    """Write each caption line to its own .txt file. Captions go through
    files (not inline) so ffmpeg metacharacters in the text never break the
    filter expression. Returns one path per line."""
    files: list[Path] = []
    for i, line in enumerate(lines):
        path = dest_dir / f"{prefix}_{i}.txt"
        path.write_text(line, encoding="utf-8")
        files.append(path)
    return files


def _drawtext_chain(textfiles: list[Path], font_path: Path, top_y_expr: str) -> str:
    """Comma-joined drawtext filter chain. Each textfile becomes one centered
    line; lines stack downward from `top_y_expr` with 1.2× leading. Per-line
    filters (rather than a single multi-line one) so each line is independently
    centered — text_w is computed per-filter, not max across lines."""
    fontfile = str(font_path).replace("\\", "/").replace(":", r"\:")
    line_height = int(settings.caption.font_size * 1.2)
    parts: list[str] = []
    for i, tf in enumerate(textfiles):
        textfile_path = str(tf).replace("\\", "/").replace(":", r"\:")
        parts.append(
            f"drawtext=fontfile='{fontfile}':textfile='{textfile_path}':"
            f"fontsize={settings.caption.font_size}:fontcolor=white:"
            # No background box — keep readability via a black stroke around
            # the glyphs (same look as pro Shorts captions).
            f"borderw=4:bordercolor=black:"
            f"x=(w-text_w)/2:y={top_y_expr}+{i * line_height}"
        )
    return ",".join(parts)


def _build_filtergraph(
    num_inputs: int,
    persistent_files: list[Path] | None,
    title_files: list[Path] | None,
    per_clip_files: list[list[Path]] | None,
    font_path: Path,
) -> str:
    """Build the complex filtergraph string for one ffmpeg invocation.

    Structure:
      Per-input: split into bg/fg, blur bg, scale fg, overlay -> [norm{i}]
      If per-clip captions: drawtext on each [norm{i}] -> [v{i}]; else
        [norm{i}] feeds concat directly.
      Concat -> [concat]
      Persistent caption OR title (mutually exclusive) drawn on [concat]
        -> [out]. If neither, null filter passthrough.
    """
    W, H = settings.video.width, settings.video.height
    line_height = int(settings.caption.font_size * 1.2)
    parts: list[str] = []

    # Per-input: split into bg/fg, blur bg, scale fg to fit, overlay.
    for i in range(num_inputs):
        parts.append(
            f"[{i}:v]split=2[bg{i}][fg{i}];"
            f"[bg{i}]scale={W}:{H}:force_original_aspect_ratio=increase,"
            f"crop={W}:{H},boxblur=20:1,setsar=1[bgblur{i}];"
            f"[fg{i}]scale={W}:{H}:force_original_aspect_ratio=decrease,setsar=1[fgs{i}];"
            f"[bgblur{i}][fgs{i}]overlay=(W-w)/2:(H-h)/2:format=auto,"
            f"fps=30,format=yuv420p[norm{i}]"
        )

    # Per-clip captions: drawn on each input BEFORE concat, so each clip
    # carries its own caption baked into the duration of that clip — the
    # caption naturally switches at clip boundaries when concat joins them.
    # Anchored near the top, just below the title's reserved zone.
    if per_clip_files is not None:
        # Per-clip captions sit just below the title block. If a title is
        # also present we offset the per-clip top so it doesn't overlap;
        # otherwise the per-clip caption starts at h*0.16.
        title_line_count = len(title_files) if title_files else 0
        title_block_px = title_line_count * line_height
        # Small gap (half a line height) between title and per-clip block.
        gap_px = int(settings.caption.font_size * 0.5)
        per_clip_top_offset_px = int(settings.video.height * 0.06) + title_block_px + gap_px
        per_clip_top_y_expr = f"{per_clip_top_offset_px}"
        for i in range(num_inputs):
            chain = _drawtext_chain(per_clip_files[i], font_path, per_clip_top_y_expr)
            parts.append(f"[norm{i}]{chain}[v{i}]")
        concat_inputs = "".join(f"[v{i}]" for i in range(num_inputs))
    else:
        concat_inputs = "".join(f"[norm{i}]" for i in range(num_inputs))

    # Concat all normalised streams.
    parts.append(f"{concat_inputs}concat=n={num_inputs}:v=1:a=0[concat]")

    # Final caption layer on the concatenated stream. persistent and title
    # are mutually exclusive (validated upstream by stitch()).
    if persistent_files is not None:
        chain = _drawtext_chain(persistent_files, font_path, "h*0.22")
        parts.append(f"[concat]{chain}[out]")
    elif title_files is not None:
        chain = _drawtext_chain(title_files, font_path, "h*0.06")
        parts.append(f"[concat]{chain}[out]")
    else:
        # No caption layers at all — just relabel concat -> out so -map [out]
        # works.
        parts.append("[concat]null[out]")

    return ";".join(parts)


def _validate_plan(plan: CaptionPlan, num_clips: int) -> None:
    """Reject CaptionPlans that mix incompatible fields or have wrong
    per-clip caption counts. These would otherwise produce nonsense
    filtergraphs."""
    if plan.persistent and (plan.title or plan.per_clip):
        raise ValueError(
            "CaptionPlan: 'persistent' is mutually exclusive with 'title'/'per_clip'"
        )
    if plan.per_clip is not None and len(plan.per_clip) != num_clips:
        raise ValueError(
            f"CaptionPlan.per_clip has {len(plan.per_clip)} captions; "
            f"expected {num_clips} (one per input clip)"
        )


@traceable(name="pipeline.stitch", run_type="tool")
async def stitch(clip_paths: list[Path], plan: CaptionPlan, dest: Path) -> Path:
    """Concat + 9:16 normalize + caption overlay. Writes muted MP4 to `dest`.

    `plan` describes the caption layer:
      - plan.persistent: single caption burned across whole video (narrative)
      - plan.title + plan.per_clip: title at top + switching rank caption
        on each clip (top-5 countdown)
    Both shapes go through the same ffmpeg call — only the filtergraph branches.
    """
    if not clip_paths:
        raise ValueError("stitch() requires at least one clip")
    _validate_plan(plan, len(clip_paths))

    dest.parent.mkdir(parents=True, exist_ok=True)
    font_path = resolve_caption_font()

    persistent_files: list[Path] | None = None
    title_files: list[Path] | None = None
    per_clip_files: list[list[Path]] | None = None

    if plan.persistent:
        persistent_files = _write_textfiles(
            _wrap_caption_lines(plan.persistent), dest.parent, "caption"
        )
    if plan.title:
        title_files = _write_textfiles(
            _wrap_caption_lines(plan.title), dest.parent, "title"
        )
    if plan.per_clip is not None:
        per_clip_files = [
            _write_textfiles(_wrap_caption_lines(cap), dest.parent, f"clip_{i}")
            for i, cap in enumerate(plan.per_clip)
        ]

    filtergraph = _build_filtergraph(
        num_inputs=len(clip_paths),
        persistent_files=persistent_files,
        title_files=title_files,
        per_clip_files=per_clip_files,
        font_path=font_path,
    )

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

    # Logging: describe the layout we picked so failures are diagnosable
    # from the runner log alone (no ffmpeg log digging).
    if plan.persistent:
        layout = f"persistent caption ({len(persistent_files)} line(s))"
    elif plan.title and plan.per_clip:
        layout = (
            f"title ({len(title_files)} line(s)) + per-clip captions "
            f"({len(plan.per_clip)})"
        )
    else:
        layout = "no captions"
    logger.info("stitching %d clips with %s -> %s", len(clip_paths), layout, dest)

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
