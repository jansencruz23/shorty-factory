"""Video provider port + factory.

Resolves a provider name (from settings or per-job override) to a concrete
adapter implementing the VideoProvider Protocol. Adapter modules are
imported lazily so adding a heavy dependency (e.g. an HTTP SDK for Runway)
doesn't slow startup unless that adapter is actually selected.
"""

from __future__ import annotations

from app.providers.video.base import ProgressCb, VideoProvider

__all__ = ["VideoProvider", "ProgressCb", "get_video_provider"]


def get_video_provider(name: str) -> VideoProvider:
    if name == "meta_ai":
        from app.providers.video.meta_ai import MetaAIVideoProvider

        return MetaAIVideoProvider()
    raise ValueError(f"unknown video provider: {name!r}")
