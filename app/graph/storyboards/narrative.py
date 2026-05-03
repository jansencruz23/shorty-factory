"""Narrative-mode storyboard.

One connected story arc with a twist. Visual continuity comes from world-layer
anchors (style, setting, characters) being repeated VERBATIM in every scene's
meta.ai prompt — meta.ai has no memory between generations, so identical
anchor strings is the only way to get a consistent character/look across cuts.
Shot-layer (scene_shots) varies per clip so the cuts feel cinematic."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.graph.storyboards.base import CaptionPlan


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
        description="Detailed character descriptions, comma-joined: age, ethnicity, hair, "
        "distinguishing marks, exact wardrobe, weapon. Reused verbatim every scene so "
        "meta.ai produces a visually-consistent character across cuts. Empty string for "
        "pure-landscape pieces or when no character should appear.",
    )
    pov_caption: str = Field(
        ...,
        description="The single on-screen hook, e.g. 'POV: You are an astronaut'",
    )
    twist_premise: str = Field(
        ...,
        description="One sentence describing the reversal/reveal that the final scene "
        "delivers. Should fundamentally change how the viewer reads the earlier scenes "
        "after seeing the last clip. The composer uses this to plant foreshadowing in "
        "scene_actions[0..N-2]. Not shown to the viewer — it's a story-design artifact.",
    )
    youtube_title: str = Field(
        ...,
        description="YouTube Shorts title, max 60 chars. Lead with the niche keyword "
        "(Tikbalang, Aswang, etc.). Hint at the twist without spoiling. No ALL-CAPS, "
        "no clickbait clichés, no excessive emoji.",
    )
    youtube_description: str = Field(
        ...,
        description="YouTube Shorts description. First sentence (≤140 chars) is the hook "
        "for the algorithm preview. Then 1-2 short paragraphs expanding without spoiling "
        "the twist. Last line: 3-5 niche-specific hashtags.",
    )
    youtube_tags: list[str] = Field(
        ...,
        description="YouTube tags (metadata layer, separate from description hashtags). "
        "8-12 items, NO '#' prefix, lowercase preferred. Mix broad niche keywords "
        "(e.g. 'filipino mythology', 'folklore'), creature/topic-specific terms (e.g. "
        "'tikbalang', 'aswang'), and format keywords ('shorts', 'pov', 'horror short'). "
        "Total joined-by-commas length must stay under 500 chars (YouTube's hard cap).",
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

    def num_scenes(self) -> int:
        return len(self.scene_actions)

    def prompt_for_scene(self, index: int) -> str:
        # Lead with an explicit video directive — meta.ai's unified Create
        # tool routes by intent ("Describe an image or video..."), and
        # without this leading phrase it defaults to image generation.
        parts = ["Create a 5-second cinematic video", self.style_anchor, self.setting_anchor]
        if self.character_anchors:
            parts.append(self.character_anchors)
        parts.append(f"SHOT: {self.scene_shots[index]}")
        parts.append(f"ACTION: {self.scene_actions[index]}")
        return ". ".join(p.rstrip(". ") for p in parts) + "."

    def build_caption_plan(self) -> CaptionPlan:
        return CaptionPlan(persistent=self.pov_caption)
