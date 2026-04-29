"""HTTP routes: POST /jobs (enqueue), GET /jobs/{id} (status),
GET /jobs/{id}/download (stream final.mp4)."""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.api.schemas import JobCreate, JobCreateResponse, JobStatus
from app.graph.state import JobState
from app.jobs import runner, store
from app.storage import paths_for

router = APIRouter()


@router.post("/jobs", response_model=JobCreateResponse, status_code=202)
async def create_job(req: JobCreate) -> JobCreateResponse:
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
