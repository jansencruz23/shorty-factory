"""Composer dispatcher (public entry point).

Routes to the per-mode composer in app.graph.composers based on the `mode`
field. Per-mode implementations own their own SYSTEM prompts and validation
logic — this file only picks one.

Adding a third mode is one new file under composers/ plus one branch here."""

from __future__ import annotations

from langchain_openai import ChatOpenAI

from app.config import settings
from app.graph.storyboards import BaseStoryboard


def get_chat_llm() -> ChatOpenAI:
    """Shared LLM construction. Per-mode composers wrap this with their
    own `with_structured_output(<their model>)` call so the schema binding
    is per-call, not module-level."""
    return ChatOpenAI(
        model=settings.llm.composer_model,
        api_key=settings.llm.nvidia_api_key,
        base_url=settings.llm.nvidia_base_url,
        temperature=0.7,
        max_tokens=40000,
    )


async def compose(
    *,
    idea: str,
    niche: str | None,
    num_scenes: int,
    mode: str,
    pov_caption_override: str | None = None,
) -> BaseStoryboard:
    # Lazy submodule imports keep both prompts off the import path until
    # the mode is picked — speeds up cold start when only one mode is used.
    if mode == "narrative":
        from app.graph.composers.narrative import compose_narrative

        return await compose_narrative(
            idea=idea,
            niche=niche,
            num_scenes=num_scenes,
            pov_caption_override=pov_caption_override,
        )
    if mode == "top5":
        from app.graph.composers.top5 import compose_top5

        # num_scenes is fixed at 5 for top-5 — the runner already clamps it
        # before reaching here, but the per-mode composer ignores it either way.
        return await compose_top5(idea=idea, niche=niche)
    raise ValueError(f"unknown mode: {mode!r}")
