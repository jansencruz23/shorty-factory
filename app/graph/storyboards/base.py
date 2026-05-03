"""Storyboard base types — the protocol every mode implements and the
CaptionPlan contract the stitcher consumes.

Keeping these in their own module avoids circular imports between the
package __init__ and per-mode submodules: narrative.py and top5.py both
import from base, and __init__ re-exports everything for callers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass
class CaptionPlan:
    """Contract between the storyboard layer and the stitcher. Built by
    storyboard.build_caption_plan(). Two flavors:

    - persistent: single caption burned across the whole video (narrative)
    - title + per_clip: fixed top title + a different caption per clip that
      switches at clip boundaries (top-5 countdown)
    """
    persistent: str | None = None
    title: str | None = None
    per_clip: list[str] | None = None


@runtime_checkable
class BaseStoryboard(Protocol):
    """Interface every storyboard variant implements so graph nodes don't
    branch on mode. Only node_compose ever sees the concrete class."""
    youtube_title: str
    youtube_description: str
    youtube_tags: list[str]

    def num_scenes(self) -> int: ...
    def prompt_for_scene(self, index: int) -> str: ...
    def build_caption_plan(self) -> CaptionPlan: ...
