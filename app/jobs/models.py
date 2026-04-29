"""SQLModel table for job rows. Persists progress so the API can report
status while the runner coroutine drives the LangGraph pipeline."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Job(SQLModel, table=True):
    job_id: str = Field(primary_key=True)
    status: str = Field(default="queued", index=True)
    stage: str | None = None
    scene: int | None = None
    total_scenes: int | None = None

    state_json: str = Field(default="{}")
    webhook_url: str | None = None
    result_url: str | None = None
    error: str | None = None

    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
