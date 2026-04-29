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
  Visual continuity comes ONLY from the anchor strings being repeated VERBATIM in every scene's prompt.
- style_anchor: 1 sentence. Camera, lens, palette, lighting, mood. Reused verbatim every scene.
  Example: "Cinematic 35mm anamorphic, teal-and-amber grade, low-key lighting, anamorphic lens flares, slow drifting camera."
- setting_anchor: 1 sentence. Location + atmosphere + time of day. No action.
- character_anchors: empty string OR a single line listing each character with sharp visual detail
  (age, ethnicity, hair, distinguishing marks, exact wardrobe). Empty for pure-POV / landscape pieces.
- pov_caption: ONE on-screen hook in canonical Shorts format.
  "POV: You are <subject>" or "POV: <situation>". Max 9 words. No emoji.
- scene_actions: exactly N items. Each ~10-18 words. Visual beat only — no camera direction,
  no dialogue. Each beat must visually flow from the previous one.

Avoid: complex multi-character dialogue, lip-sync, fast cuts within a scene, hands doing detail work,
text/signs in the world (current AI video models render these badly).
"""

USER_TEMPLATE = """Idea: {idea}
Niche: {niche}
Number of scenes (N): {num_scenes}"""


def get_structured_llm():
    """Build a ChatOpenAI pointed at NVIDIA Build, wrapped to return Storyboard."""
    llm = ChatOpenAI(
        model=settings.composer_model,
        api_key=settings.nvidia_api_key,
        base_url=settings.nvidia_base_url,
        temperature=0.7,
        max_tokens=2000,
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
    if len(storyboard.scene_actions) < num_scenes:
        raise ValueError(
            f"composer returned {len(storyboard.scene_actions)} scenes, expected {num_scenes}"
        )

    if pov_caption_override:
        storyboard.pov_caption = pov_caption_override

    return storyboard
