"""
Frame extraction and blur rejection (M1 `pipeline frames`).

ffmpeg does the decoding; the selection logic here is pure numpy so it can be
unit-tested without a video file or a GPU.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np


class FfmpegMissing(RuntimeError):
    pass


def ffmpeg_path() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise FfmpegMissing(
            "ffmpeg is not on PATH. Install it (apt install ffmpeg / winget install "
            "Gyan.FFmpeg) — see docs/PIPELINE.md."
        )
    return exe


def extract_frames(video: Path, out_dir: Path, fps: float = 3.0) -> list[Path]:
    """Decode the video to PNGs at `fps`. Returns the frame paths in order."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.glob("frame_*.png"):
        stale.unlink()
    cmd = [
        ffmpeg_path(),
        "-hide_banner",
        "-loglevel", "error",
        "-i", str(video),
        "-vf", f"fps={fps}",
        "-q:v", "2",
        str(out_dir / "frame_%05d.png"),
    ]
    subprocess.run(cmd, check=True)
    return sorted(out_dir.glob("frame_*.png"))


def variance_of_laplacian(image: np.ndarray) -> float:
    """
    Sharpness score. Low values mean motion blur or defocus.

    cv2.Laplacian takes the uint8 array directly and produces float output,
    so converting first was a pure copy — 17 ms and 66 MB per 4K frame.
    float32 is ample for ranking frames against each other.
    """
    grey = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    return float(cv2.Laplacian(grey, cv2.CV_32F).var())


@dataclass
class FrameSelection:
    keep: list[Path]
    dropped_blurry: list[Path]
    dropped_to_target: list[Path]
    threshold: float

    @property
    def summary(self) -> str:
        return (
            f"{len(self.keep)} kept, {len(self.dropped_blurry)} dropped as blurry "
            f"(threshold {self.threshold:.1f}), "
            f"{len(self.dropped_to_target)} trimmed to hit the frame target"
        )


def select_frames(
    scores: dict[Path, float],
    min_frames: int = 150,
    max_frames: int = 600,
    relative_threshold: float = 0.4,
) -> FrameSelection:
    """
    Choose which frames to keep.

    The blur threshold is relative to the median sharpness of this capture,
    because absolute Laplacian variance depends heavily on scene texture —
    a plain white wall scores low even when perfectly sharp.

    If dropping blurry frames would leave fewer than `min_frames`, the
    threshold is relaxed rather than returning too few frames to register.
    """
    if not scores:
        return FrameSelection([], [], [], 0.0)

    ordered = sorted(scores.items(), key=lambda kv: kv[0].name)
    values = np.array([v for _, v in ordered], dtype=np.float64)
    median = float(np.median(values))
    threshold = median * relative_threshold

    keep = [p for p, v in ordered if v >= threshold]
    dropped_blurry = [p for p, v in ordered if v < threshold]

    # Relax rather than starve the reconstruction.
    if len(keep) < min_frames and len(ordered) >= min_frames:
        by_sharpness = sorted(ordered, key=lambda kv: -kv[1])[:min_frames]
        keep_set = {p for p, _ in by_sharpness}
        keep = [p for p, _ in ordered if p in keep_set]
        dropped_blurry = [p for p, _ in ordered if p not in keep_set]
        threshold = min(v for _, v in by_sharpness)

    # Thin evenly if we still have too many; even spacing preserves coverage
    # of the whole walk, which matters more than keeping the sharpest frames.
    dropped_to_target: list[Path] = []
    if len(keep) > max_frames:
        idx = np.linspace(0, len(keep) - 1, max_frames).round().astype(int)
        chosen = {keep[i] for i in idx}
        dropped_to_target = [p for p in keep if p not in chosen]
        keep = [p for p in keep if p in chosen]

    return FrameSelection(keep, dropped_blurry, dropped_to_target, threshold)


def score_directory(frame_dir: Path) -> dict[Path, float]:
    scores: dict[Path, float] = {}
    for path in sorted(frame_dir.glob("frame_*.png")):
        image = cv2.imread(str(path))
        if image is None:
            continue
        scores[path] = variance_of_laplacian(image)
    return scores


def downsample(image: np.ndarray, long_edge: int) -> np.ndarray:
    """
    Shrink so the long edge is `long_edge` px.

    docs/SPEC.md §2 caps training resolution at ~1600 px because the target
    GPU has 8 GB of VRAM.
    """
    h, w = image.shape[:2]
    longest = max(h, w)
    if longest <= long_edge:
        return image
    scale = long_edge / longest
    return cv2.resize(image, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA)
