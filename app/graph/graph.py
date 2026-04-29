"""LangGraph wiring.

Nodes:
    compose      -> Storyboard
    generate     -> per-scene clip MP4s via Playwright
    stitch       -> 9:16 + caption overlay -> stitched.mp4
    music        -> music bed muxed -> final.mp4

State is the JobState TypedDict from app.graph.state.
"""

from __future__ import annotations

import logging
from pathlib import Path

from langgraph.graph import END, StateGraph

from app.graph import composer as composer_mod
from app.graph import meta_ai
from app.graph import music as music_mod
from app.graph import stitcher as stitcher_mod
from app.graph.state import JobState
from app.storage import paths_for

logger = logging.getLogger(__name__)


async def node_compose(state: JobState) -> JobState:
    sb = await composer_mod.compose(
        idea=state["idea"],
        niche=state.get("niche"),
        num_scenes=state["num_scenes"],
        pov_caption_override=state.get("pov_caption_override"),
    )
    paths = paths_for(state["job_id"])
    paths.storyboard_json.write_text(sb.model_dump_json(indent=2), encoding="utf-8")
    return {"storyboard": sb}


async def node_generate(state: JobState) -> JobState:
    sb = state["storyboard"]
    paths = paths_for(state["job_id"])
    prompts = [sb.prompt_for_scene(i) for i in range(len(sb.scene_actions))]
    clip_paths = await meta_ai.generate_clips(prompts, paths.clip_path)
    return {"clip_paths": [str(p) for p in clip_paths]}


async def node_stitch(state: JobState) -> JobState:
    paths = paths_for(state["job_id"])
    sb = state["storyboard"]
    out = await stitcher_mod.stitch(
        [Path(p) for p in state["clip_paths"]],
        sb.pov_caption,
        paths.stitched,
    )
    return {"stitched_path": str(out)}


async def node_music(state: JobState) -> JobState:
    paths = paths_for(state["job_id"])
    final = await music_mod.add_music(
        stitched_mp4=paths.stitched,
        music_dest=paths.music_track,
        final_dest=paths.final,
        niche=state.get("niche"),
        music_track=state.get("music_track"),
        mode=state.get("music_mode", "import"),
    )
    return {"music_path": str(paths.music_track), "final_path": str(final)}


def build_graph():
    g = StateGraph(JobState)
    g.add_node("compose", node_compose)
    g.add_node("generate", node_generate)
    g.add_node("stitch", node_stitch)
    g.add_node("music", node_music)

    g.set_entry_point("compose")
    g.add_edge("compose", "generate")
    g.add_edge("generate", "stitch")
    g.add_edge("stitch", "music")
    g.add_edge("music", END)

    return g.compile()
