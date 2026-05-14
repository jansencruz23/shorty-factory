"""ffmpeg stitcher.

Takes N clips of arbitrary aspect ratios, normalises each to 1080x1920 with
a blurred-fill background (the standard Shorts/Reels aesthetic), concatenates
them, and burns in the captions described by a CaptionPlan. Audio is dropped
here — the mux step adds the music bed afterwards.

Two caption layouts are supported, picked by which CaptionPlan field is set:

- persistent (narrative mode): one caption burned across the whole video,
  positioned at h*0.22. Drawn post-concat (single drawtext chain on the
  concatenated stream).

- progressive (top-5 countdown): a ProgressiveOverlay per clip describing
  the FULL on-screen layout for that clip. Each overlay has:
    - title_lines: 1-2 lines of multi-color segments (white + accent red)
    - subtitle: a small tease line below the title
    - rank_rows: 5 stacked rows, with captions revealed cumulatively as the
      countdown advances (clip i populates rows 0..i; rows i+1..N show
      number-only)
  The full overlay is drawn per-input BEFORE concat — that way each clip
  carries its own state, and concat naturally splices them at boundaries.

One ffmpeg call with a complex filtergraph. Avoids intermediate files."""

from __future__ import annotations

import asyncio
import logging
import textwrap
from pathlib import Path

from langsmith import traceable

from app.config import resolve_caption_font, settings
from app.graph.storyboards import CaptionPlan
from app.graph.storyboards.base import ProgressiveOverlay

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────────────
# Color palette + sizing constants for the progressive layout. Kept here
# (not in settings) since they're tightly coupled to the filtergraph
# expressions below — moving them to config would just spread the magic
# numbers across two files.
# ────────────────────────────────────────────────────────────────────────

COLOR_WHITE = "white"
COLOR_ACCENT = "#FF3B3B"  # punchy red for the title accent phrase

# Font size ratios relative to settings.caption.font_size (which is the
# narrative-mode persistent caption size — the "main" text size).
SUBTITLE_FONT_RATIO = 0.5
RANK_FONT_RATIO = 0.85

# Pixel-width estimate for centering multi-segment lines. ffmpeg's text_w
# is per-drawtext, so we can't reference "total line width" inside the
# filter. We estimate in Python instead. 0.55 is a reasonable average for
# bold sans-serif at typical Shorts font sizes.
GLYPH_WIDTH_RATIO = 0.55

# Layout anchors as fractions of frame height. Tweaked by eye for 9:16.
TITLE_TOP_FRAC = 0.04          # title line 1 starts here
TITLE_LINE_LEADING = 1.2       # line height = font_size * leading
SUBTITLE_GAP_RATIO = 0.5       # gap between title bottom and subtitle (× subtitle font size)
RANK_BLOCK_TOP_FRAC = 0.28     # top of the rank list block
RANK_ROW_LEADING = 1.4         # row height = rank font_size * leading
RANK_LEFT_MARGIN_PX = 80       # left edge of the rank block
RANK_NUMBER_CAPTION_GAP_PX = 18  # gap between rank number and its caption


# ────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────


def _wrap_caption_lines(text: str, font_size: int | None = None) -> list[str]:
    """Word-wrap a caption to fit horizontally inside the 9:16 frame.

    Used by the persistent (narrative) caption only — progressive top-5
    overlays trust the LLM-provided line breaks and don't auto-wrap.
    """
    fs = font_size if font_size is not None else settings.caption.font_size
    avg_glyph_px = fs * GLYPH_WIDTH_RATIO
    usable_px = settings.video.width * 0.85
    max_chars = max(10, int(usable_px / avg_glyph_px))
    lines = textwrap.wrap(text.strip(), width=max_chars)
    return lines or [text.strip()]


def _estimate_text_width(text: str, font_size: int) -> int:
    """Rough pixel width of `text` at `font_size` in a bold sans-serif font.
    Used to center multi-segment lines where each segment has its own
    drawtext filter (and therefore its own private text_w that ffmpeg
    can't sum across filters)."""
    return int(len(text) * font_size * GLYPH_WIDTH_RATIO)


def _escape_filter_path(path: Path) -> str:
    """Escape a filesystem path for inclusion in a drawtext filter expression.
    Backslashes → forward slashes, then `:` (used as filter argument
    separator) → `\\:`."""
    return str(path).replace("\\", "/").replace(":", r"\:")


def _write_textfile(text: str, dest_dir: Path, name: str) -> Path:
    """Write a single line of caption text to its own file. Captions go
    through files (not inline) so ffmpeg metacharacters in the text never
    break the filter expression."""
    path = dest_dir / f"{name}.txt"
    path.write_text(text, encoding="utf-8")
    return path


def _drawtext(
    *,
    textfile: Path,
    fontfile: str,
    font_size: int,
    color: str,
    x_expr: str,
    y_expr: str,
) -> str:
    """Build one drawtext filter spec. All non-trivial text goes through
    `textfile=` (not `text=`) so caller-supplied content can never break
    the filter. fontcolor takes a name or a #RRGGBB hex."""
    return (
        f"drawtext=fontfile='{fontfile}':textfile='{_escape_filter_path(textfile)}':"
        f"fontsize={font_size}:fontcolor={color}:"
        # Black stroke around the glyphs for readability without a background
        # box — same look as pro Shorts captions.
        f"borderw=4:bordercolor=black:"
        f"x={x_expr}:y={y_expr}"
    )


# ────────────────────────────────────────────────────────────────────────
# Per-clip overlay drawtext chain (progressive top-5)
# ────────────────────────────────────────────────────────────────────────


def _color_for(segment_color: str) -> str:
    """Map a logical color name from the storyboard layer to a concrete
    ffmpeg color. 'accent' → red; everything else → white."""
    return COLOR_ACCENT if segment_color == "accent" else COLOR_WHITE


def _drawtext_chain_for_overlay(
    overlay: ProgressiveOverlay,
    clip_idx: int,
    font_path: Path,
    work_dir: Path,
) -> str:
    """Build the full drawtext chain (comma-joined) for one clip's overlay.

    Layout (from top of frame to bottom):
      - title_lines (1-2 lines of multi-color segments, centered)
      - subtitle (one line, white, smaller, centered)
      - rank_rows (5 stacked rows, left-aligned at RANK_LEFT_MARGIN_PX)
    """
    fontfile = _escape_filter_path(font_path)
    title_fs = settings.caption.font_size
    subtitle_fs = max(20, int(settings.caption.font_size * SUBTITLE_FONT_RATIO))
    rank_fs = max(24, int(settings.caption.font_size * RANK_FONT_RATIO))

    title_line_height = int(title_fs * TITLE_LINE_LEADING)
    rank_row_height = int(rank_fs * RANK_ROW_LEADING)

    H = settings.video.height
    W = settings.video.width

    title_top_y = int(H * TITLE_TOP_FRAC)
    drawtexts: list[str] = []

    # ── Title: per-line, per-segment ──
    # Each segment is its own drawtext (so it can have its own color). Within
    # a line, segments are positioned left-to-right with x=start_x+sum(prior
    # widths) so the whole line is roughly centered horizontally.
    for line_idx, segments in enumerate(overlay.title_lines):
        line_y = title_top_y + line_idx * title_line_height
        widths = [_estimate_text_width(seg.text, title_fs) for seg in segments]
        total_w = sum(widths)
        cursor_x = (W - total_w) // 2
        for seg_idx, seg in enumerate(segments):
            tf = _write_textfile(
                seg.text, work_dir, f"title_clip{clip_idx}_l{line_idx}_s{seg_idx}"
            )
            drawtexts.append(
                _drawtext(
                    textfile=tf,
                    fontfile=fontfile,
                    font_size=title_fs,
                    color=_color_for(seg.color),
                    x_expr=str(cursor_x),
                    y_expr=str(line_y),
                )
            )
            cursor_x += widths[seg_idx]

    title_block_bottom = title_top_y + len(overlay.title_lines) * title_line_height

    # ── Subtitle: centered, smaller font, white ──
    if overlay.subtitle:
        subtitle_y = title_block_bottom + int(subtitle_fs * SUBTITLE_GAP_RATIO)
        tf = _write_textfile(overlay.subtitle, work_dir, f"subtitle_clip{clip_idx}")
        drawtexts.append(
            _drawtext(
                textfile=tf,
                fontfile=fontfile,
                font_size=subtitle_fs,
                color=COLOR_WHITE,
                # ffmpeg-side centering: text_w is the THIS drawtext's own width.
                x_expr="(w-text_w)/2",
                y_expr=str(subtitle_y),
            )
        )

    # ── Rank rows: 5 stacked rows, left-aligned ──
    # Numbers always shown (in accent color); captions only for revealed rows.
    rank_top_y = int(H * RANK_BLOCK_TOP_FRAC)
    # Estimate the widest rank number ("5." vs "10.") so all captions align.
    # All single-digit ranks have the same width, but futureproof for >9.
    max_number_text = max((row.number for row in overlay.rank_rows), key=len, default="5.")
    number_width = _estimate_text_width(max_number_text, rank_fs)
    caption_x = RANK_LEFT_MARGIN_PX + number_width + RANK_NUMBER_CAPTION_GAP_PX

    for row_idx, row in enumerate(overlay.rank_rows):
        row_y = rank_top_y + row_idx * rank_row_height

        # Number — always shown, always accent color.
        tf_num = _write_textfile(
            row.number, work_dir, f"ranknum_clip{clip_idx}_p{row_idx}"
        )
        drawtexts.append(
            _drawtext(
                textfile=tf_num,
                fontfile=fontfile,
                font_size=rank_fs,
                color=COLOR_ACCENT,
                x_expr=str(RANK_LEFT_MARGIN_PX),
                y_expr=str(row_y),
            )
        )

        # Caption — only if this row is revealed for this clip. White.
        if row.caption:
            tf_cap = _write_textfile(
                row.caption, work_dir, f"rankcap_clip{clip_idx}_p{row_idx}"
            )
            drawtexts.append(
                _drawtext(
                    textfile=tf_cap,
                    fontfile=fontfile,
                    font_size=rank_fs,
                    color=COLOR_WHITE,
                    x_expr=str(caption_x),
                    y_expr=str(row_y),
                )
            )

    return ",".join(drawtexts)


# ────────────────────────────────────────────────────────────────────────
# Filtergraph builder
# ────────────────────────────────────────────────────────────────────────


def _persistent_drawtext_chain(
    textfiles: list[Path], font_path: Path, top_y_expr: str
) -> str:
    """Comma-joined drawtext chain for the narrative persistent caption.
    Each wrapped line gets its own drawtext so per-line centering works
    (text_w is computed per-filter, not max across lines)."""
    fontfile = _escape_filter_path(font_path)
    line_height = int(settings.caption.font_size * 1.2)
    parts: list[str] = []
    for i, tf in enumerate(textfiles):
        parts.append(
            _drawtext(
                textfile=tf,
                fontfile=fontfile,
                font_size=settings.caption.font_size,
                color=COLOR_WHITE,
                x_expr="(w-text_w)/2",
                y_expr=f"{top_y_expr}+{i * line_height}",
            )
        )
    return ",".join(parts)


def _build_filtergraph(
    num_inputs: int,
    persistent_files: list[Path] | None,
    progressive_overlays: list[ProgressiveOverlay] | None,
    font_path: Path,
    work_dir: Path,
) -> str:
    """Build the complex filtergraph for one ffmpeg invocation.

    Structure:
      Per-input: split into bg/fg, blur bg, scale fg, overlay → [norm{i}]
      If progressive: drawtext-chain per [norm{i}] → [v{i}]; else [norm{i}]
        feeds concat directly.
      Concat → [concat]
      If persistent: drawtext on [concat] → [out]; else null passthrough → [out]
    """
    W, H = settings.video.width, settings.video.height
    parts: list[str] = []

    # ── Per-input normalization (unchanged from previous shipped version) ──
    for i in range(num_inputs):
        parts.append(
            f"[{i}:v]split=2[bg{i}][fg{i}];"
            f"[bg{i}]scale={W}:{H}:force_original_aspect_ratio=increase,"
            f"crop={W}:{H},boxblur=20:1,setsar=1[bgblur{i}];"
            f"[fg{i}]scale={W}:{H}:force_original_aspect_ratio=decrease,setsar=1[fgs{i}];"
            f"[bgblur{i}][fgs{i}]overlay=(W-w)/2:(H-h)/2:format=auto,"
            f"fps=30,format=yuv420p[norm{i}]"
        )

    # ── Per-clip progressive overlay (top-5) ──
    # Drawn BEFORE concat so each clip carries its own state. When concat
    # splices, the overlay naturally switches at clip boundaries — no
    # timeline-based drawtext gymnastics needed.
    if progressive_overlays is not None:
        if len(progressive_overlays) != num_inputs:
            raise ValueError(
                f"progressive overlays ({len(progressive_overlays)}) must match "
                f"clip count ({num_inputs})"
            )
        for i, overlay in enumerate(progressive_overlays):
            chain = _drawtext_chain_for_overlay(overlay, i, font_path, work_dir)
            parts.append(f"[norm{i}]{chain}[v{i}]")
        concat_inputs = "".join(f"[v{i}]" for i in range(num_inputs))
    else:
        concat_inputs = "".join(f"[norm{i}]" for i in range(num_inputs))

    # ── Concat ──
    parts.append(f"{concat_inputs}concat=n={num_inputs}:v=1:a=0[concat]")

    # ── Final layer on the concatenated stream ──
    if persistent_files is not None:
        chain = _persistent_drawtext_chain(persistent_files, font_path, "h*0.22")
        parts.append(f"[concat]{chain}[out]")
    else:
        # No post-concat caption (progressive already drew everything per-input,
        # or there are simply no captions). Relabel concat → out so -map [out]
        # works.
        parts.append("[concat]null[out]")

    return ";".join(parts)


def _validate_plan(plan: CaptionPlan, num_clips: int) -> None:
    """Reject CaptionPlans that mix incompatible fields or have wrong
    progressive-overlay counts. These would otherwise produce nonsense
    filtergraphs."""
    if plan.persistent and plan.progressive:
        raise ValueError(
            "CaptionPlan: 'persistent' is mutually exclusive with 'progressive'"
        )
    if plan.progressive is not None and len(plan.progressive) != num_clips:
        raise ValueError(
            f"CaptionPlan.progressive has {len(plan.progressive)} overlays; "
            f"expected {num_clips} (one per input clip)"
        )


# ────────────────────────────────────────────────────────────────────────
# Public entry point
# ────────────────────────────────────────────────────────────────────────


@traceable(name="pipeline.stitch", run_type="tool")
async def stitch(clip_paths: list[Path], plan: CaptionPlan, dest: Path) -> Path:
    """Concat + 9:16 normalize + caption overlay. Writes muted MP4 to `dest`.

    `plan` describes the caption layer:
      - plan.persistent: single caption burned across whole video (narrative)
      - plan.progressive: per-clip overlays with multi-color title + subtitle
        + cumulative rank reveal (top-5 countdown)

    Both shapes go through the same ffmpeg call — only the filtergraph branches.
    """
    if not clip_paths:
        raise ValueError("stitch() requires at least one clip")
    _validate_plan(plan, len(clip_paths))

    dest.parent.mkdir(parents=True, exist_ok=True)
    font_path = resolve_caption_font()

    persistent_files: list[Path] | None = None
    if plan.persistent:
        # Reuse the existing wrap+per-line-textfile pattern for the persistent
        # narrative caption.
        lines = _wrap_caption_lines(plan.persistent)
        persistent_files = [
            _write_textfile(line, dest.parent, f"caption_{i}")
            for i, line in enumerate(lines)
        ]

    filtergraph = _build_filtergraph(
        num_inputs=len(clip_paths),
        persistent_files=persistent_files,
        progressive_overlays=plan.progressive,
        font_path=font_path,
        work_dir=dest.parent,
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

    # Diagnostic logging — describes the layout we picked so failures are
    # readable from the runner log alone.
    if plan.persistent:
        layout = f"persistent caption ({len(persistent_files or [])} line(s))"
    elif plan.progressive:
        n_overlays = len(plan.progressive)
        layout = f"progressive overlay × {n_overlays} (top-5 countdown)"
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
