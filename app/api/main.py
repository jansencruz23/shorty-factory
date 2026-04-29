"""FastAPI app + lifespan. Tables are created once at startup;
routes are mounted from app.api.routes."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import router
from app.db import init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
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
