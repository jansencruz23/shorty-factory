"""Settings loaded from .env: NVIDIA Build creds, paths, video dimensions, music gain.
Single source of truth — every magic number elsewhere is named here."""

import os
from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # NVIDIA Build is OpenAI-compatible
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
    caption_font_fallback: Path = Path(r"C:\Windows\Fonts\arialbd.ttf")
    caption_font_size: int = 64

    video_width: int = 1080
    video_height: int = 1920

    music_gain_db: float = -8.0

    # LangSmith observability — when langsmith_tracing is true and the API
    # key is set, langchain/langgraph auto-trace every LLM call and graph
    # node. Non-LLM steps (Playwright, ffmpeg, MusicGen) surface via the
    # @traceable decorators in app/graph/{meta_ai,stitcher,music}.py.
    langsmith_tracing: bool = False
    langsmith_api_key: str = ""
    langsmith_project: str = "shorty-factory"
    langsmith_endpoint: str = "https://api.smith.langchain.com"


settings = Settings()
settings.outputs_dir.mkdir(parents=True, exist_ok=True)
(settings.assets_dir / "music").mkdir(parents=True, exist_ok=True)
(settings.assets_dir / "fonts").mkdir(parents=True, exist_ok=True)

# Push LangSmith config to os.environ so langchain/langgraph (which read
# from environment, not from our Settings object) pick it up. Done once at
# import time, before any langchain client is constructed.
if settings.langsmith_tracing and settings.langsmith_api_key:
    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ["LANGSMITH_API_KEY"] = settings.langsmith_api_key
    os.environ["LANGSMITH_PROJECT"] = settings.langsmith_project
    os.environ["LANGSMITH_ENDPOINT"] = settings.langsmith_endpoint


def resolve_caption_font() -> Path:
    if settings.caption_font_path.exists():
        return settings.caption_font_path
    if settings.caption_font_fallback.exists():
        return settings.caption_font_fallback
    raise FileNotFoundError(
        f"No caption font found. Expected one of: {settings.caption_font_path}, "
        f"{settings.caption_font_fallback}"
    )
