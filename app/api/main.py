"""FastAPI app + lifespan. Tables are created once at startup;
routes are mounted from app.api.routes."""

from __future__ import annotations

import logging
import shutil
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from fastapi import FastAPI

from app.api.routes import router
from app.config import settings
from app.db import init_db
from app.jobs.runner import reconcile_orphaned_jobs

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

OUTPUTS_RETENTION_DAYS = 7


def _cleanup_old_outputs(retention_days: int = OUTPUTS_RETENTION_DAYS) -> None:
    """Drop intermediate clip dirs and stitched.mp4 for jobs older than
    `retention_days`. Keeps `final.mp4` and storyboard.json so old uploads
    remain inspectable. Disk fill is otherwise guaranteed."""
    if not settings.outputs_dir.exists():
        return
    cutoff = datetime.now() - timedelta(days=retention_days)
    pruned = 0
    for job_dir in settings.outputs_dir.iterdir():
        if not job_dir.is_dir():
            continue
        try:
            mtime = datetime.fromtimestamp(job_dir.stat().st_mtime)
        except OSError:
            continue
        if mtime > cutoff:
            continue
        clips = job_dir / "clips"
        stitched = job_dir / "stitched.mp4"
        if clips.is_dir():
            shutil.rmtree(clips, ignore_errors=True)
            pruned += 1
        if stitched.is_file():
            stitched.unlink(missing_ok=True)
            pruned += 1
    if pruned:
        logger.info("outputs cleanup: pruned %d intermediate file/dir(s)", pruned)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await reconcile_orphaned_jobs()
    _cleanup_old_outputs()
    yield


app = FastAPI(
    title="shorty-factory",
    description="Compose Meta AI clips into vertical shorts.",
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
