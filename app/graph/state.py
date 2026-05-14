"""LangGraph runtime state.

Storyboard schemas live in app.graph.storyboards (one module per mode).
This file is just the TypedDict that flows through the graph."""

from __future__ import annotations

from typing import Literal, TypedDict

from app.graph.storyboards import Storyboard, TopFiveStoryboard


class JobState(TypedDict, total=False):
    job_id: str
    idea: str
    niche: str | None
    num_scenes: int
    mode: Literal["narrative", "top5"]
    pov_caption_override: str | None
    music_track: str | None
    music_mode: str

    # Either Storyboard or TopFiveStoryboard — both implement BaseStoryboard.
    # TypedDict can't enforce the protocol, but graph nodes only call protocol
    # methods so the union shape doesn't leak past node_compose.
    storyboard: Storyboard | TopFiveStoryboard
    clip_paths: list[str]
    stitched_path: str
    music_path: str
    final_path: str

    error: str | None
