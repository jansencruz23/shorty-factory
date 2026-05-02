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

# MusicGen-small has a positional-embedding cap around 1503; going past it
# triggers `IndexError: index out of range in self` mid-generation. ~30s of
# audio at 50Hz tokens. For longer videos we generate this much and loop
# the output to match the target duration.
MAX_MUSICGEN_TOKENS = 1500

MODEL_NAME = "facebook/musicgen-small"

# Module-level cache: load processor + model once per uvicorn process.
# Re-running from_pretrained() on every job re-validates HF cache via
# HEAD requests and re-deserializes weights — wasteful when the data is
# already in RAM. Held until the worker exits.
_processor = None
_model = None


def _load_model_sync():
    """Synchronous load — call from asyncio.to_thread so it doesn't block
    the event loop during the ~5-10s first-time deserialization."""
    global _processor, _model
    if _processor is not None and _model is not None:
        return
    import torch  # noqa: F401
    from transformers import AutoProcessor, MusicgenForConditionalGeneration

    logger.info("loading MusicGen weights into RAM (one-time per process)")
    _processor = AutoProcessor.from_pretrained(MODEL_NAME)
    _model = MusicgenForConditionalGeneration.from_pretrained(MODEL_NAME)


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

        import scipy.io.wavfile

        prompt = PROMPT_FOR_NICHE.get(niche or "", DEFAULT_PROMPT)
        logger.info("generating music with MusicGen: %r", prompt)

        # Load once and cache; subsequent jobs skip all HF HEAD checks
        # and weight deserialization.
        await asyncio.to_thread(_load_model_sync)
        processor, model = _processor, _model

        max_new_tokens = min(int(duration * 50) + 50, MAX_MUSICGEN_TOKENS)
        inputs = processor(text=[prompt], padding=True, return_tensors="pt")
        # Generation is CPU-bound and blocks the event loop; offload to a
        # worker thread so the API can still respond to /healthz polls etc.
        audio = await asyncio.to_thread(
            lambda: model.generate(**inputs, max_new_tokens=max_new_tokens)
        )

        sampling_rate = model.config.audio_encoder.sampling_rate
        raw_wav = dest.with_suffix(".wav")
        scipy.io.wavfile.write(raw_wav, rate=sampling_rate, data=audio[0, 0].cpu().numpy())

        fade = 1.5
        # Loop the generated audio to match the video duration. When the
        # video is short (≤30s) the loop is a no-op since MusicGen already
        # produced enough; for longer videos we tile the bed seamlessly.
        args = [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-stream_loop",
            "-1",
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
