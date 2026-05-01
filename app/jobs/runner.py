"""Background job runner.

One coroutine per job — runs the LangGraph end-to-end, persists progress to
the ORM, and posts to the webhook (if any) on success/failure.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import settings
from app.graph.graph import build_graph
from app.graph.state import JobState
from app.jobs import store

logger = logging.getLogger(__name__)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8), reraise=True)
async def _post_webhook_attempt(url: str, payload: dict[str, Any]) -> None:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()


async def post_webhook(url: str, payload: dict[str, Any]) -> None:
    """Best-effort POST to a job webhook. Retries 3x with exponential backoff;
    on final failure logs and swallows so a webhook outage never fails the job
    itself. Public so the startup orphan-reconciliation in app.api.main can
    re-use it."""
    try:
        await _post_webhook_attempt(url, payload)
    except Exception as e:
        logger.warning("webhook to %s failed after retries: %s", url, e)


async def reconcile_orphaned_jobs() -> None:
    """On startup, mark any job left in 'queued'/'running' as errored — that
    only happens when uvicorn died mid-job. Also fire the webhook so n8n's
    Wait node receives a terminal signal instead of timing out after 4h."""
    rows = await store.list_active_jobs()
    if not rows:
        return
    logger.warning("reconciling %d orphaned job(s) from previous run", len(rows))
    for job in rows:
        err = "orphaned by restart"
        await store.update_progress(job.job_id, status="error", error=err)
        if job.webhook_url:
            await post_webhook(
                job.webhook_url,
                {"job_id": job.job_id, "status": "error", "error": err},
            )


async def run_job(job_id: str, initial_state: JobState, webhook_url: str | None) -> None:
    graph = build_graph()
    await store.update_progress(job_id, status="running", stage="compose")

    try:
        async for event in graph.astream(initial_state, stream_mode="updates"):
            for node_name, patch in event.items():
                logger.info("[%s] node %s done", job_id, node_name)
                # Filter out non-JSON-serializable values (e.g. Storyboard
                # Pydantic instances). They live in storyboard.json on disk
                # already; sqlite is for progress reporting only.
                # Note: stage is owned by the nodes themselves (they update it
                # at start), so we don't write stage here — that would clobber
                # the *current* stage with the *just-finished* node name.
                serializable = {
                    k: v for k, v in patch.items() if isinstance(v, (str, int, float, bool, list))
                }
                await store.update_progress(job_id, state_patch=serializable)

        result_url = f"{settings.public_base_url.rstrip('/')}/jobs/{job_id}/download"
        await store.update_progress(job_id, status="done", stage="done", result_url=result_url)
        if webhook_url:
            await post_webhook(
                webhook_url,
                {"job_id": job_id, "status": "done", "result_url": result_url},
            )

    except Exception as e:
        logger.exception("[%s] job failed", job_id)
        # Some exceptions stringify to "" (validation errors, bare raises);
        # always include the type so the DB row is never blank.
        err_msg = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
        await store.update_progress(job_id, status="error", error=err_msg)
        if webhook_url:
            await post_webhook(
                webhook_url,
                {"job_id": job_id, "status": "error", "error": err_msg},
            )


def spawn(job_id: str, initial_state: JobState, webhook_url: str | None) -> asyncio.Task:
    # Fire-and-forget: we don't await this. If uvicorn restarts mid-job, the
    # task dies with the process and the row is left in `running`. Acceptable
    # for v1; swap to arq/Celery if you need durability.
    return asyncio.create_task(run_job(job_id, initial_state, webhook_url))
