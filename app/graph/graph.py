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

from app.config import settings
from app.graph import composer as composer_mod
from app.graph.state import JobState
from app.jobs import store
from app.pipeline import mux as mux_mod
from app.pipeline import stitch as stitch_mod
from app.providers.music import MUSIC_MODE_TO_PROVIDER, get_music_provider
from app.providers.video import get_video_provider
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
    job_id = state["job_id"]
    sb = state["storyboard"]
    paths = paths_for(job_id)
    prompts = [sb.prompt_for_scene(i) for i in range(len(sb.scene_actions))]

    await store.update_progress(job_id, stage="generate", scene=0)

    async def on_scene(scene_num: int) -> None:
        await store.update_progress(job_id, stage="generate", scene=scene_num)

    provider = get_video_provider(settings.video_provider)
    clip_paths = await provider.generate_clips(prompts, paths.clip_path, progress_cb=on_scene)
    return {"clip_paths": [str(p) for p in clip_paths]}


async def node_stitch(state: JobState) -> JobState:
    job_id = state["job_id"]
    await store.update_progress(job_id, stage="stitch")
    paths = paths_for(job_id)
    sb = state["storyboard"]
    out = await stitch_mod.stitch(
        [Path(p) for p in state["clip_paths"]],
        sb.pov_caption,
        paths.stitched,
    )
    return {"stitched_path": str(out)}


async def node_music(state: JobState) -> JobState:
    job_id = state["job_id"]
    await store.update_progress(job_id, stage="music")
    paths = paths_for(job_id)

    # Mode → provider name. The JobCreate `music_mode` ("import" | "generate")
    # is preserved as the public input; internally we resolve it to a
    # MusicProvider via the factory.
    mode = state.get("music_mode", "generate")
    provider_name = MUSIC_MODE_TO_PROVIDER.get(mode)
    if provider_name is None:
        raise ValueError(f"unknown music_mode: {mode!r}")
    provider = get_music_provider(provider_name)

    duration = await mux_mod.probe_duration(paths.stitched)
    await provider.build_track(
        duration,
        paths.music_track,
        niche=state.get("niche"),
        track_override=state.get("music_track"),
    )
    final = await mux_mod.mux(paths.stitched, paths.music_track, paths.final)

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
