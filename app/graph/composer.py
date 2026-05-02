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

Continuity is the WORLD ANCHORS' job — they repeat verbatim. The shot and action layers vary
freely. Treat the N scenes as N different exciting moments from the same scenario, captured
from N different camera setups, not N sequential frames of one slow event.

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
        max_tokens=4000,
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
