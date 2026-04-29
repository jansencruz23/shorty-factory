# shorty-factory

Compose 6–8 connected Meta AI video clips into a vertical short. Triggerable from n8n / Power Automate via a small FastAPI surface.

## What it does

1. LLM (NVIDIA Build, OpenAI-compatible) splits an idea into N visually-connected ~5s scene prompts plus one POV caption.
2. Playwright drives meta.ai, generating one clip per scene and downloading the MP4.
3. ffmpeg stitches them to 1080×1920 with a blurred-fill background and a persistent on-screen caption (e.g. *POV: You are an astronaut*).
4. ffmpeg muxes a music bed — imported from `assets/music/` or generated with MusicGen.
5. FastAPI exposes the job lifecycle for n8n to poll or webhook.

## Risks worth knowing

- Meta AI has no public video API. UI automation likely violates Meta's ToS — use a dedicated account, not your personal one. Selectors will break when Meta updates the UI; edit `META_SELECTORS` in `app/graph/meta_ai.py`.
- ffmpeg must be on `PATH` (we tested with 8.1 on Windows).

## Setup

```powershell
# 1. Install Python deps + Playwright browser
uv sync
uv run playwright install chromium

# 2. Copy env template and fill it in
copy .env.example .env
# Set NVIDIA_API_KEY (https://build.nvidia.com), pick COMPOSER_MODEL.

# 3. Capture a Meta session (one time; redo when storage_state.json expires)
uv run python scripts/capture_session.py
# A Chromium window opens. Log in to meta.ai. Press ENTER in the terminal
# once the chat input is visible. storage_state.json is written.

# 4. Drop a few royalty-free music tracks into assets/music/
#    (or assets/music/<niche>/ for per-niche selection)

# 5. Run the API
uv run uvicorn app.api.main:app --reload
```

## Endpoints

```
POST /jobs                       enqueue a job
GET  /jobs/{job_id}              status + result_url
GET  /jobs/{job_id}/download     stream final.mp4
GET  /health
```

`POST /jobs` body:

```json
{
  "idea": "a Tikbalang lures a hunter deeper into the rainforest",
  "niche": "filipino-mythology",
  "num_scenes": 6,
  "pov_caption": null,
  "music_track": null,
  "music_mode": "import",
  "webhook_url": null
}
```

`pov_caption` is optional — if null, the LLM writes one. `music_mode: "generate"` needs `torch` + `transformers` installed (`uv add torch transformers scipy`).

## n8n trigger

Polling pattern:

1. Schedule trigger → HTTP Request `POST /jobs`.
2. Wait 60s → HTTP Request `GET /jobs/{id}`. Loop until `status == "done"`.
3. HTTP Request `GET /jobs/{id}/download` → Write file → YouTube/TikTok upload node.

Or pass `webhook_url` so the API calls back when done.

## Layout

```
app/
  api/        FastAPI surface
  graph/      LangGraph nodes (composer, meta_ai, stitcher, music)
  jobs/       sqlite store + background runner
  config.py
  storage.py
assets/
  music/      bg tracks (organize by niche subdir if you like)
  fonts/      drop Inter-Bold.ttf here, or rely on Windows arialbd.ttf fallback
scripts/
  capture_session.py
outputs/      per-job working dirs + final.mp4
```

## Verification

1. Composer: `uv run python -c "import asyncio; from app.graph.composer import compose; print(asyncio.run(compose('a Tikbalang lures a hunter', 'filipino-mythology', 6)).model_dump_json(indent=2))"`
2. Single clip: drive `meta_ai.generate_clips([prompt], …)` with one prompt.
3. End-to-end: `curl -X POST localhost:8000/jobs -H "Content-Type: application/json" -d '{"idea":"...","num_scenes":6}'`, then poll, then download.
