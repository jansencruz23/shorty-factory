"""SQLite-backed job store.

Schema is intentionally tiny — `state` is a JSON blob so we don't have to
migrate when the JobState TypedDict grows new fields.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from app.config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id        TEXT PRIMARY KEY,
    status        TEXT NOT NULL,
    stage         TEXT,
    scene         INTEGER,
    total_scenes  INTEGER,
    state_json    TEXT NOT NULL,
    error         TEXT,
    result_url    TEXT,
    webhook_url   TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def init_db() -> None:
    async with aiosqlite.connect(settings.jobs_db) as db:
        await db.executescript(SCHEMA)
        await db.commit()


async def create_job(job_id: str, state: dict[str, Any], webhook_url: str | None) -> None:
    now = _now()
    async with aiosqlite.connect(settings.jobs_db) as db:
        await db.execute(
            "INSERT INTO jobs (job_id, status, stage, scene, total_scenes, state_json, "
            "webhook_url, created_at, updated_at) VALUES (?, 'queued', NULL, 0, ?, ?, ?, ?, ?)",
            (job_id, state.get("num_scenes"), json.dumps(state), webhook_url, now, now),
        )
        await db.commit()


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
    async with aiosqlite.connect(settings.jobs_db) as db:
        async with db.execute(
            "SELECT state_json FROM jobs WHERE job_id = ?", (job_id,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            raise KeyError(job_id)
        current = json.loads(row[0])
        if state_patch:
            current.update(state_patch)

        sets = ["state_json = ?", "updated_at = ?"]
        params: list[Any] = [json.dumps(current, default=str), _now()]
        if status is not None:
            sets.append("status = ?")
            params.append(status)
        if stage is not None:
            sets.append("stage = ?")
            params.append(stage)
        if scene is not None:
            sets.append("scene = ?")
            params.append(scene)
        if error is not None:
            sets.append("error = ?")
            params.append(error)
        if result_url is not None:
            sets.append("result_url = ?")
            params.append(result_url)
        params.append(job_id)

        await db.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE job_id = ?", params)
        await db.commit()


async def get_job(job_id: str) -> dict[str, Any] | None:
    async with aiosqlite.connect(settings.jobs_db) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,))
        row = await cur.fetchone()
        if row is None:
            return None
        return dict(row)
