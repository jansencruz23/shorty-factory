"""MusicGen adapter via Hugging Face transformers.

Original audio, no Content ID risk. Slow on CPU (~1-3 min per track);
torch and transformers are imported lazily so app startup isn't paying
for them on every run.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

PROMPT_FOR_NICHE: dict[str, str] = {
    "filipino-mythology": "dark ambient drone with sparse kulintang gongs, slow tempo",
    "cosmic-horror": "deep sub-bass drone with distant metallic stings, slow tempo",
}
DEFAULT_PROMPT = "cinematic ambient drone, slow tempo, atmospheric"


class MusicGenMusicProvider:
    name = "musicgen"

    async def build_track(
        self,
        duration: float,
        dest: Path,
        *,
        niche: str | None = None,
        track_override: str | None = None,
    ) -> Path:
        # track_override is meaningless for the generative path — silently
        # ignored so per-job overrides via webhook don't break this provider.
        del track_override

        # Lazy-imported because torch is heavy (~2GB on disk, ~5s import).
        import torch  # noqa: F401
        from transformers import AutoProcessor, MusicgenForConditionalGeneration

        import scipy.io.wavfile

        prompt = PROMPT_FOR_NICHE.get(niche or "", DEFAULT_PROMPT)
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
