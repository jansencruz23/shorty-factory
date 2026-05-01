"""HTTP routes: POST /jobs (enqueue), GET /jobs/{id} (status),
GET /jobs/{id}/download (stream final.mp4), GET /healthz (readiness)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.api.schemas import JobCreate, JobCreateResponse, JobStatus
from app.config import settings
from app.graph.state import JobState
from app.jobs import runner, store
from app.storage import paths_for

router = APIRouter()


@router.post("/jobs", response_model=JobCreateResponse, status_code=202)
async def create_job(req: JobCreate) -> JobCreateResponse:
    # Single-flight: meta.ai's per-account budget can't safely sustain two
    # parallel sessions on one storage_state. Reject overlapping requests so
    # an n8n retry on a transient 502 doesn't double-fire the pipeline.
    if await store.has_active_job():
        raise HTTPException(429, "another job is currently queued or running")

    # 12 hex chars ≈ 48 bits of entropy. Collision risk is negligible at
    # any scale this service is plausibly operating at.
    job_id = uuid.uuid4().hex[:12]
    initial: JobState = {
        "job_id": job_id,
        "idea": req.idea,
        "niche": req.niche,
        "num_scenes": req.num_scenes,
        "pov_caption_override": req.pov_caption,
        "music_track": req.music_track,
        "music_mode": req.music_mode,
    }
    await store.create_job(job_id, dict(initial), req.webhook_url)
    runner.spawn(job_id, initial, req.webhook_url)
    return JobCreateResponse(job_id=job_id)


@router.get("/healthz")
async def healthz() -> dict:
    """Readiness probe consumed by n8n's daily cron guard. Returns:
    - ready: true if storage_state.json is present and no job is in-flight
    - last_success_at / ran_today: drives "skip if today already produced"
    - has_active_job: drives "wait until current job finishes"
    """
    last = await store.get_last_success()
    last_at = last.updated_at if last else None
    ran_today = bool(last_at and last_at.astimezone(timezone.utc).date() == datetime.now(timezone.utc).date())
    storage_state_present = settings.meta_storage_state.exists()
    active = await store.has_active_job()
    return {
        "ready": storage_state_present and not active,
        "last_success_at": last_at.isoformat() if last_at else None,
        "ran_today": ran_today,
        "has_active_job": active,
        "storage_state_present": storage_state_present,
    }


@router.get("/jobs/{job_id}", response_model=JobStatus)
async def get_job(job_id: str) -> JobStatus:
    job = await store.get_job(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return JobStatus(
        job_id=job.job_id,
        status=job.status,
        stage=job.stage,
        scene=job.scene,
        total_scenes=job.total_scenes,
        result_url=job.result_url,
        error=job.error,
    )


@router.get("/jobs/{job_id}/download")
async def download(job_id: str):
    job = await store.get_job(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    if job.status != "done":
        raise HTTPException(409, f"job not ready (status={job.status})")

    final = paths_for(job_id).final
    if not Path(final).exists():
        raise HTTPException(410, "final file gone")
    return FileResponse(str(final), media_type="video/mp4", filename=f"{job_id}.mp4")
