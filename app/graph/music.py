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

from langsmith import traceable

from app.config import settings

logger = logging.getLogger(__name__)


async def _probe_duration(mp4: Path) -> float:
    args = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
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
            p
            for p in d.iterdir()
            if p.is_file() and p.suffix.lower() in {".mp3", ".m4a", ".wav", ".ogg", ".flac"}
        )
        if tracks:
            return random.choice(tracks)

    raise FileNotFoundError(
        f"No music tracks found under {music_root}. Drop a few royalty-free "
        f"tracks into assets/music/ (or assets/music/<niche>/)."
    )


async def _import_track(
    niche: str | None, override: str | None, duration: float, dest: Path
) -> Path:
    """Loop+trim the imported track to `duration`, with fade in/out."""
    src = _pick_track(niche, override)
    fade = 1.5
    # `-stream_loop -1 -i src` loops the input forever; `-t duration` trims
    # the output to the exact length we need. Standard "loop a short bed
    # behind a longer video" pattern.
    args = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-stream_loop",
        "-1",
        "-i",
        str(src),
        "-t",
        f"{duration:.3f}",
        "-af",
        f"afade=t=in:st=0:d={fade},afade=t=out:st={duration - fade:.3f}:d={fade}",
        "-c:a",
        "libmp3lame",
        "-b:a",
        "192k",
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
    """MusicGen via transformers. Lazy-imported because torch is heavy
    (~2GB on disk, ~5s import) and we don't want to pay it on app startup."""
    import torch  # noqa: F401
    from transformers import AutoProcessor, MusicgenForConditionalGeneration

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
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(raw_wav),
        "-t",
        f"{duration:.3f}",
        "-af",
        f"afade=t=in:st=0:d={fade},afade=t=out:st={duration - fade:.3f}:d={fade}",
        "-c:a",
        "libmp3lame",
        "-b:a",
        "192k",
        str(dest),
    ]
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await proc.communicate()
    raw_wav.unlink(missing_ok=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg generate-track post-process failed: {stderr.decode(errors='replace')}"
        )
    return dest


@traceable(name="music.add_music", run_type="tool")
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
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(stitched_mp4),
        "-i",
        str(music_dest),
        "-map",
        "0:v",
        "-map",
        "1:a",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-af",
        f"volume={settings.music_gain_db}dB",
        "-shortest",
        "-movflags",
        "+faststart",
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
