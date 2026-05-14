# shorty-factory

Compose 5-8 connected Meta AI video clips into a vertical short, served via FastAPI and triggerable from n8n / Power Automate / curl. The pipeline is hexagonal: video and music providers are pluggable behind Protocols, so swapping `meta.ai` for Runway/Pika/Luma later is a single-file change.

Two video formats are supported: **narrative** (one connected story arc with a twist, default) and **top5** (countdown of 5 ranked moments around a theme). One API server handles both — different n8n workflows can drive different YouTube channels with their own niches and cadences. See [Modes](#modes) below.

## What it does

1. **compose** — LLM (NVIDIA Build, OpenAI-compatible) builds a `Storyboard` (narrative) or `TopFiveStoryboard` (top-5) from the idea + niche. Both implement `BaseStoryboard`, so downstream nodes don't branch on mode.
2. **generate** — the configured `VideoProvider` adapter renders one clip per scene. Default adapter: `MetaAIVideoProvider` driving meta.ai via Playwright.
3. **stitch** — ffmpeg normalises clips to 1080×1920 with a blurred-fill background and burns in the captions described by `storyboard.build_caption_plan()`. Narrative mode: persistent POV caption. Top-5 mode: title at top + per-clip rank caption that switches at clip boundaries.
4. **music** — the configured `MusicProvider` builds an audio bed of matching duration. Default: MusicGen (no Content ID risk). Alternative: pick a track from `assets/music/<niche>/`.
5. **mux** — ffmpeg muxes audio onto video at the master gain → `final.mp4`.

FastAPI exposes the job lifecycle for n8n to trigger and poll, with webhook callbacks on completion.

## Data flow

```
┌──────────────────────────────────────────────────────────────────────────┐
│                     ENTRY POINTS  (n8n workflow)                         │
│                                                                          │
│  ┌──────────────┐   ┌──────────────┐   ┌───────────────────────────┐     │
│  │ Schedule     │   │ Telegram     │   │ Webhook /short            │     │
│  │ 09:00 daily  │   │ /short <idea>│   │ POST {idea?, niche?, ...} │     │
│  └──────┬───────┘   └──────┬───────┘   └────────────┬──────────────┘     │
│         │                  │                        │                    │
│         │ checks /healthz  │ skips guard            │ skips guard        │
│         │ ran_today=false  │                        │                    │
│         └──────────────────┼────────────────────────┘                    │
│                            ▼                                             │
│              ┌─────────────────────────────┐                             │
│              │ Set: JobCreate body         │                             │
│              │   idea (or LLM-generated)   │                             │
│              │   niche, num_scenes         │                             │
│              │   webhook_url ←─ unique     │                             │
│              └──────────────┬──────────────┘                             │
│                             │ POST /jobs                                 │
└─────────────────────────────┼────────────────────────────────────────────┘
                              ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                        SHORTY-FACTORY (FastAPI)                          │
│                                                                          │
│   routes.create_job ── single-flight (429 if active) ── store row        │
│                                  │                                       │
│                                  ▼                                       │
│   runner.run_job:                                                        │
│      progress = partial(store.update_progress, job_id)   ← bakes job_id  │
│      graph = build_graph(progress=progress)                              │
│                                  │                                       │
│      ┌───────────────────────────┴───────────────────────────┐           │
│      ▼                                                       ▼           │
│   ┌──────────┐ → ┌────────────┐ → ┌──────────┐ → ┌────────────────┐      │
│   │ compose  │   │  generate  │   │  stitch  │   │     music      │      │
│   └─────┬────┘   └──────┬─────┘   └────┬─────┘   └────────┬───────┘      │
│         │               │              │                  │              │
│         │   ┌───────────┴──────────┐   │                  │              │
│         │   ▼                      ▼   │                  ▼              │
│         │  port: VideoProvider         │     port: MusicProvider         │
│         │  adapter: MetaAIVideoProvider│     adapter: MusicGen / Local   │
│         │  (Playwright + meta.ai)      │                                 │
│         │                              │                                 │
│         ▼  every node calls progress(stage=..., scene=...)               │
│        ───►  sink → store.update_progress → SQLite                       │
│                                                                          │
│         outputs/{job_id}/{storyboard.json, clips/, stitched.mp4,         │
│                          music.mp3, final.mp4}                           │
│                                                                          │
│   on completion (or error):                                              │
│      POST {webhook_url} {job_id, status, result_url|error,error_type}    │
└────────────────────────────┬─────────────────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                          BACK TO n8n                                     │
│                                                                          │
│   Wait node resumes  ─►  IF status == "done"                             │
│                              │                                           │
│         ┌────────────────────┴────────────────────┐                      │
│         ▼ true                                    ▼ false                │
│   GET /jobs/{id}/download                  Telegram: error notify        │
│         │                                  (uses error_type for routing) │
│         ▼                                                                │
│   YouTube ─ Upload Video                                                 │
│         │                                                                │
│         ▼                                                                │
│   Telegram: success                                                      │
└──────────────────────────────────────────────────────────────────────────┘
```

## Architecture (hexagonal, lite variant)

The graph nodes are the **core** — they decide *what* should happen (compose, generate, stitch, music). The technology choices behind each step (which video API, which music model, which database) are **adapters** plugged in through `typing.Protocol` **ports**. Swapping technology doesn't touch the core.

| Port | File | Concrete adapters |
|------|------|-------------------|
| `VideoProvider` | [app/providers/video/base.py](app/providers/video/base.py) | `MetaAIVideoProvider` (Playwright). Future: Runway, Pika, Luma — each is a new file in [app/providers/video/](app/providers/video/) |
| `MusicProvider` | [app/providers/music/base.py](app/providers/music/base.py) | `MusicGenMusicProvider` (default), `LocalLibraryMusicProvider` (`assets/music/<niche>/`) |
| `ProgressSink` | [app/jobs/events.py](app/jobs/events.py) | `partial(store.update_progress, job_id)` in production; record-calls fake in tests |
| `BaseStoryboard` | [app/graph/storyboards/base.py](app/graph/storyboards/base.py) | `Storyboard` (narrative), `TopFiveStoryboard` (top-5) — both expose `num_scenes()`, `prompt_for_scene(i)`, `build_caption_plan()` |

Adding a new video provider: write `app/providers/video/runway.py` with a `RunwayVideoProvider` class implementing the Protocol, then add one `if name == "runway"` line to the factory in [app/providers/video/\_\_init\_\_.py](app/providers/video/__init__.py). No graph code changes.

Adding a new format (e.g. "explainer" mode): write `app/graph/storyboards/explainer.py` with a class implementing `BaseStoryboard`, and `app/graph/composers/explainer.py` with a `compose_explainer()` function, then add one `if mode == "explainer"` branch to the dispatcher in [app/graph/composer.py](app/graph/composer.py). The graph nodes don't change — they only call protocol methods.

## Modes

The `mode` field on the `POST /jobs` body picks the storyboard format:

| Mode | Composer | Storyboard | Caption layout | Typical use |
|------|----------|------------|----------------|-------------|
| `narrative` (default) | [composers/narrative.py](app/graph/composers/narrative.py) | `Storyboard` | One persistent POV caption | Connected story arc with a twist (4-8 clips) |
| `top5` | [composers/top5.py](app/graph/composers/top5.py) | `TopFiveStoryboard` | Title pinned at top + per-clip rank caption (#5 → #1) | Countdown of 5 self-contained ranked moments |

When `mode: "top5"`, `num_scenes` is silently clamped to 5 (the format demands it). The storyboard composer accepts any `niche` string — known niches (`filipino-mythology`, `cute`, `wins`, `fails`, `satisfying`, `funny`, `mind_blowing`, etc.) get tuned MusicGen prompts; unknown ones fall back to a neutral cinematic underscore (logged as a warning).

Two-channel deployment shape: run one API server, drive it from two n8n workflows — one per channel — each with its own Schedule, niche list, idea-generation prompt, YouTube OAuth credential, and `mode` field. Both workflows go through the same single-flight queue (one Meta AI session can't safely serve parallel jobs). Walkthrough in [N8N_SETUP.md](N8N_SETUP.md).

## Risks worth knowing

- **Meta AI has no public video API.** UI automation likely violates Meta's ToS — use a dedicated account, not your personal one.
- **Selectors will drift.** Edit `META_SELECTORS` in [app/providers/video/meta_ai.py](app/providers/video/meta_ai.py) when meta.ai redesigns.
- **Session re-capture every 2-4 weeks.** When meta.ai invalidates the cookie, jobs fail with `error_type: session_expired`. Re-run `scripts/capture_session.py` from a Windows host where Chromium can pop up.
- **First MusicGen run downloads ~1.5GB** of model weights from HuggingFace. Subsequent runs use the cache (`~/.cache/huggingface/`).
- **ffmpeg must be on `PATH`** (tested with 8.1 on Windows).
- **`uvicorn --reload` breaks Playwright on Windows** (selector event loop, no subprocess transport). [main.py](main.py) automatically disables reload on Windows.

## Setup

```powershell
# 1. Install Python deps + Playwright browser
uv sync
uv run playwright install chromium

# 2. Copy env template and fill it in
copy .env.example .env
# Set LLM__NVIDIA_API_KEY (https://build.nvidia.com)

# 3. Capture a Meta session (one time; redo when storage_state.json expires)
uv run python scripts/capture_session.py
# A Chromium window opens. Log in to meta.ai. Press ENTER in the terminal
# once the chat input is visible. storage_state.json is written.

# 4. Run the API
uv run python main.py
```

Optional: drop royalty-free tracks into `assets/music/` (or `assets/music/<niche>/`) if you want to fall back to imported music for specific jobs (`music_mode: "import"` per-job override).

### Env vars

Settings are nested pydantic models that flatten to env vars via the `__` (double-underscore) delimiter. See [.env.example](.env.example) for the full template. Common ones:

```
LLM__NVIDIA_API_KEY=nvapi-...
LLM__COMPOSER_MODEL=meta/llama-3.3-70b-instruct
VIDEO__PROVIDER=meta_ai
META_AI__STORAGE_STATE=storage_state.json
META_AI__HEADLESS=false
MUSIC__PROVIDER=musicgen
LANGSMITH__TRACING=false
LANGSMITH__API_KEY=lsv2_pt_...
```

## Endpoints

```
POST /jobs                       enqueue a job (returns 429 if active job exists)
GET  /jobs/{job_id}              status + result_url
GET  /jobs/{job_id}/download     stream final.mp4
GET  /healthz                    readiness probe (n8n consumes this)
GET  /health                     liveness (just {"status":"ok"})
```

`POST /jobs` body (narrative — default):

```json
{
  "idea": "a Tikbalang lures a hunter deeper into the rainforest",
  "niche": "filipino-mythology",
  "num_scenes": 6,
  "mode": "narrative",
  "pov_caption": null,
  "music_track": null,
  "music_mode": "generate",
  "webhook_url": null
}
```

`POST /jobs` body (top-5):

```json
{
  "idea": "top 5 most unsettling Tikbalang encounters",
  "niche": "filipino-mythology",
  "mode": "top5",
  "music_mode": "generate",
  "webhook_url": null
}
```

- `mode` defaults to `"narrative"`. Pass `"top5"` for ranking format. `num_scenes` is ignored when `mode` is `"top5"` (always 5).
- `pov_caption` is optional — if null, the LLM writes one. Only consumed by narrative mode (top-5 uses `main_title` from the LLM instead).
- `music_mode` defaults to `"generate"` (MusicGen). Set to `"import"` to use a track from `assets/music/`. `music_track` is only consulted by `"import"`.
- `webhook_url` is POSTed at terminal status with `{job_id, status, result_url|error, error_type, youtube_title, youtube_description, youtube_tags}`.

`GET /healthz` response shape:

```json
{
  "ready": true,
  "last_success_at": "2026-04-30T08:15:55Z",
  "ran_today": false,
  "has_active_job": false,
  "storage_state_present": true
}
```

n8n's daily cron consults `ran_today` to skip re-runs after a manual trigger fired earlier the same day.

### Error types in webhook payloads

When a job fails, the webhook payload includes `error_type` so callers can route on cause:

| `error_type` | Meaning | Operator action |
|--------------|---------|-----------------|
| `session_expired` | meta.ai cookie invalid | Re-run `scripts/capture_session.py` |
| `rate_limited` | Provider throttled | Wait it out; retry later |
| `ui_changed` | Selector didn't match | Edit `META_SELECTORS` |
| `quota_exceeded` | Daily/monthly cap hit | Wait for the quota window |
| `pipeline` | ffmpeg / IO failure | Inspect logs |
| `config` | Missing asset / font | Fix env or assets/ |
| `orphaned` | uvicorn restarted mid-job | None — cosmetic |
| `unknown` | Anything else | Investigate |

## n8n trigger

Three triggers (Schedule cron, Telegram bot, generic Webhook) converge on the same `POST /jobs → wait for webhook → YouTube upload` pipeline. Full setup walkthrough in [N8N_SETUP.md](N8N_SETUP.md).

Quick curl-based trigger from any external system:

```cmd
curl -X POST http://localhost:5678/webhook/short ^
  -H "X-Trigger-Token: your-shared-secret" ^
  -H "Content-Type: application/json" ^
  -d "{\"idea\":\"a Tikbalang lures a hunter\",\"niche\":\"filipino-mythology\",\"num_scenes\":4}"
```

## Layout

```
app/
  api/             FastAPI HTTP adapter
    main.py          lifespan: init_db → orphan reconciliation → outputs cleanup
    routes.py        POST /jobs, GET /jobs/:id, /download, /healthz
    schemas.py       JobCreate / JobStatus pydantic models
  graph/           LangGraph wiring (the "core" of the hexagon)
    graph.py         build_graph(progress=ProgressSink) — node factories
    composer.py      compose() dispatcher — picks per-mode composer by `mode`
    composers/      one file per mode
      narrative.py   compose_narrative() + system prompt (story arc + twist)
      top5.py        compose_top5() + system prompt (countdown, domain-agnostic)
    storyboards/    one file per mode
      base.py        BaseStoryboard protocol + CaptionPlan dataclass
      narrative.py   Storyboard pydantic model
      top5.py        TopFiveStoryboard + TopFiveItem pydantic models
    state.py         JobState TypedDict (graph state, no schema definitions)
  jobs/            persistence + runner adapter
    runner.py        run_job → builds sink → drives the graph → posts webhook
    store.py         SQLite CRUD over the Job table
    models.py        Job SQLModel
    events.py        ProgressSink Protocol
  pipeline/        ffmpeg steps (not "providers")
    stitch.py        9:16 normalize + caption overlay
    mux.py           combine video + music bed
  providers/       swappable adapters behind Protocols
    video/
      base.py        VideoProvider Protocol
      meta_ai.py     Playwright adapter
      __init__.py    get_video_provider factory
    music/
      base.py        MusicProvider Protocol
      musicgen.py    MusicGen adapter (default)
      local.py       LocalLibraryMusicProvider adapter
      __init__.py    get_music_provider factory
  exceptions.py    typed error tree (ProviderSessionExpired, ...) + classify_error
  config.py        nested pydantic Settings (env_nested_delimiter="__")
  db.py            async SQLModel engine + session factory
  storage.py       per-job filesystem layout (paths_for(job_id))
assets/
  music/           bg tracks (organize by niche subdir if you like) — optional
  fonts/           drop Inter-Bold.ttf here, or rely on Windows arialbd.ttf fallback
scripts/
  capture_session.py   one-time meta.ai login → storage_state.json
outputs/             per-job working dirs + final.mp4 (auto-pruned after 7 days)
N8N_SETUP.md         full n8n workflow walkthrough
main.py              uvicorn entry point (auto-disables --reload on Windows)
```

## Verification

Three smoke tests, ordered by cost:

1. **Composer (no Playwright)**:
   ```cmd
   :: Narrative mode
   uv run python -c "import asyncio; from app.graph.composer import compose; print(asyncio.run(compose(idea='a Tikbalang lures a hunter', niche='filipino-mythology', num_scenes=4, mode='narrative')).model_dump_json(indent=2))"

   :: Top-5 mode
   uv run python -c "import asyncio; from app.graph.composer import compose; print(asyncio.run(compose(idea='top 5 most unsettling tikbalang encounters', niche='filipino-mythology', num_scenes=5, mode='top5')).model_dump_json(indent=2))"
   ```
2. **Single video clip** (uses Playwright + meta.ai, ~2 min):
   ```cmd
   uv run python -c "import asyncio; from pathlib import Path; from app.providers.video import get_video_provider; p = get_video_provider('meta_ai'); asyncio.run(p.generate_clips(['Create a 5-second cinematic video. A lone astronaut walking on Mars at sunset.'], lambda i: Path(f'outputs/test_{i}.mp4')))"
   ```
3. **End-to-end via API** (~10 min for `num_scenes=2` + first MusicGen run):
   ```cmd
   curl -X POST http://localhost:8000/jobs -H "Content-Type: application/json" -d "{\"idea\":\"a Tikbalang lures a hunter\",\"niche\":\"filipino-mythology\",\"num_scenes\":2}"
   curl http://localhost:8000/jobs/<job_id>          # poll until status=done
   curl -o final.mp4 http://localhost:8000/jobs/<job_id>/download
   ```
