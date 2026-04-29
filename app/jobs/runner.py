"""Background job runner.

One coroutine per job — runs the LangGraph end-to-end, persists progress to
sqlite, and posts to the webhook (if any) on success/failure.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from app.config import settings
from app.graph.graph import build_graph
from app.graph.state import JobState
from app.jobs import store

logger = logging.getLogger(__name__)


async def _post_webhook(url: str, payload: dict[str, Any]) -> None:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(url, json=payload)
    except Exception as e:
        logger.warning("webhook post to %s failed: %s", url, e)


async def run_job(job_id: str, initial_state: JobState, webhook_url: str | None) -> None:
    graph = build_graph()
    await store.update_progress(job_id, status="running", stage="compose")

    try:
        # Stream node updates so progress lands in sqlite as the graph advances.
        async for event in graph.astream(initial_state, stream_mode="updates"):
            for node_name, patch in event.items():
                logger.info("[%s] node %s done", job_id, node_name)
                await store.update_progress(
                    job_id,
                    stage=node_name,
                    state_patch={k: v for k, v in patch.items() if isinstance(v, (str, int, float, bool, list))},
                )

        result_url = f"{settings.public_base_url.rstrip('/')}/jobs/{job_id}/download"
        await store.update_progress(job_id, status="done", stage="done", result_url=result_url)
        if webhook_url:
            await _post_webhook(webhook_url, {"job_id": job_id, "status": "done", "result_url": result_url})

    except Exception as e:
        logger.exception("[%s] job failed", job_id)
        await store.update_progress(job_id, status="error", error=str(e))
        if webhook_url:
            await _post_webhook(webhook_url, {"job_id": job_id, "status": "error", "error": str(e)})


def spawn(job_id: str, initial_state: JobState, webhook_url: str | None) -> asyncio.Task:
    return asyncio.create_task(run_job(job_id, initial_state, webhook_url))
