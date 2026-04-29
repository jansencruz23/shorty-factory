"""Storyboard (LLM output) and JobState (LangGraph state).
Storyboard.prompt_for_scene is where visual continuity across N independent
Meta generations is enforced — anchors are repeated verbatim, only the action changes."""

from __future__ import annotations
from typing import TypedDict
from pydantic import BaseModel, Field


class Storyboard(BaseModel):
    style_anchor: str = Field(
        ...,
        description="Cinematography, palette, lightning. Reused verbatim every scene.",
    )
    setting_anchor: str = Field(
        ...,
        description="Location, atmosphere, time of day. Reused verbatim every scene.",
    )
    character_anchors: str = Field(
        default="",
        description="Detailed character descriptions, comma-joined. Reused verbatim every scene. "
        "Empty sctring for pure-POV/landscape pieces.",
    )
    pov_caption: str = Field(
        ...,
        description="The single on-screen hook, e.g. 'POV: You are an astronaut'",
    )
    scene_actions: list[str] = Field(
        ...,
        description="One short visual action per ~5s scene.",
    )

    def prompt_for_scene(self, index: int) -> str:
        action = self.scene_actions[index]
        parts = [self.style_anchor, self.setting_anchor]
        if self.character_anchors:
            parts.append(self.character_anchors)
        parts.append(f"SCENE: {action}")
        return ". ".join(p.rstrip(". ") for p in parts) + "."


class JobState(TypedDict, total=False):
    job_id: str
    idea: str
    niche: str | None
    num_scenes: int
    pov_caption_override: str | None
    music_track: str | None
    music_mode: str

    storyboard: Storyboard
    clip_paths: list[str]
    stitched_path: str
    music_path: str
    final_path: str

    error: str | None
