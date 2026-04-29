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
