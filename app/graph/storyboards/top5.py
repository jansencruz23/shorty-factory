"""Top-5 ranking-mode storyboard.

Five self-contained ranked moments around a single theme, NOT a story arc.
Each item stands alone, building to #1 as the strongest hit. The on-screen
layout uses a *progressive countdown reveal*:

- A multi-color title sits at the top, persistent across all clips, with one
  accent phrase popped in red.
- A short subtitle below the title teases the payoff ("you wont believe the
  last one", "wait for #1").
- A 5-row rank list is stacked top-to-bottom. All five row numbers appear
  from clip 0; each successive clip reveals one more caption next to its
  number, so by the final clip every caption is visible.

The captions themselves are MINIMAL brainrot/Gen Z phrases — 1-4 words, not
sentences. The hook is the format and the reveal cadence, not the prose.

The style_anchor locks visual identity across all 5 clips. Each item gets
its own micro-setting that varies — that's what visually separates the
moments."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.graph.storyboards.base import (
    CaptionPlan,
    ProgressiveOverlay,
    RankRow,
    TitleSegmentRender,
)


class TopFiveItem(BaseModel):
    rank: int = Field(
        ...,
        description="Rank position, 5 → 1. Items must be ordered descending: items[0].rank=5, "
        "items[1].rank=4, ..., items[4].rank=1.",
    )
    caption: str = Field(
        ...,
        description="MINIMAL humorous caption — 1-4 words, brainrot/Gen Z tone. NOT a "
        "complete sentence. NO rank prefix (the renderer prepends '5.', '4.', etc.). "
        "Examples: 'cute blue eyes', 'i love mom', 'unbelievable', 'thank you', 'Hiiii!', "
        "'no cap', 'lowkey iconic', 'sheeesh', 'down bad', 'menace'. "
        "REJECT: complete sentences, formal descriptions, anything over 5 words.",
    )
    setting: str = Field(
        ...,
        description="This item's micro-setting — location/atmosphere unique to this clip. "
        "Different from sibling items'; that's what visually separates the moments.",
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
        description="The full title as a plain string — used for YouTube title fallbacks "
        "and logging. Format: 'Top 5 [adjective] [theme] [Moments|Reactions|Things|...]', "
        "or 'Ranking [adjective] [theme] [Moments|Reactions|...]'. Examples: 'Top 5 Most "
        "Satisfying Tikbalang Moments', 'Ranking Cutest Baby Responses Ever', 'Top 5 "
        "Wildest Skateboard Wins'.",
    )
    title_lines: list[str] = Field(
        ...,
        description="The title pre-broken into 1-2 RENDERED lines (the renderer does NOT "
        "auto-wrap — what you put here is what shows). Concatenating the lines with a "
        "space should reproduce main_title. Example for 'Ranking Cutest Baby Responses "
        "Ever': ['Ranking Cutest', 'Baby Responses Ever']. Pick a break point that puts "
        "the accent_phrase mostly on one line.",
    )
    accent_phrase: str = Field(
        ...,
        description="A 1-3 word phrase from main_title to highlight in accent color "
        "(red). MUST appear EXACTLY (case-sensitive) inside one of the title_lines. "
        "Pick the noun phrase that pops visually — usually the subject of the countdown. "
        "Examples: in 'Ranking Cutest Baby Responses Ever' → 'Baby Responses'; in 'Top 5 "
        "Wildest Skateboard Wins' → 'Skateboard Wins'; in 'Top 5 Most Unsettling "
        "Tikbalang Encounters' → 'Tikbalang Encounters'.",
    )
    subtitle: str = Field(
        ...,
        description="Short tease line under the title — sells the payoff without "
        "spoiling. Lowercase preferred for casual brainrot energy. Max 8 words. "
        "Examples: 'you wont believe the last one', 'the last one is lovely', "
        "'wait for #1', 'no.1 is unhinged', 'pure cinema'. May include 1 emoji.",
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
    music_prompt: str = Field(
        ...,
        description="A MusicGen-ready prompt tailored to THIS countdown's specific content "
        "and tone. 12-20 words. Format: [genre/feel] + [key instruments] + [tempo] + "
        "[mood/atmosphere]. Instrumental only — NEVER mention vocals or lyrics. Match the "
        "energy of the format AND the subject. Examples:\n"
        "  Cute baby reactions: 'warm playful ukulele with light glockenspiel and bouncy "
        "percussion, cheerful cozy uptempo, wholesome'\n"
        "  Skateboard wins: 'driving electric guitar riff with cinematic synth swell and "
        "punchy drums, triumphant fast tempo, hype energy'\n"
        "  Tikbalang horror countdown: 'dark cinematic kulintang gongs with sparse bamboo "
        "flute and metallic stings, ominous mid-tempo, dread atmosphere'\n"
        "  Skateboard fails: 'comedic brass stings with awkward acoustic guitar plinks, "
        "off-beat tempo, lighthearted slapstick'\n"
        "  Satisfying loops: 'smooth ambient synth pulse with subtle pad swells, steady "
        "tempo, hypnotic minimal'",
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

    def _split_line_by_accent(self, line: str) -> list[TitleSegmentRender]:
        """Split one title line into colored segments, coloring the accent
        phrase (if present) red and the surrounding text white. If the
        phrase isn't found in this line, the whole line stays white — the
        accent phrase is on a different line."""
        idx = line.find(self.accent_phrase) if self.accent_phrase else -1
        if idx == -1:
            return [TitleSegmentRender(text=line, color="white")]
        segments: list[TitleSegmentRender] = []
        before = line[:idx]
        after = line[idx + len(self.accent_phrase):]
        if before:
            segments.append(TitleSegmentRender(text=before, color="white"))
        segments.append(TitleSegmentRender(text=self.accent_phrase, color="accent"))
        if after:
            segments.append(TitleSegmentRender(text=after, color="white"))
        return segments

    def build_caption_plan(self) -> CaptionPlan:
        # Title gets the same colored-segment treatment on every clip — it
        # doesn't change as the countdown progresses.
        title_lines_render = [self._split_line_by_accent(line) for line in self.title_lines]

        # Display order is INVERTED from items order: rank 1 sits at the TOP
        # of the rank list (items[-1]), rank 5 sits at the BOTTOM (items[0]).
        # The countdown reveal still happens in items order — items[0] (rank
        # 5, bottom row) populates on clip 0; items[-1] (rank 1, top row)
        # populates on the final clip. So captions visually fill the list
        # FROM THE BOTTOM UP, with the #1 reveal landing last at the top.
        #
        # For display position p (0 = top), the source item is items[n-1-p].
        # That item is revealed when clip_idx ≥ (n-1-p).
        overlays: list[ProgressiveOverlay] = []
        n = len(self.items)
        for clip_idx in range(n):
            rank_rows: list[RankRow] = []
            for p in range(n):
                item_idx = n - 1 - p
                item = self.items[item_idx]
                revealed = item_idx <= clip_idx
                rank_rows.append(
                    RankRow(
                        number=f"{item.rank}.",
                        caption=item.caption if revealed else None,
                    )
                )
            overlays.append(
                ProgressiveOverlay(
                    title_lines=title_lines_render,
                    subtitle=self.subtitle,
                    rank_rows=rank_rows,
                )
            )
        return CaptionPlan(progressive=overlays)
