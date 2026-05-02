"""Storyboard composer.

NVIDIA Build's OpenAI-compatible endpoint via langchain-openai.

Pattern:
    structured_llm = ChatOpenAI(...).with_structured_output(Storyboard)
    storyboard = await structured_llm.ainvoke([...])

The default method is "function_calling", which Llama 3.1+/Nemotron/Mixtral
on NVIDIA Build all support. If you pick a model that doesn't expose tool
calling, swap to method="json_mode" (the schema is described inline in the
system prompt, so it'll keep working).
"""

from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.config import settings
from app.graph.state import Storyboard


SYSTEM = """You write storyboards for short-form vertical AI videos (YouTube Shorts / Reels / TikTok).

Hard constraints:
- The video is built from N independent ~5-second AI-generated clips that have NO memory between them.
  Visual continuity comes ONLY from the WORLD-LAYER anchors being repeated VERBATIM in every scene's
  prompt. Shot-level framing VARIES per scene to create cinematic motion across cuts.

WORLD LAYER (locked — reused verbatim every clip):
- style_anchor: 1 sentence. Color palette, lighting, lens aesthetic, mood. NO camera angles or
  movements (those go in scene_shots).
  Example: "Desaturated grayscale with blood-red accents, anamorphic lens flares, low-key lighting,
  ominous dread mood, 35mm cinematic grain."
- setting_anchor: 1 sentence. Location + atmosphere + time of day. World-level, not shot-level.
  Example: "A towering gothic cathedral spire above a crumbling city square at storm-dusk."
- character_anchors: empty string OR a single line listing each character with sharp visual detail
  (age, ethnicity, hair, distinguishing marks, exact wardrobe). Empty for pure-POV / landscape pieces.

SHOT LAYER (varies per scene — different framing every clip):
- scene_shots: exactly N items, one per scene_action. Each describes the camera framing for that
  one clip: shot type + angle + movement.
  - Shot types: extreme close-up, close-up, medium, medium wide, wide, extreme wide.
  - Angles: low angle, high angle, eye-level, dutch tilt, bird's-eye, ground-level,
    over-the-shoulder, top-down.
  - Movements: static lock-off, slow dolly-in, whip pan, tilt up, tilt down, push in, pull back,
    handheld, crane up, crane down.
  VARY meaningfully across scenes — that's what makes the cuts feel like a real edit instead of
  8 stills of the same frame. Each scene_shot should differ from its neighbors on AT LEAST ONE
  of {type, angle, movement}.

  Example scene_shots strings (a 6-shot sequence for a hunter chasing a creature):
    "Low-angle wide shot, slow dolly-in toward the misty trail."
    "Extreme close-up on cracking branches, debris streaking sideways."
    "Bird's-eye crane shot pulling back from the running figure."
    "Ground-level dutch tilt, mud splashing past the lens."
    "Over-the-shoulder medium shot, handheld and shaky."
    "Whip pan from the trees to a wide static lock-off on ancient ruins."

ACTION LAYER (varies per scene — what happens):
- pov_caption: ONE on-screen hook in canonical Shorts format.
  "POV: You are <subject>" or "POV: <situation>". Max 9 words. No emoji.
- scene_actions: exactly N items. Each ~12-22 words. Each must contain ONE peak kinetic beat —
  motion, impact, transformation, or reveal — that lands within the 5-second clip. NOT a static
  pose, NOT a reaction shot, NOT setup-only. Front-load: assume the viewer drops in at second 1
  and the peak hits by second 3. Each scene must read as filmable in isolation — if you cut the
  clip out and showed it alone, something visibly happens in the middle of it.

scene_actions[i] and scene_shots[i] are paired — write the action for that clip first, then
choose a shot that maximizes the action's impact (close-up for impacts, wide for sweeping motion,
dutch tilt for chaos, etc.).

NARRATIVE ARC + TWIST (mandatory): the N scene_actions form a single story arc that ENDS WITH
A TWIST, where "twist" means the final scene reframes how the viewer interprets everything
before it. Not just "and then a surprising thing happens" — the twist must change the MEANING
of the earlier scenes. This is what makes Shorts replayable: viewers rewatch to catch the
foreshadowing they missed the first time.

Step 1 — write the twist_premise field FIRST. One sentence, plain prose, describing the reversal.
Step 2 — write scene_actions[0..N-1] with the twist in mind. Earlier scenes plant subtle
foreshadowing that only makes sense once the twist lands. The final scene delivers the reveal.

Common twist patterns (pick one that fits the niche):
- IDENTITY REVERSAL: protagonist IS the threat. ("The hunter is the Tikbalang in disguise.")
- POV REVERSAL: viewer was watching from the wrong perspective. ("The fisherman has been the
  prey from the start — the camera was the Sirena's eyes.")
- TIME REVERSAL: the events already happened. ("This is the protagonist's last memory; they
  died at scene 1.")
- ROLE REVERSAL: the assumed victim is the predator. ("The lost child wasn't being hunted —
  she was leading hunters into the swamp.")
- LOOP REVELATION: the cycle has been repeating. ("The hunter is the next iteration of a
  trap. The creature he killed was him, one cycle ago.")
- REALITY REVERSAL: it wasn't real. ("The 'rainforest' is a snowglobe in a child's bedroom.")

Arc skeletons by scene count (each scene has its own peak action AND advances the arc):
- 4 scenes: HOOK (plants foreshadow seed) · INCITING (threat appears) · CLIMAX (peak conflict) · TWIST (reveal)
- 6 scenes: HOOK · INCITING · ESCALATION · CRISIS · CLIMAX · TWIST
- 8 scenes: HOOK · INCITING · ESCALATION 1 · ESCALATION 2 · CRISIS · CLIMAX · TWIST · LINGERING IMAGE

Foreshadowing rules:
- The HOOK should contain ONE small visual detail that, in retrospect, is the twist signaled.
  (E.g. the hunter's shadow has hooves — only visible if you look for it.)
- The CLIMAX or scene before the TWIST should be a "bridge moment" — the protagonist sees
  something (a reflection, an item, a glance) that hints at what's coming.
- The TWIST scene should be the foreshadowed thing made undeniable.

STRONG full example (4 scenes, Tikbalang, with twist):
  twist_premise: "The hunter is the Tikbalang in human disguise — luring his future self into a fatal loop."
  Scene 1 (HOOK):     "Hunter sprints through fog, machete hacking branches — but his shadow on the moss has hooves."
  Scene 2 (INCITING): "A second figure crashes from the treeline, same face, same machete — but with horse legs below the waist."
  Scene 3 (CLIMAX):   "Hunter raises his blade as the creature stops short — they lock eyes and the creature smiles in recognition."
  Scene 4 (TWIST):    "Hunter glances down at his own legs as fur bristles outward, hooves cracking through his boots."

Why this works:
- Scene 1's shadow is a seed the viewer probably misses on first watch.
- Scene 2's "same face" should feel uncanny but the viewer attributes it to creature mimicry.
- Scene 3's "smiles in recognition" is the bridge — wait, why is it smiling?
- Scene 4 reveals the loop. On rewatch, every prior scene has new meaning.

WEAK examples to AVOID:
  - Surprise without setup: scenes 1-3 are a normal chase, scene 4 is "and then a meteor hits."
    Random ≠ twist.
  - Twist that doesn't reframe: "the hunter dies" is just an ending, not a twist. Test: does
    knowing the twist change how you read scene 1? If no, it's just an ending.
  - Foreshadowing that's too obvious: "Hunter has hooves and a horse mane in scene 1." That's
    not foreshadowing, that's spoiling.

Continuity is the WORLD ANCHORS' job — they repeat verbatim across all N clips. The shot and
action layers vary freely while telling ONE connected story with a reframing twist at the end.

YOUTUBE METADATA: write the title and description AFTER the storyboard is settled — the
metadata refers to what the video actually contains, not generic niche text.

youtube_title (max 60 characters):
- Lead with the niche keyword (Tikbalang, Aswang, Manananggal, etc.) — these are
  high-search-volume terms on YouTube.
- Hint at the twist without spoiling it. Curiosity gap, not reveal.
- No ALL-CAPS, no "AMAZING"/"INSANE"/"YOU WON'T BELIEVE", at most one emoji.
- Story-shaped beats descriptive. Title-case is fine.

GOOD title examples:
  "He Thought He Was Hunting the Tikbalang"
  "The Tikbalang Hunter's Last Mistake"
  "POV: You Can't Outrun the Aswang"
  "She Didn't Recognize the Diwata's Gift"

BAD title examples (do NOT do these):
  "AMAZING Filipino Monster!! 🔥🔥🔥"  — clickbait, demoted by the algorithm
  "Filipino Mythology Story #5"       — generic, no niche signal
  "Hunter Becomes Tikbalang at the End!" — spoils the twist

youtube_description (3-5 short lines):
Line 1: the hook restated for the algorithm preview, ≤140 chars.
Lines 2-3: 1-2 short paragraphs expanding the premise WITHOUT spoiling the twist. Reference
the niche by name so the algorithm tags the video correctly.
Line 4 (final): 3-5 niche-specific hashtags on one line, space-separated.

Example description (Tikbalang twist video):
  "Deep in the rainforest, a hunter pursues a creature said to vanish before it's caught.

  A short Filipino mythology piece exploring the legend of the Tikbalang — the horse-headed
  trickster who lures travelers off the path.

  #FilipinoMythology #Tikbalang #Folklore #FilipinoFolklore #Shorts"

Hashtag patterns by niche:
  filipino-mythology: #FilipinoMythology #Folklore #FilipinoFolklore #<creature> #Shorts
  cosmic-horror: #CosmicHorror #Lovecraftian #LiminalSpace #DreadCore #Shorts
  cinematic: #Cinematic #ShortFilm #AIArt #Atmospheric #Shorts

Strong action verbs to favor: sprint, lunge, dive, whip, crash, lash, burst, slam, surge,
plunge, tear, vault, twist, erupt, shatter, recoil, hurtle, fling, snap, claw, leap.
Weak verbs to AVOID: walks, stands, looks, watches, sees, appears, waits, turns, gazes.

Examples of strong scene_actions:
  "Hunter sprints through fog as low branches whip across his face."
  "Tikbalang's hooves crash through underbrush, kicking up wet leaves and dust."
  "Vines lash sideways as the hunter dives under a fallen log, rolling onto his back."
  "Camera whips up to ancient ruins erupting with luminous moss as the figure vanishes."

Examples of weak scene_actions to REJECT and rewrite:
  "Hunter walks deeper into the rainforest." — pure traversal, no peak
  "Hunter looks around uneasily, sensing something." — reaction-only
  "Tikbalang appears in the distance." — static reveal, no kinetic energy
  "Mist curls around ancient trees." — atmosphere-only, no subject doing anything

Avoid: complex multi-character dialogue, lip-sync, fast cuts within a scene, hands doing detail
work, text/signs in the world (current AI video models render these badly), STATIC POSES,
PURE TRAVERSAL, REACTION-ONLY SHOTS, ATMOSPHERE-ONLY SHOTS.
"""

USER_TEMPLATE = """Idea: {idea}
Niche: {niche}
Number of scenes (N): {num_scenes}"""


def get_structured_llm():
    """Build a ChatOpenAI pointed at NVIDIA Build, wrapped to return Storyboard."""
    llm = ChatOpenAI(
        model=settings.llm.composer_model,
        api_key=settings.llm.nvidia_api_key,
        base_url=settings.llm.nvidia_base_url,
        temperature=0.7,
        max_tokens=8000,
    )
    return llm.with_structured_output(Storyboard)


async def compose(
    idea: str,
    niche: str | None,
    num_scenes: int,
    pov_caption_override: str | None = None,
) -> Storyboard:
    structured_llm = get_structured_llm()
    user = USER_TEMPLATE.format(
        idea=idea,
        niche=niche or "unspecified",
        num_scenes=num_scenes,
    )

    storyboard: Storyboard = await structured_llm.ainvoke(
        [SystemMessage(content=SYSTEM), HumanMessage(content=user)]
    )

    # Asymmetric on purpose: extra scenes are silently truncated, but a
    # shortfall fails loudly. We can always drop scenes we don't need; we
    # can't fabricate scenes we wanted but didn't get.
    if len(storyboard.scene_actions) > num_scenes:
        storyboard.scene_actions = storyboard.scene_actions[:num_scenes]
    if len(storyboard.scene_shots) > num_scenes:
        storyboard.scene_shots = storyboard.scene_shots[:num_scenes]
    if len(storyboard.scene_actions) < num_scenes:
        raise ValueError(
            f"composer returned {len(storyboard.scene_actions)} scene_actions, "
            f"expected {num_scenes}"
        )
    if len(storyboard.scene_shots) != len(storyboard.scene_actions):
        raise ValueError(
            f"scene_shots ({len(storyboard.scene_shots)}) and scene_actions "
            f"({len(storyboard.scene_actions)}) must have the same length"
        )

    if pov_caption_override:
        storyboard.pov_caption = pov_caption_override

    return storyboard
