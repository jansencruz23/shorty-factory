"""Music provider port + factory.

Resolves a provider name (from settings or per-job override) to a concrete
adapter implementing the MusicProvider Protocol. Adapter modules are
imported lazily so torch/transformers (musicgen) don't load unless picked.
"""

from __future__ import annotations

from app.providers.music.base import MusicProvider

# JobCreate's music_mode ("import" | "generate") is preserved as the public
# input for backward compatibility. Internally we map to provider names.
MUSIC_MODE_TO_PROVIDER: dict[str, str] = {
    "import": "local",
    "generate": "musicgen",
}

__all__ = ["MusicProvider", "MUSIC_MODE_TO_PROVIDER", "get_music_provider"]


def get_music_provider(name: str) -> MusicProvider:
    if name == "local":
        from app.providers.music.local import LocalLibraryMusicProvider

        return LocalLibraryMusicProvider()
    if name == "musicgen":
        from app.providers.music.musicgen import MusicGenMusicProvider

        return MusicGenMusicProvider()
    raise ValueError(f"unknown music provider: {name!r}")
