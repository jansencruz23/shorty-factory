"""Storyboard (LLM output) and JobState (LangGraph state).
Storyboard.prompt_for_scene is where visual continuity across N independent
Meta generations is enforced — anchors are repeated verbatim, only the action changes."""

from __future__ import annotations
from typing import TypedDict
from pydantic import BaseModel, Field


class Storyboard(BaseModel):
    style_anchor: str = Field(
        ...,
        description="Color palette, lighting, lens aesthetic, mood. Reused verbatim every "
        "scene to lock visual identity. Do NOT include camera angles or movements here — "
        "those vary per scene via scene_shots.",
    )
    setting_anchor: str = Field(
        ...,
        description="Location, atmosphere, time of day. Reused verbatim every scene. "
        "World-level, not shot-level — describe the place, not the framing.",
    )
    character_anchors: str = Field(
        default="",
        description="Detailed character descriptions, comma-joined. Reused verbatim every "
        "scene. Empty string for pure-POV/landscape pieces.",
    )
    pov_caption: str = Field(
        ...,
        description="The single on-screen hook, e.g. 'POV: You are an astronaut'",
    )
    scene_actions: list[str] = Field(
        ...,
        description="Exactly N items. The peak kinetic beat for each ~5s clip — one strong "
        "verb that lands by second 3 of the clip.",
    )
    scene_shots: list[str] = Field(
        ...,
        description="Exactly N items, one per scene_action. Each describes the camera "
        "framing for that clip: shot type (wide/medium/close-up/extreme close-up), angle "
        "(low/high/dutch/bird's-eye/ground-level/over-the-shoulder), and movement "
        "(static/dolly-in/whip pan/tilt up/push in/pull back/handheld). VARY meaningfully "
        "across scenes — that's how the audience reads kinetic energy across cuts.",
    )

    def prompt_for_scene(self, index: int) -> str:
        # Lead with an explicit video directive — meta.ai's unified Create
        # tool routes by intent ("Describe an image or video..."), and
        # without this leading phrase it defaults to image generation.
        #
        # World layer (style/setting/characters) is locked — repeated verbatim
        # every clip so visual identity stays consistent. Shot layer (scene_shots)
        # varies per clip so the cuts feel like a real edit, not 8 stills of the
        # same frame.
        parts = ["Create a 5-second cinematic video", self.style_anchor, self.setting_anchor]
        if self.character_anchors:
            parts.append(self.character_anchors)
        parts.append(f"SHOT: {self.scene_shots[index]}")
        parts.append(f"ACTION: {self.scene_actions[index]}")
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
