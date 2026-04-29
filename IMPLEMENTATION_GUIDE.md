# shorty-factory — implementation guide

You're building this yourself. This doc has everything: architecture, decisions, folder layout, full code for every file, setup steps, and a debugging map.

---

## 1. What you're building

A service that turns one idea into a finished vertical short:

```
idea ──> LLM (storyboard + POV caption)
       ──> Playwright drives meta.ai N times (one ~5s clip per scene)
       ──> ffmpeg stitches to 1080×1920 with persistent caption overlay
       ──> ffmpeg muxes a music bed (imported or MusicGen)
       ──> final.mp4 + signed URL via FastAPI
```

Triggered by n8n / Power Automate over HTTP. Orchestrated by **LangGraph**.

---

## 2. Decisions baked in

These are settled. If any of these don't fit, change them deliberately, not by accident.

| Decision | Why |
|---|---|
| **NVIDIA Build for the LLM** (OpenAI-compatible endpoint via `langchain-openai`) | You picked this. Gives you free-tier access to Llama 3.3 70B / Nemotron / Mixtral. |
| `with_structured_output(Storyboard)` for parsing | One canonical pattern: `structured_llm.ainvoke(...)` returns a parsed `Storyboard` Pydantic instance directly. Default `method="function_calling"` works on Llama 3.1+ on NVIDIA Build. |
| **No voiceover.** One persistent on-screen POV caption (e.g. *"POV: You are an astronaut"*) | You picked this. Simplifies pipeline; aligns with current Shorts hook format. |
| **Music: imported by default, MusicGen as a fallback** | Imported is deterministic + free; MusicGen lazy-imports torch/transformers so the base install stays light. |
| **9:16 vertical with blurred-fill background** | Standard Shorts/Reels look. Lets us accept clips of any aspect from Meta. |
| **Persistent `storage_state.json` for Meta auth** | One manual login → reuse forever. Avoids automating the login form, which Meta breaks aggressively. |
| **LangGraph for orchestration** | Long-running, multi-step, naturally retryable per-node, persisted state. |
| **SQLModel for the ORM** | Built on SQLAlchemy 2.x, Pydantic-flavored, native FastAPI fit. Same author as FastAPI. |
| **Single-process FastAPI + `asyncio.create_task` background runner** | Simplest viable. Swap to arq/Celery later if you need concurrency. |
| **Headed Chromium, human-like timings** | Meta's anti-bot is harsh on headless. |

### Hard risk to acknowledge

Meta AI has **no public video API**. Automating meta.ai likely violates Meta's ToS. Use a **dedicated** Meta account, not your personal one. Selectors **will** break — when they do, edit `META_SELECTORS` in `app/graph/meta_ai.py`.

---

## 3. Tech stack

```
Python ≥ 3.12 (uv)

FastAPI                  HTTP surface
uvicorn                  ASGI server
pydantic / pydantic-settings   config + schemas
sqlmodel                 ORM (built on SQLAlchemy 2.x)
aiosqlite                async sqlite driver
langchain                core message types + parsers
langchain-openai         ChatOpenAI → NVIDIA Build endpoint
langgraph                graph orchestration
playwright               meta.ai automation
tenacity                 retry policy for Playwright
httpx                    webhook callbacks

ffmpeg (system binary)   stitch, mux, drawtext caption
torch + transformers + scipy   ONLY when music_mode="generate"
```

---

## 4. Folder structure

```
shorty-factory/
├── app/
│   ├── __init__.py
│   ├── config.py             # pydantic-settings, .env loader
│   ├── storage.py            # per-job filesystem layout
│   ├── db.py                 # async SQLModel engine + session
│   ├── api/
│   │   ├── __init__.py
│   │   ├── main.py           # FastAPI app + lifespan
│   │   ├── routes.py         # /jobs endpoints
│   │   └── schemas.py        # request/response models
│   ├── graph/
│   │   ├── __init__.py
│   │   ├── state.py          # JobState TypedDict + Storyboard pydantic
│   │   ├── composer.py       # storyboard + POV caption LLM
│   │   ├── meta_ai.py        # Playwright driver (selector dict)
│   │   ├── stitcher.py       # ffmpeg concat + 9:16 + drawtext caption
│   │   ├── music.py          # imported track OR MusicGen + final mux
│   │   └── graph.py          # LangGraph wiring
│   └── jobs/
│       ├── __init__.py
│       ├── models.py         # SQLModel Job table
│       ├── store.py          # ORM-backed CRUD
│       └── runner.py         # background task that runs the graph
├── assets/
│   ├── music/                # royalty-free bg tracks (subdir per niche)
│   └── fonts/                # Inter-Bold.ttf (or rely on Windows fallback)
├── outputs/                  # per-job dirs (gitignored)
├── scripts/
│   └── capture_session.py    # one-time headed login
├── storage_state.json        # gitignored, written by capture_session
├── jobs.sqlite               # gitignored, created by SQLModel on startup
├── .env                      # gitignored (copy from .env.example)
├── .env.example
├── .gitignore
├── pyproject.toml
├── uv.lock
└── README.md
```

---

## 5. Build in this order

Follow the order — each step compiles cleanly given the previous one.

1. `pyproject.toml`, `.gitignore`, `.env.example` — project skeleton.
2. `uv sync` to install deps.
3. `uv run playwright install chromium`.
4. `app/config.py` — settings, paths, `resolve_caption_font()`.
5. `app/storage.py` — `paths_for(job_id)`.
6. `app/graph/state.py` — `Storyboard` Pydantic + `JobState` TypedDict.
7. `app/db.py` — async engine + session.
8. `app/jobs/models.py` — `Job` SQLModel.
9. `app/jobs/store.py` — CRUD against the ORM.
10. `app/graph/composer.py` — LLM via NVIDIA Build.
11. `app/graph/meta_ai.py` — Playwright driver.
12. `app/graph/stitcher.py` — ffmpeg complex filter.
13. `app/graph/music.py` — import + generate + final mux.
14. `app/graph/graph.py` — LangGraph wiring.
15. `app/jobs/runner.py` — background runner.
16. `app/api/schemas.py`, `routes.py`, `main.py` — HTTP surface.
17. `scripts/capture_session.py` — one-time helper.
18. Run end-to-end (see Section 7).

---

## 6. The code

Every file in the order from Section 5. Read each section, type or copy, run.

### 6.1 `pyproject.toml`

```toml
[project]
name = "shorty-factory"
version = "0.1.0"
description = "Compose 6-8 connected Meta AI video clips into a vertical short, served via FastAPI."
readme = "README.md"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.32",
    "pydantic>=2.9",
    "pydantic-settings>=2.6",
    "sqlmodel>=0.0.22",
    "aiosqlite>=0.20",
    "playwright>=1.48",
    "langchain>=0.3",
    "langchain-openai>=0.2",
    "langgraph>=0.2.50",
    "tenacity>=9.0",
    "httpx>=0.27",
    "python-multipart>=0.0.12",
]

[dependency-groups]
dev = [
    "ruff>=0.7",
    "pytest>=8.3",
    "pytest-asyncio>=0.24",
]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.pytest.ini_options]
asyncio_mode = "auto"
```

### 6.2 `.gitignore`

```gitignore
# Python-generated files
__pycache__/
*.py[oc]
build/
dist/
wheels/
*.egg-info

# Virtual environments
.venv

# Project artefacts
.env
storage_state.json
outputs/
*.sqlite
*.sqlite-journal

# Editor
.vscode/
.idea/
```

### 6.3 `.env.example`

```env
# NVIDIA Build API (OpenAI-compatible endpoint).
# Get a key at https://build.nvidia.com — free tier covers light usage.
NVIDIA_API_KEY=
NVIDIA_BASE_URL=https://integrate.api.nvidia.com/v1

# Composer model. Llama 3.3 70B and Nemotron support tool calling on NVIDIA Build.
COMPOSER_MODEL=meta/llama-3.3-70b-instruct

META_STORAGE_STATE=storage_state.json
PLAYWRIGHT_HEADLESS=false

MAX_SCENES=8
PUBLIC_BASE_URL=http://localhost:8000

CAPTION_FONT_PATH=assets/fonts/Inter-Bold.ttf
CAPTION_FONT_FALLBACK=C:/Windows/Fonts/arialbd.ttf
```

### 6.4 `app/__init__.py`

Empty file. Just makes `app/` a package.

### 6.5 `app/config.py`

Single source of truth for paths, dimensions, and provider config.

```python
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # NVIDIA Build is OpenAI-compatible; we point langchain-openai at it.
    nvidia_api_key: str = ""
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    composer_model: str = "meta/llama-3.3-70b-instruct"

    meta_storage_state: Path = ROOT / "storage_state.json"
    playwright_headless: bool = False

    max_scenes: int = Field(default=8, ge=2, le=12)
    public_base_url: str = "http://localhost:8000"

    outputs_dir: Path = ROOT / "outputs"
    assets_dir: Path = ROOT / "assets"
    jobs_db: Path = ROOT / "jobs.sqlite"

    caption_font_path: Path = ROOT / "assets" / "fonts" / "Inter-Bold.ttf"
    caption_font_fallback: Path = Path("C:/Windows/Fonts/arialbd.ttf")
    caption_font_size: int = 64

    video_width: int = 1080
    video_height: int = 1920

    music_gain_db: float = -8.0


settings = Settings()
settings.outputs_dir.mkdir(parents=True, exist_ok=True)
(settings.assets_dir / "music").mkdir(parents=True, exist_ok=True)
(settings.assets_dir / "fonts").mkdir(parents=True, exist_ok=True)


def resolve_caption_font() -> Path:
    if settings.caption_font_path.exists():
        return settings.caption_font_path
    if settings.caption_font_fallback.exists():
        return settings.caption_font_fallback
    raise FileNotFoundError(
        f"No caption font found. Expected one of: {settings.caption_font_path}, "
        f"{settings.caption_font_fallback}"
    )
```

**Why side-effect mkdirs at import:** convenient for local dev. Be aware: importing `app.config` creates dirs. Acceptable here; would be wrong in a library.

### 6.6 `app/storage.py`

Centralizes per-job filesystem layout. **No other module should construct paths under `outputs/`.**

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.config import settings


@dataclass(frozen=True)
class JobPaths:
    job_id: str
    root: Path

    @property
    def clips_dir(self) -> Path:
        return self.root / "clips"

    @property
    def stitched(self) -> Path:
        return self.root / "stitched.mp4"

    @property
    def music_track(self) -> Path:
        return self.root / "music.m4a"

    @property
    def final(self) -> Path:
        return self.root / "final.mp4"

    @property
    def storyboard_json(self) -> Path:
        return self.root / "storyboard.json"

    def clip_path(self, scene_index: int) -> Path:
        return self.clips_dir / f"scene_{scene_index:02d}.mp4"


def paths_for(job_id: str) -> JobPaths:
    root = settings.outputs_dir / job_id
    root.mkdir(parents=True, exist_ok=True)
    (root / "clips").mkdir(parents=True, exist_ok=True)
    return JobPaths(job_id=job_id, root=root)
```

### 6.7 `app/graph/__init__.py`

Empty.

### 6.8 `app/graph/state.py`

The two key types every other graph file refers to.

```python
from __future__ import annotations

from typing import TypedDict

from pydantic import BaseModel, Field


class Storyboard(BaseModel):
    style_anchor: str = Field(
        ...,
        description="Cinematography, palette, lighting. Reused verbatim every scene.",
    )
    setting_anchor: str = Field(
        ...,
        description="Location, atmosphere, time of day. Reused verbatim every scene.",
    )
    character_anchors: str = Field(
        default="",
        description="Detailed character descriptions, comma-joined. Reused verbatim every scene. "
        "Empty string for pure-POV/landscape pieces.",
    )
    pov_caption: str = Field(
        ...,
        description="The single on-screen hook, e.g. 'POV: You are an astronaut'.",
    )
    scene_actions: list[str] = Field(
        ...,
        description="One short visual action per ~5s scene.",
    )

    def prompt_for_scene(self, index: int) -> str:
        action = self.scene_actions[index]
        parts = [self.style_anchor, self.setting_anchor]
        if self.character_anchors:
            parts.append(self.character_anchors)
        parts.append(f"SCENE: {action}")
        return ". ".join(p.rstrip(". ") for p in parts) + "."


class JobState(TypedDict, total=False):
    job_id: str
    idea: str
    niche: str | None
    num_scenes: int
    pov_caption_override: str | None
    music_track: str | None
    music_mode: str

    storyboard: Storyboard
    clip_paths: list[str]
    stitched_path: str
    music_path: str
    final_path: str

    error: str | None
```

**Why `prompt_for_scene` matters:** this method is the ENTIRE visual-continuity strategy. Anchors repeated verbatim every scene + scene_actions changing per scene = the only thing keeping the look consistent across N independent Meta generations.

**Why `JobState` is `total=False`:** every key is optional. LangGraph nodes return *partial* dicts and merge them.

### 6.9 `app/db.py`

Async SQLModel engine + session factory. One file, the whole DB plumbing.

```python
from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from app.config import settings

# echo=False; flip to True when debugging queries.
engine = create_async_engine(
    f"sqlite+aiosqlite:///{settings.jobs_db}",
    echo=False,
    future=True,
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def init_db() -> None:
    """Create all tables. Idempotent — safe to call on every startup."""
    # Importing the models here ensures their tables register on SQLModel.metadata
    # before create_all runs.
    from app.jobs import models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency. Use as: `session: AsyncSession = Depends(get_session)`."""
    async with async_session_factory() as session:
        yield session
```

### 6.10 `app/jobs/__init__.py`

Empty.

### 6.11 `app/jobs/models.py`

The single Job table. `state_json` is intentionally a JSON string column so we don't migrate when `JobState` grows new fields.

```python
from __future__ import annotations

from datetime import datetime, timezone

from sqlmodel import Field, SQLModel


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Job(SQLModel, table=True):
    __tablename__ = "jobs"

    job_id: str = Field(primary_key=True, max_length=32)
    status: str = Field(default="queued", index=True)
    stage: str | None = None
    scene: int | None = None
    total_scenes: int | None = None

    # Mutable JSON blob mirroring the LangGraph JobState. Kept as text so we
    # don't have to migrate when JobState grows.
    state_json: str = Field(default="{}")

    error: str | None = None
    result_url: str | None = None
    webhook_url: str | None = None

    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
```

### 6.12 `app/jobs/store.py`

ORM-backed CRUD. No raw SQL.

```python
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.db import async_session_factory
from app.jobs.models import Job


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
```

### 6.13 `app/graph/composer.py`

The LLM step. NVIDIA Build via `langchain-openai`. **One** call returns a parsed `Storyboard`.

```python
"""Storyboard composer.

NVIDIA Build's OpenAI-compatible endpoint via langchain-openai.

Pattern:
    structured_llm = ChatOpenAI(...).with_structured_output(Storyboard)
    storyboard = await structured_llm.ainvoke([...])

The default method is "function_calling", which Llama 3.1+/Nemotron/Mixtral
on NVIDIA Build all support. If you pick a model that doesn't expose tool
calling, swap to method="json_mode" (the schema is described inline in the
system prompt, so it'll keep working).
"""
from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.config import settings
from app.graph.state import Storyboard

SYSTEM = """You write storyboards for short-form vertical AI videos (YouTube Shorts / Reels / TikTok).

Hard constraints:
- The video is built from N independent ~5-second AI-generated clips that have NO memory between them.
  Visual continuity comes ONLY from the anchor strings being repeated VERBATIM in every scene's prompt.
- style_anchor: 1 sentence. Camera, lens, palette, lighting, mood. Reused verbatim every scene.
  Example: "Cinematic 35mm anamorphic, teal-and-amber grade, low-key lighting, anamorphic lens flares, slow drifting camera."
- setting_anchor: 1 sentence. Location + atmosphere + time of day. No action.
- character_anchors: empty string OR a single line listing each character with sharp visual detail
  (age, ethnicity, hair, distinguishing marks, exact wardrobe). Empty for pure-POV / landscape pieces.
- pov_caption: ONE on-screen hook in canonical Shorts format.
  "POV: You are <subject>" or "POV: <situation>". Max 9 words. No emoji.
- scene_actions: exactly N items. Each ~10-18 words. Visual beat only — no camera direction,
  no dialogue. Each beat must visually flow from the previous one.

Avoid: complex multi-character dialogue, lip-sync, fast cuts within a scene, hands doing detail work,
text/signs in the world (current AI video models render these badly).
"""

USER_TEMPLATE = """Idea: {idea}
Niche: {niche}
Number of scenes (N): {num_scenes}"""


def get_structured_llm():
    """Build a ChatOpenAI pointed at NVIDIA Build, wrapped to return Storyboard."""
    llm = ChatOpenAI(
        model=settings.composer_model,
        api_key=settings.nvidia_api_key,
        base_url=settings.nvidia_base_url,
        temperature=0.7,
        max_tokens=2000,
    )
    return llm.with_structured_output(Storyboard)


async def compose(
    idea: str,
    niche: str | None,
    num_scenes: int,
    pov_caption_override: str | None = None,
) -> Storyboard:
    structured_llm = get_structured_llm()
    user = USER_TEMPLATE.format(
        idea=idea,
        niche=niche or "unspecified",
        num_scenes=num_scenes,
    )

    storyboard: Storyboard = await structured_llm.ainvoke(
        [SystemMessage(content=SYSTEM), HumanMessage(content=user)]
    )

    if len(storyboard.scene_actions) > num_scenes:
        storyboard.scene_actions = storyboard.scene_actions[:num_scenes]
    if len(storyboard.scene_actions) < num_scenes:
        raise ValueError(
            f"composer returned {len(storyboard.scene_actions)} scenes, expected {num_scenes}"
        )

    if pov_caption_override:
        storyboard.pov_caption = pov_caption_override

    return storyboard
```

**If `pov_caption_override` should also shape the visuals**, thread it into the prompt template instead of overwriting the result.

### 6.14 `app/graph/meta_ai.py`

The riskiest module. **Selectors will break.** When they do, edit `META_SELECTORS`.

```python
"""Playwright driver for meta.ai video generation.

Selector-driven on purpose. Meta will change their UI; when that happens,
edit META_SELECTORS and the small set of helper functions rather than
rewriting the flow.
"""
from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Callable
from pathlib import Path

from playwright.async_api import (
    BrowserContext,
    Page,
    TimeoutError as PWTimeout,
    async_playwright,
)
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import settings

logger = logging.getLogger(__name__)

META_URL = "https://www.meta.ai"

META_SELECTORS: dict[str, list[str] | str] = {
    "prompt_input": [
        'textarea[placeholder*="Ask" i]',
        'div[contenteditable="true"][role="textbox"]',
        'textarea',
    ],
    "submit_button": [
        'button[aria-label*="Send" i]',
        'button[aria-label*="Submit" i]',
        'button[type="submit"]',
    ],
    "video_mode_toggle": [
        'button:has-text("Video")',
        'button[aria-label*="video" i]',
    ],
    "rendered_video": "video[src]",
    "login_wall": 'a[href*="login"], button:has-text("Log in")',
}

GENERATION_TIMEOUT_MS = 180_000  # 3 min per clip
NAV_TIMEOUT_MS = 30_000


class MetaSessionExpired(RuntimeError):
    """storage_state.json no longer authenticates — re-run capture_session.py."""


class MetaUIChanged(RuntimeError):
    """A required selector didn't match. Update META_SELECTORS."""


async def _first_visible(page: Page, selectors: list[str], timeout: int = 5000):
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            await loc.wait_for(state="visible", timeout=timeout)
            return loc
        except PWTimeout:
            continue
    raise MetaUIChanged(f"none of {selectors} were visible on page")


async def _ensure_logged_in(page: Page) -> None:
    await page.goto(META_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
    await asyncio.sleep(1.5)
    if await page.locator(META_SELECTORS["login_wall"]).first.is_visible():
        raise MetaSessionExpired(
            f"meta.ai shows a login wall. Re-run scripts/capture_session.py to refresh "
            f"{settings.meta_storage_state}."
        )


async def _switch_to_video_mode(page: Page) -> None:
    for sel in META_SELECTORS["video_mode_toggle"]:
        try:
            loc = page.locator(sel).first
            if await loc.is_visible():
                await loc.click()
                await asyncio.sleep(0.5)
                return
        except Exception:
            continue
    logger.warning("No explicit video-mode toggle found; proceeding without one.")


async def _submit_prompt(page: Page, prompt: str) -> None:
    input_loc = await _first_visible(page, META_SELECTORS["prompt_input"])
    await input_loc.click()
    await input_loc.type(prompt, delay=random.randint(35, 85))
    await asyncio.sleep(0.4)

    submit_loc = await _first_visible(page, META_SELECTORS["submit_button"], timeout=3000)
    await submit_loc.click()


async def _wait_for_video_and_download(page: Page, dest: Path) -> None:
    video_loc = page.locator(META_SELECTORS["rendered_video"]).last
    await video_loc.wait_for(state="attached", timeout=GENERATION_TIMEOUT_MS)

    src = await video_loc.get_attribute("src")
    if not src:
        raise MetaUIChanged("rendered <video> has no src attribute")

    response = await page.request.get(src)
    if not response.ok:
        raise MetaUIChanged(f"video fetch failed: HTTP {response.status} for {src}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(await response.body())


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=4, max=20),
    retry=retry_if_exception_type((PWTimeout, MetaUIChanged)),
    reraise=True,
)
async def _generate_one(context: BrowserContext, prompt: str, dest: Path) -> None:
    page = await context.new_page()
    try:
        await _ensure_logged_in(page)
        await _switch_to_video_mode(page)
        await _submit_prompt(page, prompt)
        await _wait_for_video_and_download(page, dest)
    finally:
        await page.close()


async def generate_clips(
    prompts: list[str],
    clip_path_for: Callable[[int], Path],
) -> list[Path]:
    """Generate one clip per prompt, sequentially, in a single browser context."""
    if not settings.meta_storage_state.exists():
        raise MetaSessionExpired(
            f"{settings.meta_storage_state} not found. Run scripts/capture_session.py first."
        )

    out_paths: list[Path] = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=settings.playwright_headless)
        context = await browser.new_context(
            storage_state=str(settings.meta_storage_state),
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
            ),
        )
        try:
            for i, prompt in enumerate(prompts):
                dest = clip_path_for(i)
                logger.info("generating clip %d/%d -> %s", i + 1, len(prompts), dest)
                await _generate_one(context, prompt, dest)
                out_paths.append(dest)
                await asyncio.sleep(random.uniform(8, 20))
        finally:
            await context.close()
            await browser.close()

    return out_paths
```

**Two failure classes you need to recognize:**
- `MetaSessionExpired` — login wall detected, or `storage_state.json` missing. Re-run `capture_session.py`.
- `MetaUIChanged` — selectors didn't match. Update `META_SELECTORS`.

### 6.15 `app/graph/stitcher.py`

Single ffmpeg call. Read the filtergraph carefully — it's the densest piece of code in the repo.

```python
"""ffmpeg stitcher.

Takes N clips of arbitrary aspect ratios, normalises each to 1080x1920 with
a blurred-fill background (the standard Shorts/Reels aesthetic), concatenates
them, and burns in a single persistent POV caption overlay. Audio is dropped
here — the music module adds the bed afterwards.

One ffmpeg call with a complex filtergraph. Avoids intermediate files.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from app.config import resolve_caption_font, settings

logger = logging.getLogger(__name__)


def _build_filtergraph(num_inputs: int, caption_textfile: Path, font_path: Path) -> str:
    W, H = settings.video_width, settings.video_height
    parts: list[str] = []

    # Per-input: split into bg/fg, blur bg, scale fg to fit, overlay.
    for i in range(num_inputs):
        parts.append(
            f"[{i}:v]split=2[bg{i}][fg{i}];"
            f"[bg{i}]scale={W}:{H}:force_original_aspect_ratio=increase,"
            f"crop={W}:{H},boxblur=20:1,setsar=1[bgblur{i}];"
            f"[fg{i}]scale={W}:{H}:force_original_aspect_ratio=decrease,setsar=1[fgs{i}];"
            f"[bgblur{i}][fgs{i}]overlay=(W-w)/2:(H-h)/2:format=auto,"
            f"fps=30,format=yuv420p[v{i}]"
        )

    # Concat all normalised streams.
    concat_inputs = "".join(f"[v{i}]" for i in range(num_inputs))
    parts.append(f"{concat_inputs}concat=n={num_inputs}:v=1:a=0[concat]")

    # Persistent POV caption — text comes from a file so we don't have to
    # escape ffmpeg's drawtext metacharacters. Paths still need : escaped
    # because of Windows drive letters.
    fontfile = str(font_path).replace("\\", "/").replace(":", r"\:")
    textfile = str(caption_textfile).replace("\\", "/").replace(":", r"\:")
    parts.append(
        f"[concat]drawtext=fontfile='{fontfile}':textfile='{textfile}':"
        f"fontsize={settings.caption_font_size}:fontcolor=white:"
        f"box=1:boxcolor=black@0.55:boxborderw=20:"
        f"x=(w-text_w)/2:y=h*0.22[out]"
    )

    return ";".join(parts)


async def stitch(clip_paths: list[Path], pov_caption: str, dest: Path) -> Path:
    """Concat + 9:16 normalize + POV caption overlay. Writes muted MP4 to `dest`."""
    if not clip_paths:
        raise ValueError("stitch() requires at least one clip")

    dest.parent.mkdir(parents=True, exist_ok=True)
    font_path = resolve_caption_font()

    # Caption goes through a textfile so ':' and quotes don't break the filter.
    caption_textfile = dest.parent / "caption.txt"
    caption_textfile.write_text(pov_caption.strip(), encoding="utf-8")

    filtergraph = _build_filtergraph(len(clip_paths), caption_textfile, font_path)

    args: list[str] = ["ffmpeg", "-y", "-loglevel", "error"]
    for clip in clip_paths:
        args += ["-i", str(clip)]
    args += [
        "-filter_complex", filtergraph,
        "-map", "[out]",
        "-an",                     # drop audio; music module adds the bed
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(dest),
    ]

    logger.info("stitching %d clips -> %s", len(clip_paths), dest)
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg stitch failed (exit {proc.returncode}):\n{stderr.decode(errors='replace')}"
        )

    return dest
```

**Why captions go through a textfile:** escaping ffmpeg drawtext metacharacters by hand (`:`, `'`, `\`, `%`) is a footgun. `textfile=` sidesteps it.

### 6.16 `app/graph/music.py`

```python
"""Music bed: import a local track or generate one with MusicGen.

Both strategies produce an audio file matching the stitched video's duration,
then mux it onto the silent stitched video at the configured master gain.

The MusicGen path imports torch/transformers lazily so the base install
stays light; `uv add torch transformers scipy` when you flip music_mode="generate".
"""
from __future__ import annotations

import asyncio
import logging
import random
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)


async def _probe_duration(mp4: Path) -> float:
    args = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(mp4),
    ]
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {stderr.decode(errors='replace')}")
    return float(stdout.strip())


def _pick_track(niche: str | None, override: str | None) -> Path:
    music_root = settings.assets_dir / "music"
    if override:
        candidate = music_root / override
        if not candidate.exists():
            raise FileNotFoundError(f"music_track '{override}' not found at {candidate}")
        return candidate

    search_dirs = []
    if niche:
        search_dirs.append(music_root / niche)
    search_dirs.append(music_root)

    for d in search_dirs:
        if not d.exists():
            continue
        tracks = sorted(
            p for p in d.iterdir()
            if p.is_file() and p.suffix.lower() in {".mp3", ".m4a", ".wav", ".ogg", ".flac"}
        )
        if tracks:
            return random.choice(tracks)

    raise FileNotFoundError(
        f"No music tracks found under {music_root}. Drop a few royalty-free "
        f"tracks into assets/music/ (or assets/music/<niche>/)."
    )


async def _import_track(niche: str | None, override: str | None, duration: float, dest: Path) -> Path:
    """Loop+trim the imported track to `duration`, with fade in/out."""
    src = _pick_track(niche, override)
    fade = 1.5
    args = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-stream_loop", "-1", "-i", str(src),
        "-t", f"{duration:.3f}",
        "-af", f"afade=t=in:st=0:d={fade},afade=t=out:st={duration - fade:.3f}:d={fade}",
        "-c:a", "aac", "-b:a", "192k",
        str(dest),
    ]
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg music import failed: {stderr.decode(errors='replace')}")
    logger.info("imported music %s -> %s (%.1fs)", src.name, dest.name, duration)
    return dest


async def _generate_track(niche: str | None, duration: float, dest: Path) -> Path:
    """MusicGen via transformers. Lazy import to keep base install lean."""
    try:
        import torch  # noqa: F401
        from transformers import AutoProcessor, MusicgenForConditionalGeneration
    except ImportError as e:
        raise RuntimeError(
            "music_mode='generate' needs torch and transformers installed. "
            "Run `uv add torch transformers scipy` first."
        ) from e

    import scipy.io.wavfile

    prompt_for_niche = {
        "filipino-mythology": "dark ambient drone with sparse kulintang gongs, slow tempo",
        "cosmic-horror": "deep sub-bass drone with distant metallic stings, slow tempo",
    }
    prompt = prompt_for_niche.get(niche or "", "cinematic ambient drone, slow tempo, atmospheric")

    logger.info("generating music with MusicGen: %r", prompt)
    processor = AutoProcessor.from_pretrained("facebook/musicgen-small")
    model = MusicgenForConditionalGeneration.from_pretrained("facebook/musicgen-small")

    max_new_tokens = int(duration * 50) + 50
    inputs = processor(text=[prompt], padding=True, return_tensors="pt")
    audio = model.generate(**inputs, max_new_tokens=max_new_tokens)

    sampling_rate = model.config.audio_encoder.sampling_rate
    raw_wav = dest.with_suffix(".wav")
    scipy.io.wavfile.write(raw_wav, rate=sampling_rate, data=audio[0, 0].cpu().numpy())

    fade = 1.5
    args = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(raw_wav),
        "-t", f"{duration:.3f}",
        "-af", f"afade=t=in:st=0:d={fade},afade=t=out:st={duration - fade:.3f}:d={fade}",
        "-c:a", "aac", "-b:a", "192k",
        str(dest),
    ]
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await proc.communicate()
    raw_wav.unlink(missing_ok=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg generate-track post-process failed: {stderr.decode(errors='replace')}")
    return dest


async def add_music(
    stitched_mp4: Path,
    music_dest: Path,
    final_dest: Path,
    niche: str | None,
    music_track: str | None,
    mode: str,
) -> Path:
    """Build the music bed and mux it onto the silent stitched video."""
    duration = await _probe_duration(stitched_mp4)

    if mode == "import":
        await _import_track(niche, music_track, duration, music_dest)
    elif mode == "generate":
        await _generate_track(niche, duration, music_dest)
    else:
        raise ValueError(f"unknown music_mode: {mode!r}")

    final_dest.parent.mkdir(parents=True, exist_ok=True)
    args = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(stitched_mp4),
        "-i", str(music_dest),
        "-map", "0:v", "-map", "1:a",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        "-af", f"volume={settings.music_gain_db}dB",
        "-shortest",
        "-movflags", "+faststart",
        str(final_dest),
    ]
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg final mux failed: {stderr.decode(errors='replace')}")

    logger.info("final muxed -> %s", final_dest)
    return final_dest
```

### 6.17 `app/graph/graph.py`

LangGraph wiring. Five nodes, four edges. ~70 lines.

```python
"""LangGraph wiring.

Nodes:
    compose      -> Storyboard
    generate     -> per-scene clip MP4s via Playwright
    stitch       -> 9:16 + caption overlay -> stitched.mp4
    music        -> music bed muxed -> final.mp4

State is the JobState TypedDict from app.graph.state.
"""
from __future__ import annotations

import logging
from pathlib import Path

from langgraph.graph import END, StateGraph

from app.graph import composer as composer_mod
from app.graph import meta_ai
from app.graph import music as music_mod
from app.graph import stitcher as stitcher_mod
from app.graph.state import JobState
from app.storage import paths_for

logger = logging.getLogger(__name__)


async def node_compose(state: JobState) -> JobState:
    sb = await composer_mod.compose(
        idea=state["idea"],
        niche=state.get("niche"),
        num_scenes=state["num_scenes"],
        pov_caption_override=state.get("pov_caption_override"),
    )
    paths = paths_for(state["job_id"])
    paths.storyboard_json.write_text(sb.model_dump_json(indent=2), encoding="utf-8")
    return {"storyboard": sb}


async def node_generate(state: JobState) -> JobState:
    sb = state["storyboard"]
    paths = paths_for(state["job_id"])
    prompts = [sb.prompt_for_scene(i) for i in range(len(sb.scene_actions))]
    clip_paths = await meta_ai.generate_clips(prompts, paths.clip_path)
    return {"clip_paths": [str(p) for p in clip_paths]}


async def node_stitch(state: JobState) -> JobState:
    paths = paths_for(state["job_id"])
    sb = state["storyboard"]
    out = await stitcher_mod.stitch(
        [Path(p) for p in state["clip_paths"]],
        sb.pov_caption,
        paths.stitched,
    )
    return {"stitched_path": str(out)}


async def node_music(state: JobState) -> JobState:
    paths = paths_for(state["job_id"])
    final = await music_mod.add_music(
        stitched_mp4=paths.stitched,
        music_dest=paths.music_track,
        final_dest=paths.final,
        niche=state.get("niche"),
        music_track=state.get("music_track"),
        mode=state.get("music_mode", "import"),
    )
    return {"music_path": str(paths.music_track), "final_path": str(final)}


def build_graph():
    g = StateGraph(JobState)
    g.add_node("compose", node_compose)
    g.add_node("generate", node_generate)
    g.add_node("stitch", node_stitch)
    g.add_node("music", node_music)

    g.set_entry_point("compose")
    g.add_edge("compose", "generate")
    g.add_edge("generate", "stitch")
    g.add_edge("stitch", "music")
    g.add_edge("music", END)

    return g.compile()
```

### 6.18 `app/jobs/runner.py`

```python
"""Background job runner.

One coroutine per job — runs the LangGraph end-to-end, persists progress to
the ORM, and posts to the webhook (if any) on success/failure.
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
        async for event in graph.astream(initial_state, stream_mode="updates"):
            for node_name, patch in event.items():
                logger.info("[%s] node %s done", job_id, node_name)
                # Filter out non-JSON-serializable values (e.g. Storyboard
                # Pydantic instances). They live in storyboard.json on disk
                # already; sqlite is for progress reporting only.
                serializable = {
                    k: v for k, v in patch.items()
                    if isinstance(v, (str, int, float, bool, list))
                }
                await store.update_progress(
                    job_id,
                    stage=node_name,
                    state_patch=serializable,
                )

        result_url = f"{settings.public_base_url.rstrip('/')}/jobs/{job_id}/download"
        await store.update_progress(job_id, status="done", stage="done", result_url=result_url)
        if webhook_url:
            await _post_webhook(
                webhook_url,
                {"job_id": job_id, "status": "done", "result_url": result_url},
            )

    except Exception as e:
        logger.exception("[%s] job failed", job_id)
        await store.update_progress(job_id, status="error", error=str(e))
        if webhook_url:
            await _post_webhook(
                webhook_url,
                {"job_id": job_id, "status": "error", "error": str(e)},
            )


def spawn(job_id: str, initial_state: JobState, webhook_url: str | None) -> asyncio.Task:
    return asyncio.create_task(run_job(job_id, initial_state, webhook_url))
```

### 6.19 `app/api/__init__.py`

Empty.

### 6.20 `app/api/schemas.py`

```python
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.config import settings


class JobCreate(BaseModel):
    idea: str = Field(..., min_length=4)
    niche: str | None = None
    num_scenes: int = Field(default=8, ge=2, le=settings.max_scenes)
    pov_caption: str | None = None
    music_track: str | None = None
    music_mode: Literal["import", "generate"] = "import"
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
```

### 6.21 `app/api/routes.py`

```python
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
```

### 6.22 `app/api/main.py`

```python
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
```

### 6.23 `scripts/capture_session.py`

```python
"""One-time helper: open a headed Chromium, let the user log into meta.ai,
then save cookies + localStorage to settings.meta_storage_state so the
Playwright driver can reuse the session.

Usage:
    uv run python scripts/capture_session.py
"""
from __future__ import annotations

import asyncio

from playwright.async_api import async_playwright

from app.config import settings


async def main() -> None:
    print("Opening a headed Chromium. Log in to meta.ai in the window that appears.")
    print("When the chat input is visible, return to this terminal and press ENTER.")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
            ),
        )
        page = await context.new_page()
        await page.goto("https://www.meta.ai")

        # Block on user input from the terminal (run via asyncio.to_thread so we
        # don't freeze the event loop).
        await asyncio.to_thread(input, "Press ENTER once you're logged in... ")

        settings.meta_storage_state.parent.mkdir(parents=True, exist_ok=True)
        await context.storage_state(path=str(settings.meta_storage_state))
        print(f"Saved session to {settings.meta_storage_state}")
        await context.close()
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
```

---

## 7. Setup & verification

```powershell
# 1. Install deps
uv sync
uv run playwright install chromium

# 2. Env
copy .env.example .env
# Set NVIDIA_API_KEY (https://build.nvidia.com), pick COMPOSER_MODEL.

# 3. Capture a Meta session (one time; redo if storage_state.json expires)
uv run python scripts/capture_session.py
# A Chromium window opens. Log in to meta.ai. Press ENTER in the terminal
# once the chat input is visible. storage_state.json is written.

# 4. Drop royalty-free music tracks into assets/music/
#    (or assets/music/<niche>/ for per-niche selection)

# 5. Smoke-test the imports + graph compile + FastAPI startup
uv run python -c "from app.api.main import app; from app.graph.graph import build_graph; print(list(build_graph().get_graph().nodes))"
# expect: ['__start__', 'compose', 'generate', 'stitch', 'music', '__end__']

# 6. Run the API
uv run uvicorn app.api.main:app --reload

# 7. Trigger a job
curl -X POST http://localhost:8000/jobs -H "Content-Type: application/json" -d "{\"idea\":\"a Tikbalang lures a hunter deeper into the rainforest\",\"niche\":\"filipino-mythology\",\"num_scenes\":6}"

# 8. Poll
curl http://localhost:8000/jobs/<job_id>

# 9. Download when status == "done"
curl -OJ http://localhost:8000/jobs/<job_id>/download
```

### Per-stage verification

When something looks off, inspect artefacts in this order — the first one that's wrong is the layer to fix.

| File | Tells you |
|------|-----------|
| `outputs/{id}/storyboard.json` | What the LLM produced. Bad anchors → fix the composer prompt. |
| `outputs/{id}/clips/scene_NN.mp4` | Raw Meta outputs. Visual continuity check lives here. |
| `outputs/{id}/caption.txt` | Exactly what `drawtext` is rendering. |
| `outputs/{id}/stitched.mp4` | Composition without music — verify caption + 9:16 + concat. |
| `outputs/{id}/music.m4a` | The music bed alone. |
| `outputs/{id}/final.mp4` | The deliverable. |

---

## 8. Where to look first when X breaks

| Symptom | First place to look |
|---------|---------------------|
| LLM returns wrong number of scenes | Composer post-process truncates extras / errors on shortfall. Tighten the system prompt. |
| LLM returns malformed JSON | Switch to `method="json_mode"` in `composer.get_structured_llm()`. Try a different `COMPOSER_MODEL`. |
| `MetaSessionExpired` | Re-run `scripts/capture_session.py`. |
| `MetaUIChanged` | `META_SELECTORS` in `meta_ai.py`. Open meta.ai in DevTools, find new selectors. |
| Clips look unrelated despite the storyboard | Composer producing weak anchors. Tighten the system prompt. |
| ffmpeg fails on stitch | The error log has the full subprocess args + stderr. Copy-paste the args, run locally, iterate. |
| Caption renders wrong / not at all | `assets/fonts/Inter-Bold.ttf` AND `C:/Windows/Fonts/arialbd.ttf` both missing → `resolve_caption_font` raises. On non-Windows, override `CAPTION_FONT_FALLBACK`. |
| Caption text has weird characters | Captions go through `textfile=` so escaping isn't manual. Inspect `outputs/{id}/caption.txt`. |
| `music_mode="generate"` errors immediately | `torch`/`transformers`/`scipy` not installed. The error tells you what to `uv add`. |
| Job stuck in `running` after process restart | Known: no startup-recovery logic. Mark stuck rows manually or add a "claim queued jobs on startup" pass to `runner.py`. |
| Webhook never fires | `_post_webhook` logs warnings on failure but doesn't retry. |

---

## 9. Things to push back on / future work

If you're reviewing this design for soundness, these are the sharp corners worth asking about:

1. **Single-process job runner.** `runner.spawn` creates a bare `asyncio.Task`; if uvicorn restarts, in-flight jobs die. Acceptable for v1; called out for awareness.
2. **No graph-level retry node.** A Playwright failure on scene 7 of 8 wastes scenes 1–6. A future graph could split `generate` into per-scene subnodes with `add_conditional_edges` for retry/abort.
3. **Selectors are brittle.** Meta will break us. The selector dict is the right factoring, but there's no integration test against meta.ai itself (and there can't really be).
4. **No rate-limit / cost guardrails.** Nothing stops a webhook caller from queueing 100 jobs and hammering meta.ai. A simple per-day counter in the `Job` table would help.
5. **Captions don't yet support multi-line wrapping.** `drawtext` assumes the caption fits on one line at fontsize=64 on 1080-wide. Long captions overflow. Wrap at compose time, or compute fontsize based on width.
6. **MusicGen path is untested in CI.** If you ship `generate` to prod, add an end-to-end test that runs MusicGen on CPU.
7. **`config.py` mkdirs at import.** Side effects on import. Convenient locally; will surprise anyone using `app.config` from a script.
8. **`pov_caption_override` is applied AFTER the LLM runs.** The LLM doesn't know about the override when writing scene_actions. If you want the override to shape visuals, thread it into the prompt instead of stomping the output.

---

## 10. n8n / Power Automate trigger

**Polling pattern:**

1. Schedule trigger → HTTP Request `POST /jobs`.
2. Wait 60s → HTTP Request `GET /jobs/{id}`. Loop until `status == "done"`.
3. HTTP Request `GET /jobs/{id}/download` → Write file → YouTube/TikTok upload node.

**Webhook pattern:**

1. n8n Webhook node creates a public URL.
2. n8n HTTP Request `POST /jobs` with `webhook_url` = the n8n webhook.
3. FastAPI POSTs to that webhook on completion → second n8n flow downloads + uploads.

---

## 11. Niche & sound recommendation

**Top pick: Filipino mythology / Southeast Asian folklore** — Aswang, Manananggal, Tikbalang, Kapre, Diwata, Bakunawa, Engkanto, Bathala creation, Hinilawod, Biag ni Lam-ang.

Why it fits:
- Underserved on Shorts/Reels/TikTok vs. saturated Greek/Norse mythology.
- AI-video friendly: dreamy, atmospheric, supernatural visuals are exactly where current AI video models look strongest.
- Cultural authority on your end.
- Episodic formula: each creature / each epic chapter is a self-contained 30–40s short.

The POV caption format works particularly well: *"POV: You meet a Tikbalang in the forest"*, *"POV: The Bakunawa is eating the moon"*.

**Sound:** low ambient drone + sparse Filipino kulintang/agung gongs. Pixabay Music (CC0) and Free Music Archive have usable tracks. Master at -8 dB, 1.5s fade in/out, pick tracks with clean loop points.

---

That's the whole build. Type it, sync it, run it, and the rest is iteration on the prompt and the selectors.
