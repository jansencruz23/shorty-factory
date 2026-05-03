"""Top-5 ranking-mode composer.

Produces a TopFiveStoryboard: 5 self-contained ranked moments around one
theme, NOT a story arc. The persistent main_title sits at the top of every
clip; the rank caption (#5..#1) switches per clip.

Domain-agnostic: the SYSTEM prompt teaches the format with examples spanning
horror, wins, and cute so the LLM doesn't anchor to one tone. Whatever niche
+ idea the caller passes in, the composer adapts.

Validation:
- exactly 5 items required (one retry on length mismatch, then fail)
- ranks must be {5,4,3,2,1}; sorted descending if the LLM returns out of order"""

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

main_title (the persistent on-screen title — burned across every clip):
- Format: "Top 5 [adjective] [theme] [Moments|Encounters|Things|Fails|Wins|Reactions]"
- Lead with "Top 5 ". Use Title Case. No emoji. ≤ 60 characters.
- The adjective amplifies what the viewer is about to see (Most Satisfying, Cutest, Wildest,
  Funniest, Scariest, Most Insane, Sweetest, Strangest).
- Examples:
    "Top 5 Most Satisfying Tikbalang Moments"
    "Top 5 Cutest Cat Reactions"
    "Top 5 Wildest Skateboard Wins"
    "Top 5 Funniest Lola Hugot Moments"
    "Top 5 Scariest Aswang Sightings"

items: exactly 5 entries, ordered rank 5 → 4 → 3 → 2 → 1 (items[0].rank=5, items[4].rank=1).

Per item:
  rank — integer, decreasing 5,4,3,2,1.
  caption — verb-phrase describing what happens in this clip. ≤ 8 words. NO rank prefix
            (the renderer adds "#5: ", "#4: ", ... automatically).
            Examples: "Domino chain collapses in slow motion" / "Kitten swats at a feather"
                      / "Skater lands an impossible kickflip".
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

────── HORROR (Filipino mythology) ──────
main_title: "Top 5 Most Unsettling Tikbalang Encounters"
style_anchor: "Desaturated grayscale with blood-red accents, low-key cinematic lighting,
              35mm grain, dread-mood."
items:
  rank=5 setting:"Misty rainforest clearing at dusk"
         caption:"Hooves crash through the underbrush"
         scene_action:"Hooves smash through wet leaves and ferns, kicking up debris in a
                       low fog as something charges past."
         scene_shot:"Low-angle ground-level static lock-off, ferns brush the lens."
  rank=4 setting:"Old colonial road wrapped in fog"
         caption:"Footprints flip from boots to hooves"
         scene_action:"Camera tracks across damp earth as fresh boot-prints transform
                       mid-trail into hoof-prints, then back."
         scene_shot:"Top-down dolly-in pushing along the trail."
  rank=3 setting:"Moss-covered stone shrine at moonrise"
         caption:"Mane whips into view"
         scene_action:"A black mane lashes across frame as something tall rears up,
                       silhouette flickering against the moon."
         scene_shot:"Whip-pan from shrine to wide silhouette."
  rank=2 setting:"Mountain trail switchback, predawn"
         caption:"Smile widens beneath the hat"
         scene_action:"Wide-brim hat tilts up — too-wide grin glints, then teeth become
                       horse teeth in close-up reveal."
         scene_shot:"Push-in extreme close-up under the hat brim."
  rank=1 setting:"Endless rainforest path looping back"
         caption:"You arrive where you started"
         scene_action:"Hiker breaks through underbrush — same fallen log, same red ribbon —
                       hooves stamp the dirt behind them."
         scene_shot:"Reverse dolly pull-back revealing hoofprints behind the figure."

────── WINS (skating) ──────
main_title: "Top 5 Wildest Skateboard Wins"
style_anchor: "High-contrast vibrant color, golden-hour light, dynamic lens flares,
              triumphant energy, sports-doc aesthetic."
items:
  rank=5 setting:"Sunset skatepark concrete bowl"  caption:"Lands a switch heelflip clean"
         scene_action:"Skater pops a switch heelflip mid-bowl, board snaps under feet,
                       wheels slap concrete as they roll out grinning."
         scene_shot:"Tracking medium handheld follows the skater out of the bowl."
  rank=4 setting:"City stair set at twilight"     caption:"Kickflips a 12-stair gap"
         ... (full action + shot)
  ... (rank 3, 2, 1 each unique)

────── CUTE (cats) ──────
main_title: "Top 5 Cutest Cat Reactions"
style_anchor: "Warm bright daylight, soft pastel palette, shallow depth-of-field,
              cheerful uplifting mood, 50mm cinematic."
items:
  rank=5 setting:"Sunlit studio apartment hardwood floor"
         caption:"Pounces on a feather toy"
         scene_action:"Kitten leaps sideways, paws batting at a drifting feather, tumbles
                       in golden sunlight in a mock-attack roll."
         scene_shot:"Low-angle handheld tracking shot, follow-pan."
  ... (rank 4-1 each unique cute beat)

GOOD captions (verb-phrase, no rank prefix, ≤8 words):
  "Hooves crash through the underbrush"
  "Footprints flip from boots to hooves"
  "Kitten swats at a feather"
  "Skater lands an impossible kickflip"

BAD captions (do NOT do these):
  "#5: Hooves crash..."           — rank prefix is added by the renderer; do not include.
  "Number five is when the..."    — narrative voice, breaks the format.
  "Cat is cute"                    — no verb peak, atmosphere-only.
  "The Tikbalang appears"         — static reveal, not a kinetic beat.

WEAK scene_actions to AVOID:
  "Hunter walks deeper into the rainforest." — pure traversal, no peak.
  "Cat sits and looks around."               — reaction-only, no kinetic beat.
  "Skater stands at the top of the ramp."   — setup-only, no impact.

YOUTUBE METADATA:

youtube_title (max 60 chars):
- Often a tighter rephrase of main_title. Lead with the niche keyword.
- No ALL-CAPS, no clickbait clichés ("AMAZING", "INSANE", "YOU WON'T BELIEVE"),
  at most one emoji.
- Examples:
    "Top 5 Most Unsettling Tikbalang Encounters"
    "Top 5 Cutest Cat Reactions You'll Watch Twice"
    "Top 5 Wildest Skateboard Wins of the Year"

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

Example description (Tikbalang Top 5):
  "Five of the most unsettling Tikbalang moments captured on AI video — ranked 5 to 1.

  A countdown of the eeriest sightings the Filipino mountain trickster has been blamed
  for over the years.

  #FilipinoMythology #Tikbalang #Folklore #Top5 #Shorts"

Example description (Cute cats Top 5):
  "Five of the cutest cat reactions ranked 5 to 1 — the last one will make you smile.

  A wholesome countdown of feline pounces, swats, and surprise reactions.

  #Cute #Cats #CatsOfTikTok #Top5 #Shorts"

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
    # always comply, and the rendering code prepends "#{rank}: " from the
    # rank field anyway, so a mis-ordered list would still produce wrong
    # caption-clip alignment.
    storyboard.items.sort(key=lambda it: it.rank, reverse=True)

    expected_ranks = [5, 4, 3, 2, 1]
    actual_ranks = [it.rank for it in storyboard.items]
    if actual_ranks != expected_ranks:
        raise ValueError(
            f"top5 composer returned ranks {actual_ranks}, expected {expected_ranks}"
        )

    return storyboard
