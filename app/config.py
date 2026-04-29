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
