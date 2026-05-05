"""Pydantic request/response models for the /jobs endpoints."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.config import settings


class JobCreate(BaseModel):
    idea: str = Field(..., min_length=4)
    niche: str | None = None
    num_scenes: int = Field(default=8, ge=2, le=settings.max_scenes)
    # Picks the storyboard schema and composer prompt. "narrative" is the
    # default (single connected arc with twist); "top5" produces a countdown
    # of 5 self-contained ranked moments. When mode == "top5" the runner
    # forces num_scenes to 5 — the format is fixed at five items.
    mode: Literal["narrative", "top5"] = "narrative"
    pov_caption: str | None = None
    music_track: str | None = None
    music_mode: Literal["import", "generate"] = "generate"
    webhook_url: str | None = None


class JobCreateResponse(BaseModel):
    job_id: str
    status: str = "queued"


class JobStatus(BaseModel):
    job_id: str
    status: str
    stage: str | None = None
    scene: int | None = None
    total_scenes: int | None = None
    result_url: str | None = None
    error: str | None = None
