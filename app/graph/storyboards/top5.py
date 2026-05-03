"""Top-5 ranking-mode storyboard.

Five self-contained ranked moments around a single theme. NOT a story arc —
each item stands alone, building to #1 as the strongest hit. The persistent
main_title sits at the top of every clip; the rank caption switches per clip.

The style_anchor locks the visual identity across all 5 clips so the
countdown reads as a unified piece. Each item gets its own micro-setting
that varies — that's what visually separates the moments."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.graph.storyboards.base import CaptionPlan


class TopFiveItem(BaseModel):
    rank: int = Field(
        ...,
        description="Rank position, 5 → 1. Items must be ordered descending: items[0].rank=5, "
        "items[1].rank=4, ..., items[4].rank=1.",
    )
    caption: str = Field(
        ...,
        description="Verb-phrase describing what happens in this clip. The renderer prepends "
        "'#N: ' so DO NOT include the rank prefix here. Keep ≤8 words. "
        "Example: 'Domino chain collapses in slow motion' → renders as "
        "'#5: Domino chain collapses in slow motion'.",
    )
    setting: str = Field(
        ...,
        description="This item's micro-setting — location/atmosphere unique to this clip. "
        "Different from the next item's setting; that's what visually separates the moments.",
    )
    scene_action: str = Field(
        ...,
        description="The peak kinetic beat for this 5s clip — one strong verb landing by "
        "second 3. Same density rules as narrative scene_actions.",
    )
    scene_shot: str = Field(
        ...,
        description="Camera framing: shot type + angle + movement. Vary across items so "
        "cuts feel like a real edit.",
    )


class TopFiveStoryboard(BaseModel):
    style_anchor: str = Field(
        ...,
        description="Color palette, lighting, lens aesthetic, mood. Reused across ALL 5 "
        "clips to give the countdown a unified visual identity. NO camera angles here — "
        "those vary per scene_shot.",
    )
    main_title: str = Field(
        ...,
        description="The persistent title displayed at the top of every clip. Format: "
        "'Top 5 [adjective] [theme] [Moments|Encounters|Things|Fails|Wins]'. Examples: "
        "'Top 5 Most Satisfying Tikbalang Moments', 'Top 5 Cutest Cat Reactions', "
        "'Top 5 Wildest Skateboard Wins'.",
    )
    youtube_title: str = Field(
        ...,
        description="YouTube Shorts title, max 60 chars. Often a tighter rephrase of "
        "main_title with the strongest niche keyword forward.",
    )
    youtube_description: str = Field(
        ...,
        description="YouTube Shorts description. Hook (≤140 chars), 1-2 short paragraphs "
        "framing the countdown, last line: 3-5 niche-specific hashtags.",
    )
    youtube_tags: list[str] = Field(
        ...,
        description="YouTube tags. 8-12 items, no '#' prefix, lowercase. Mix broad niche, "
        "topic-specific, and format keywords (e.g. 'shorts', 'top 5', 'countdown'). "
        "Joined-by-commas length under 500 chars.",
    )
    items: list[TopFiveItem] = Field(
        ...,
        description="Exactly 5 items, ordered rank 5 → 4 → 3 → 2 → 1. #1 is the strongest "
        "hit — that's what viewers expect from countdown format.",
    )

    def num_scenes(self) -> int:
        return len(self.items)

    def prompt_for_scene(self, index: int) -> str:
        # Same leading directive as narrative — meta.ai's unified Create tool
        # routes by intent, and this prefix forces video-mode generation.
        item = self.items[index]
        parts = [
            "Create a 5-second cinematic video",
            self.style_anchor,
            item.setting,
            f"SHOT: {item.scene_shot}",
            f"ACTION: {item.scene_action}",
        ]
        return ". ".join(p.rstrip(". ") for p in parts) + "."

    def build_caption_plan(self) -> CaptionPlan:
        return CaptionPlan(
            title=self.main_title,
            per_clip=[f"#{item.rank}: {item.caption}" for item in self.items],
        )
