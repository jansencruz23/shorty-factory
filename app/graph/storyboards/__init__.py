"""Storyboard schemas — one module per mode plus a shared base.

Graph nodes downstream of compose only see the BaseStoryboard protocol.
Adding a third mode (e.g. explainer) is a new file here plus a composer; no
edits to state.py, graph.py, or stitch.py."""

from app.graph.storyboards.base import BaseStoryboard, CaptionPlan
from app.graph.storyboards.narrative import Storyboard
from app.graph.storyboards.top5 import TopFiveItem, TopFiveStoryboard

__all__ = [
    "BaseStoryboard",
    "CaptionPlan",
    "Storyboard",
    "TopFiveItem",
    "TopFiveStoryboard",
]
