"""Top-5 ranking-mode composer.

Produces a TopFiveStoryboard for the progressive countdown layout: a
multi-color title with an accent phrase popped in red, a brainrot subtitle
that teases the payoff, and 5 ranked moments with MINIMAL 1-4 word captions
that reveal cumulatively as the countdown plays.

Domain-agnostic: the SYSTEM prompt teaches the format with examples spanning
horror, wins, and cute so the LLM doesn't anchor to one tone.

Validation:
- exactly 5 items required (one retry on length mismatch, then fail)
- ranks must be {5,4,3,2,1}; sorted descending if the LLM returns out of order
- accent_phrase must appear inside one of the title_lines"""

from __future__ import annotations

import logging

from langchain_core.messages import HumanMessage, SystemMessage

from app.graph.composer import get_chat_llm
from app.graph.storyboards import TopFiveStoryboard

logger = logging.getLogger(__name__)


SYSTEM = """You write countdown Top-5 storyboards for short-form vertical AI videos
(YouTube Shorts / Reels / TikTok).

Hard constraints:
- The video is built from 5 independent ~5-second AI-generated clips that have NO memory
  between them. Visual cohesion comes ONLY from style_anchor being repeated VERBATIM in
  every clip's prompt. The PER-ITEM setting varies — that's what makes each ranked moment
  feel distinct.
- This is NOT a story arc. The 5 items are SELF-CONTAINED ranked moments around a single
  theme. No twist, no narrative through-line. Each item must read as a complete beat in
  isolation.
- Build to #1 as the strongest hit. That's the format viewers expect from countdowns.

style_anchor (locked — reused verbatim every clip):
- 1 sentence. Color palette, lighting, lens aesthetic, mood. NO camera angles or movements.
- Pick a tone consistent with the niche. Cute content → "warm bright daylight, soft pastel
  palette, shallow depth-of-field, cheerful uplifting mood, 50mm cinematic." Horror →
  "desaturated grayscale with blood-red accents, low-key lighting, ominous mood, 35mm grain."
  Wins → "high-contrast vibrant color, golden-hour light, dynamic lens flares, triumphant
  energy, sports-doc aesthetic."

══════════════════════════════════════════════════════════════════════════════
ON-SCREEN LAYOUT (this is what the renderer draws — write the fields below
to match this layout exactly):

   ┌─────────────────────────────────────────┐
   │       Ranking Cutest                    │  ← title_lines[0], white
   │       Baby Responses Ever               │  ← title_lines[1], "Baby Responses"
   │       you wont believe the last one     │  ← subtitle, smaller, white
   │                                         │
   │   5. cute blue eyes                     │  ← rank list: revealed cumulatively,
   │   4. i love mom                         │     top-to-bottom. items[0] (rank 5)
   │   3. unbelievable                       │     reveals on clip 0; items[4] (rank 1)
   │   2. thank you                          │     reveals on clip 4 (last).
   │   1. Hiiii!                             │     Numbers always visible from clip 0.
   │                                         │
   │   [video content fills the rest]        │
   └─────────────────────────────────────────┘

══════════════════════════════════════════════════════════════════════════════

main_title (plain string, used for YouTube fallbacks):
- Format: "Top 5 [adjective] [theme] [Moments|Reactions|Things|Encounters|Wins|Fails]"
  OR "Ranking [adjective] [theme] [Moments|Reactions|...]"
- Lead with "Top 5 " or "Ranking ". Title Case. No emoji. ≤ 60 characters.
- The adjective amplifies what the viewer sees (Most Satisfying, Cutest, Wildest,
  Funniest, Scariest, Most Insane, Sweetest, Strangest).
- Examples: "Top 5 Most Satisfying Tikbalang Moments", "Ranking Cutest Baby Responses
  Ever", "Top 5 Wildest Skateboard Wins".

title_lines (list of 1-2 strings — the LLM picks the line break):
- The renderer does NOT auto-wrap. What you put here is what shows on screen.
- For a short title (≤4 words), use 1 line. For longer titles, break into 2 lines so
  the line widths are roughly balanced.
- Concatenating the lines with a space should reproduce main_title.
- Pick a break point that lets the accent_phrase sit mostly on one line (split phrases
  across lines look ugly).
- Examples:
    "Top 5 Most Unsettling Tikbalang Encounters" →
        ["Top 5 Most Unsettling", "Tikbalang Encounters"]
    "Ranking Cutest Baby Responses Ever" →
        ["Ranking Cutest", "Baby Responses Ever"]
    "Top 5 Wildest Skateboard Wins" →
        ["Top 5 Wildest", "Skateboard Wins"]

accent_phrase (string — 1-3 words from main_title, rendered in red):
- MUST appear EXACTLY (case-sensitive, including spaces) inside one of the title_lines.
  If it doesn't, the renderer falls back to all-white and you lose the visual pop.
- Pick the noun phrase that POPS — usually the subject of the countdown.
- Examples:
    main_title="Ranking Cutest Baby Responses Ever"        → accent_phrase="Baby Responses"
    main_title="Top 5 Most Unsettling Tikbalang Encounters" → accent_phrase="Tikbalang Encounters"
    main_title="Top 5 Wildest Skateboard Wins"             → accent_phrase="Skateboard Wins"
    main_title="Top 5 Cutest Cat Reactions"                 → accent_phrase="Cat Reactions"

subtitle (short tease line — ≤8 words, lowercase preferred):
- Sells the payoff WITHOUT spoiling the actual #1.
- Brainrot/casual energy. Lowercase. May include 1 emoji.
- Examples: "you wont believe the last one", "the last one is lovely", "wait for #1",
  "no.1 is unhinged 🚨", "pure cinema fr", "stick around for #1", "the last one cooked".
- REJECT formal phrasing ("In this countdown, you will see..."), spoilers ("the kitten
  wins"), or anything ≥ 9 words.

items: exactly 5 entries, ordered rank 5 → 4 → 3 → 2 → 1. items[0].rank=5, items[4].rank=1.

Per item:
  rank — integer, decreasing 5,4,3,2,1.
  caption — MINIMAL brainrot phrase. **1-4 WORDS MAX.** Not a sentence, not a description.
            Pick a vibe-tag, an exclamation, or a hyper-condensed observation. The
            renderer prepends "{rank}. " automatically — DO NOT include it.
            GOOD captions: "cute blue eyes", "i love mom", "unbelievable", "thank you",
                           "Hiiii!", "no cap", "lowkey iconic", "sheesh", "pure menace",
                           "down bad", "iconic behavior", "cinema", "down catastrophic".
            BAD captions:  "The baby smiles at the camera in a charming way" — too long,
                           "A funny moment captured perfectly" — generic, no vibe,
                           "#5: cute eyes" — rank prefix included (renderer adds it).
            Tone-match the niche:
              CUTE      → "cute blue eyes", "i love mom", "thank you", "Hiiii!", "smol".
              HORROR    → "the smile", "wrong", "not him", "menace", "no thanks".
              WINS      → "sheesh", "no cap", "GOATed", "cinema", "casual W".
              FAILS     → "skill issue", "down bad", "L behavior", "embarrassing",
                          "cooked".
              SATISFYING → "perfect fit", "smooth", "elite", "chef's kiss", "asmr".
  setting — micro-setting unique to this clip: location + atmosphere. Different from
            siblings'. This is what visually separates the moments.
            Examples: "Sunlit studio apartment with hardwood floors" / "Misty rainforest
            clearing at dusk" / "Sunset skatepark, concrete bowl, golden light."
  scene_action — the peak kinetic beat for this 5s clip. ~12-22 words. ONE strong verb
            landing by second 3. NOT a static pose, NOT a reaction-only shot. Same density
            rules as narrative scene_actions.
  scene_shot — camera framing: shot type + angle + movement. VARY across the 5 items so
            cuts feel like a real edit. Mix wide / close-up / dutch / first-person POV /
            bird's-eye / handheld.

Strong action verbs to favor: snap, crack, leap, surge, plunge, lunge, dive, whip, crash,
lash, burst, slam, pounce, flick, vault, twist, erupt, shatter, recoil, hurtle, snap, claw.
Weak verbs to AVOID: walks, stands, looks, watches, sees, appears, waits, turns, gazes.

Multi-domain example pairs (study these — the format is the same regardless of tone):

────── CUTE (babies) ──────
main_title:    "Ranking Cutest Baby Responses Ever"
title_lines:   ["Ranking Cutest", "Baby Responses Ever"]
accent_phrase: "Baby Responses"
subtitle:      "you wont believe the last one"
style_anchor:  "Warm bright daylight, soft pastel palette, shallow depth-of-field,
               cheerful uplifting mood, 50mm cinematic."
items:
  rank=5 setting:"Cozy nursery with soft morning light"
         caption:"cute blue eyes"
         scene_action:"Baby's eyes widen as a parent's hand offers a rattle, sparkling
                       blue irises catching the sunlight in slow zoom."
         scene_shot:"Extreme close-up push-in on the eyes."
  rank=4 setting:"Sunlit kitchen with high chair"
         caption:"i love mom"
         scene_action:"Baby reaches both arms toward an off-camera figure and giggles,
                       milk-dotted lips parting in joy."
         scene_shot:"Medium handheld over-the-shoulder."
  rank=3 setting:"Living-room rug, midday"
         caption:"unbelievable"
         scene_action:"Baby's mouth drops fully open in cartoon-shock as a soap bubble
                       lands on a tiny finger."
         scene_shot:"Low-angle close-up, slow dolly-in."
  rank=2 setting:"Bath time, fluffy towel"
         caption:"thank you"
         scene_action:"Baby clasps hands together briefly mid-towel-wrap, prayer-like,
                       beam erupting across the face."
         scene_shot:"Eye-level close-up static."
  rank=1 setting:"Front-facing phone, golden-hour bedroom"
         caption:"Hiiii!"
         scene_action:"Baby leans into the lens until their cheek nearly touches it,
                       gigantic smile filling the frame, hand reaches up to pat camera."
         scene_shot:"First-person POV close-up, gentle handheld bob."

────── HORROR (Filipino mythology) ──────
main_title:    "Top 5 Most Unsettling Tikbalang Encounters"
title_lines:   ["Top 5 Most Unsettling", "Tikbalang Encounters"]
accent_phrase: "Tikbalang Encounters"
subtitle:      "no.1 is unhinged 🚨"
style_anchor:  "Desaturated grayscale with blood-red accents, low-key cinematic lighting,
               35mm grain, dread-mood."
items (rank=5..1, each with 1-4 word brainrot horror caption):
  rank=5 caption:"hooves"           ...
  rank=4 caption:"wrong footprints" ...
  rank=3 caption:"the mane"         ...
  rank=2 caption:"that smile"       ...
  rank=1 caption:"loop"             ...

────── WINS (skating) ──────
main_title:    "Top 5 Wildest Skateboard Wins"
title_lines:   ["Top 5 Wildest", "Skateboard Wins"]
accent_phrase: "Skateboard Wins"
subtitle:      "stick around for #1 fr"
style_anchor:  "High-contrast vibrant color, golden-hour light, dynamic lens flares,
               triumphant energy, sports-doc aesthetic."
items:
  rank=5 caption:"clean"             ...
  rank=4 caption:"sheesh"            ...
  rank=3 caption:"no cap"            ...
  rank=2 caption:"GOATed"            ...
  rank=1 caption:"cinema"            ...

══════════════════════════════════════════════════════════════════════════════

YOUTUBE METADATA:

youtube_title (max 60 chars):
- Often a tighter rephrase of main_title. Lead with the niche keyword.
- No ALL-CAPS, no clickbait clichés ("AMAZING", "INSANE", "YOU WON'T BELIEVE"),
  at most one emoji.
- Examples: "Top 5 Most Unsettling Tikbalang Encounters", "Ranking Cutest Baby
  Responses Ever", "Top 5 Wildest Skateboard Wins of the Year".

youtube_description (3-5 short lines):
Line 1: hook restated for the algorithm preview, ≤140 chars. Mention the count and theme.
Lines 2-3: 1-2 short lines framing what's in the video. Reference the niche by name so the
algorithm tags it correctly.
Line 4 (final): 3-5 niche-specific hashtags on one line, space-separated.

Hashtag derivation rules:
- Convert the niche to PascalCase as the primary hashtag (niche="filipino-mythology" →
  #FilipinoMythology, niche="cute" → #Cute, niche="skateboarding" → #Skateboarding).
- Add 2-3 closely related tags (parent categories, audience subcultures).
- Add a topic-specific tag if there's a named subject in the video (#Tikbalang, #Cats).
- ALWAYS include #Top5 and #Shorts.
- Output 4-6 total. NO hardcoded niche names — adapt to the niche supplied.

youtube_tags (metadata layer, NOT description hashtags):
- 8-12 tags. No '#' prefix. Lowercase. Comma-separated when joined; total under 500 chars.
- Mix three layers:
  1. Broad niche tags (2-3): "filipino mythology", "folklore" / "cats", "pets" / "skateboarding"
  2. Topic-specific tags (3-4): "tikbalang", "horse spirit" / "kitten reactions" / "kickflip"
  3. Format tags (3-4): "top 5", "countdown", "shorts", "ranked"

Avoid in scene_actions/scene_shots: complex multi-character dialogue, lip-sync, fast cuts
within a scene, fine hand-detail work like writing/sewing (AI video renders these badly),
text/signs in the world, STATIC POSES, PURE TRAVERSAL, REACTION-ONLY SHOTS, ATMOSPHERE-ONLY
SHOTS.
"""

USER_TEMPLATE = """Idea: {idea}
Niche: {niche}
Format: Top-5 countdown (exactly 5 items, ranked 5 → 1)"""


async def compose_top5(*, idea: str, niche: str | None) -> TopFiveStoryboard:
    structured_llm = get_chat_llm().with_structured_output(TopFiveStoryboard)
    user = USER_TEMPLATE.format(idea=idea, niche=niche or "unspecified")

    storyboard: TopFiveStoryboard = await structured_llm.ainvoke(
        [SystemMessage(content=SYSTEM), HumanMessage(content=user)]
    )

    # One retry if the LLM returns the wrong item count. Llama / GPT-class
    # models occasionally emit 4 or 6 items even when asked for "exactly 5",
    # especially with structured output. Retrying once with a sharper user
    # message usually fixes it; failing past that signals a real prompt issue.
    if len(storyboard.items) != 5:
        logger.warning(
            "top5 returned %d items, expected 5 — retrying once",
            len(storyboard.items),
        )
        retry_user = (
            user
            + "\n\nIMPORTANT: the previous attempt returned "
            f"{len(storyboard.items)} items. Output EXACTLY 5 items, ranked 5 → 1."
        )
        storyboard = await structured_llm.ainvoke(
            [SystemMessage(content=SYSTEM), HumanMessage(content=retry_user)]
        )
        if len(storyboard.items) != 5:
            raise ValueError(
                f"top5 composer returned {len(storyboard.items)} items after retry, "
                f"expected exactly 5"
            )

    # Sort items by rank descending (5 → 1) so downstream code can rely on
    # items[0] being rank 5. The LLM is instructed to do this but doesn't
    # always comply — and the renderer prepends the rank prefix from item.rank
    # so a mis-ordered list would still produce wrong reveal ordering.
    storyboard.items.sort(key=lambda it: it.rank, reverse=True)

    expected_ranks = [5, 4, 3, 2, 1]
    actual_ranks = [it.rank for it in storyboard.items]
    if actual_ranks != expected_ranks:
        raise ValueError(
            f"top5 composer returned ranks {actual_ranks}, expected {expected_ranks}"
        )

    # accent_phrase must appear in one of the title_lines (case-sensitive),
    # otherwise the renderer can't apply the color highlight and falls back
    # to all-white. Log a warning rather than failing — all-white still
    # produces a valid video, just without the visual pop.
    if storyboard.accent_phrase and not any(
        storyboard.accent_phrase in line for line in storyboard.title_lines
    ):
        logger.warning(
            "top5 accent_phrase %r not found in title_lines %r — title will "
            "render all-white. Tighten the prompt if this recurs.",
            storyboard.accent_phrase,
            storyboard.title_lines,
        )

    return storyboard
