"""Storyboard base types — the protocol every mode implements and the
CaptionPlan contract the stitcher consumes.

Two CaptionPlan shapes are supported:

- persistent: a single caption burned across the entire concatenated video
  (narrative mode — POV caption stays on screen the whole time).

- progressive: one ProgressiveOverlay per clip, each describing the FULL
  on-screen layout for that clip (multi-color title + subtitle + a 5-row
  rank list where successive clips reveal more captions). Used by top-5
  countdown mode."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass
class TitleSegmentRender:
    """One colored chunk of a title line. The stitcher renders each segment
    as its own drawtext filter; multiple segments on the same line are laid
    out left-to-right with x positions computed from estimated text widths.

    color is a logical name ("white" or "accent"); the stitcher maps it to a
    concrete hex/named color so the palette stays consistent across overlays.
    """
    text: str
    color: str  # "white" or "accent"


@dataclass
class RankRow:
    """One row in the progressive rank list. The number is always shown;
    caption is None until the matching clip plays — that's how the cumulative
    reveal works (clip i populates rows 0..i, leaves rows i+1..N as numbers
    only)."""
    number: str           # e.g. "5."
    caption: str | None   # None when this rank hasn't been revealed yet


@dataclass
class ProgressiveOverlay:
    """The full caption overlay for ONE clip in a top-5 countdown.

    title_lines + subtitle are stable across all clips (the title doesn't
    change). rank_rows differs per clip — successive clips have one more
    row populated than the previous, so the viewer sees the list "fill up"
    as the countdown advances."""
    title_lines: list[list[TitleSegmentRender]]  # outer = lines, inner = colored segments
    subtitle: str | None
    rank_rows: list[RankRow]  # always 5 rows in display order (top → bottom)


@dataclass
class CaptionPlan:
    """Contract between the storyboard layer and the stitcher. Built by
    storyboard.build_caption_plan(). Exactly one of `persistent` or
    `progressive` should be set.

    - persistent: single caption burned across whole video (narrative)
    - progressive: per-clip overlays for top-5 countdown — title + subtitle
      stay constant, rank rows reveal cumulatively
    """
    persistent: str | None = None
    progressive: list[ProgressiveOverlay] | None = None


@runtime_checkable
class BaseStoryboard(Protocol):
    """Interface every storyboard variant implements so graph nodes don't
    branch on mode. Only node_compose ever sees the concrete class."""
    youtube_title: str
    youtube_description: str
    youtube_tags: list[str]
    music_prompt: str  # MusicGen prompt tailored to this video's tone + content

    def num_scenes(self) -> int: ...
    def prompt_for_scene(self, index: int) -> str: ...
    def build_caption_plan(self) -> CaptionPlan: ...
