"""Settings loaded from .env. Nested pydantic models flatten to env vars
via the `__` (double-underscore) delimiter, so:

    settings.llm.nvidia_api_key  ←→  LLM__NVIDIA_API_KEY
    settings.meta_ai.headless    ←→  META_AI__HEADLESS
    settings.langsmith.tracing   ←→  LANGSMITH__TRACING

Cross-cutting fields that don't belong to any provider/concern (paths,
public URL, max scenes) stay flat at the top level.
"""

import os
from pathlib import Path

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class LLMSettings(BaseModel):
    """Composer LLM credentials — NVIDIA Build (OpenAI-compatible)."""

    nvidia_api_key: str = ""
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    composer_model: str = "meta/llama-3.3-70b-instruct"


class VideoSettings(BaseModel):
    """Video output dimensions + which provider produces clips."""

    provider: str = "meta_ai"
    width: int = 1080
    height: int = 1920


class MetaAISettings(BaseModel):
    """Meta.ai-specific Playwright config. Only consulted when
    settings.video.provider == 'meta_ai'."""

    storage_state: Path = ROOT / "storage_state.json"
    headless: bool = False


class MusicSettings(BaseModel):
    """Default music provider + master gain (applied at mux time)."""

    # Default to "musicgen" so unattended runs don't depend on a populated
    # assets/music/ library. JobCreate's `music_mode` per-job override
    # still resolves through MUSIC_MODE_TO_PROVIDER.
    provider: str = "musicgen"
    gain_db: float = -8.0


class CaptionSettings(BaseModel):
    """POV caption font. The fallback exists so Linux containers without
    the bundled font still render something readable."""

    font_path: Path = ROOT / "assets" / "fonts" / "Inter-Bold.ttf"
    font_fallback: Path = Path(r"C:\Windows\Fonts\arialbd.ttf")
    font_size: int = 64


class LangSmithSettings(BaseModel):
    """LangSmith tracing. When `tracing` is true and `api_key` is set,
    langchain/langgraph auto-trace every LLM call and graph node, plus
    the @traceable spans from providers and pipeline."""

    tracing: bool = False
    api_key: str = ""
    project: str = "shorty-factory"
    endpoint: str = "https://api.smith.langchain.com"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_nested_delimiter="__",
        extra="ignore",
    )

    llm: LLMSettings = Field(default_factory=LLMSettings)
    video: VideoSettings = Field(default_factory=VideoSettings)
    meta_ai: MetaAISettings = Field(default_factory=MetaAISettings)
    music: MusicSettings = Field(default_factory=MusicSettings)
    caption: CaptionSettings = Field(default_factory=CaptionSettings)
    langsmith: LangSmithSettings = Field(default_factory=LangSmithSettings)

    # Cross-cutting flat fields — used everywhere, no natural namespace.
    outputs_dir: Path = ROOT / "outputs"
    assets_dir: Path = ROOT / "assets"
    jobs_db: Path = ROOT / "jobs.sqlite"
    public_base_url: str = "http://localhost:8000"
    max_scenes: int = Field(default=8, ge=2, le=12)


settings = Settings()
settings.outputs_dir.mkdir(parents=True, exist_ok=True)
(settings.assets_dir / "music").mkdir(parents=True, exist_ok=True)
(settings.assets_dir / "fonts").mkdir(parents=True, exist_ok=True)

# Push LangSmith config to os.environ so langchain/langgraph (which read
# from environment, not from our Settings object) pick it up. Done once at
# import time, before any langchain client is constructed.
if settings.langsmith.tracing and settings.langsmith.api_key:
    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ["LANGSMITH_API_KEY"] = settings.langsmith.api_key
    os.environ["LANGSMITH_PROJECT"] = settings.langsmith.project
    os.environ["LANGSMITH_ENDPOINT"] = settings.langsmith.endpoint


def resolve_caption_font() -> Path:
    if settings.caption.font_path.exists():
        return settings.caption.font_path
    if settings.caption.font_fallback.exists():
        return settings.caption.font_fallback
    raise FileNotFoundError(
        f"No caption font found. Expected one of: {settings.caption.font_path}, "
        f"{settings.caption.font_fallback}"
    )
