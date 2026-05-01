"""ORM-backed CRUD for the Job row. Read by the API; written by the runner."""

from __future__ import annotations
import json
from datetime import datetime, timezone
from typing import Any

from sqlmodel import select

from app.db import async_session_factory
from app.jobs.models import Job

ACTIVE_STATUSES = ("queued", "running")


async def create_job(job_id: str, state: dict[str, Any], webhook_url: str | None) -> None:
    async with async_session_factory() as session:
        job = Job(
            job_id=job_id,
            state_json=json.dumps(state, default=str),
            total_scenes=state.get("num_scenes"),
            webhook_url=webhook_url,
        )
        session.add(job)
        await session.commit()


async def update_progress(
    job_id: str,
    *,
    status: str | None = None,
    stage: str | None = None,
    scene: int | None = None,
    error: str | None = None,
    result_url: str | None = None,
    state_patch: dict[str, Any] | None = None,
) -> None:
    async with async_session_factory() as session:
        job = await session.get(Job, job_id)
        if job is None:
            raise KeyError(job_id)

        if state_patch:
            current = json.loads(job.state_json)
            current.update(state_patch)
            job.state_json = json.dumps(current, default=str)

        if status is not None:
            job.status = status
        if stage is not None:
            job.stage = stage
        if scene is not None:
            job.scene = scene
        if error is not None:
            job.error = error
        if result_url is not None:
            job.result_url = result_url

        job.updated_at = datetime.now(timezone.utc)
        session.add(job)
        await session.commit()


async def get_job(job_id: str) -> Job | None:
    async with async_session_factory() as session:
        return await session.get(Job, job_id)


async def list_active_jobs() -> list[Job]:
    """Jobs currently queued or running. Used for single-flight guard and
    startup-time orphan reconciliation after a uvicorn restart."""
    async with async_session_factory() as session:
        stmt = select(Job).where(Job.status.in_(ACTIVE_STATUSES))
        result = await session.execute(stmt)
        return list(result.scalars().all())


async def has_active_job() -> bool:
    """True if any job is queued or running. Cheap pre-check for POST /jobs."""
    async with async_session_factory() as session:
        stmt = select(Job).where(Job.status.in_(ACTIVE_STATUSES)).limit(1)
        result = await session.execute(stmt)
        return result.scalar_one_or_none() is not None


async def get_last_success() -> Job | None:
    """Most recently completed job. Drives /healthz `last_success_at` and
    n8n's once-per-day cron guard."""
    async with async_session_factory() as session:
        stmt = select(Job).where(Job.status == "done").order_by(Job.updated_at.desc()).limit(1)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()
