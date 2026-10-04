"""
Frame extraction and blur rejection (M1 `pipeline frames`).

ffmpeg does the decoding; the selection logic here is pure numpy so it can be
unit-tested without a video file or a GPU.
"""

from __future__ import annotations

import json
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


def scale_filter(long_edge: int) -> str:
    """
    ffmpeg scale expression shrinking the long edge to `long_edge`, whichever
    way round the phone was held, and never enlarging.

    Scaling while decoding matters: a 4K PNG is ~12 MB, so a 10-minute walk
    at 3 fps would write 20 GB of frames before blur rejection even starts.
    """
    return (
        f"scale='if(gte(iw,ih),min({long_edge},iw),-2)':'if(gte(iw,ih),-2,min({long_edge},ih))'"
        ":flags=area"
    )


def extract_frames(
    video: Path, out_dir: Path, fps: float = 3.0, long_edge: int = 1600
) -> list[Path]:
    """Decode the video to PNGs at `fps`. Returns the frame paths in order."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.glob("frame_*.png"):
        stale.unlink()
    cmd = [
        ffmpeg_path(),
        "-hide_banner",
        "-loglevel", "error",
        "-i", str(video),
        "-vf", f"fps={fps},{scale_filter(long_edge)}",
        str(out_dir / "frame_%05d.png"),
    ]
    subprocess.run(cmd, check=True)
    return sorted(out_dir.glob("frame_*.png"))


def extract_numbered(video: Path, out_dir: Path, step: int, long_edge: int = 1600) -> list[Path]:
    """
    Every `step`-th frame, named by its frame number in the video
    (frame_000042.png), so it lines up with per-frame poses and depth.

    No autorotation: posed captures store frames in the sensor's orientation
    and their intrinsics refer to that, whatever the container's rotation tag.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.glob("*.png"):
        stale.unlink()
    subprocess.run(
        [ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-noautorotate", "-i", str(video),
         "-vf", f"select=not(mod(n\\,{step})),{scale_filter(long_edge)}", "-fps_mode", "vfr",
         str(out_dir / "seq_%06d.png")],
        check=True,
    )
    numbered = []
    for i, path in enumerate(sorted(out_dir.glob("seq_*.png"))):
        target = out_dir / f"frame_{i * step:06d}.png"
        path.rename(target)
        numbered.append(target)
    return numbered


def sharpest_per_window(scores: dict[Path, float], window: int) -> list[Path]:
    """
    The sharpest frame of each run of `window` consecutive candidates.

    Keeps coverage even along the walk — one frame per stretch of it — while
    still dropping the blurred moments, which a global threshold would
    remove in clumps exactly where the person turned.
    """
    ordered = sorted(scores, key=lambda p: p.name)
    return [
        max(ordered[i : i + window], key=scores.__getitem__)
        for i in range(0, len(ordered), window)
    ]


#: Transfer functions that mean the clip is HDR (HLG or PQ). The pipeline
#: expects SDR Rec. 709 (docs/CAPTURE.md); HDR decodes washed out.
HDR_TRANSFERS = {"arib-std-b67", "smpte2084"}


def probe(video: Path) -> dict:
    exe = shutil.which("ffprobe")
    if not exe:
        raise FfmpegMissing("ffprobe is not on PATH (it ships with ffmpeg).")
    out = subprocess.run(
        [exe, "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height,avg_frame_rate,color_transfer,codec_name:format=duration",
         "-of", "json", str(video)],
        check=True, capture_output=True, text=True,
    ).stdout
    return json.loads(out)


def video_warnings(info: dict) -> list[str]:
    """What in a probed clip contradicts docs/CAPTURE.md, in plain words."""
    streams = info.get("streams") or []
    if not streams:
        return ["No video stream found in this file."]
    s = streams[0]
    warnings = []
    if s.get("color_transfer") in HDR_TRANSFERS:
        warnings.append(
            "This clip is HDR. Re-record in SDR Rec. 709 (docs/CAPTURE.md): HDR frames "
            "decode washed out and the reconstruction inherits it."
        )
    long_side = max(int(s.get("width") or 0), int(s.get("height") or 0))
    if long_side < 3000:
        warnings.append(f"Only {long_side} px on the long side; the guide asks for 4K.")
    rate = _rate(s)
    if 0 < rate < 50:
        warnings.append(f"{rate:.0f} fps; the guide asks for 60 so blur rejection has choice.")
    duration = float((info.get("format") or {}).get("duration") or 0)
    if 0 < duration < 40:
        warnings.append(f"Only {duration:.0f} s of footage; a room needs roughly 60-90 s.")
    return warnings


def _rate(stream: dict) -> float:
    num, _, den = str(stream.get("avg_frame_rate", "0/1")).partition("/")
    return float(num) / float(den) if float(den or 0) else 0.0


def frame_rate(video: Path) -> float:
    """Average frames per second, or 0.0 if the file reports none."""
    streams = probe(video).get("streams") or []
    return _rate(streams[0]) if streams else 0.0


def check_video(video: Path) -> list[str]:
    try:
        return video_warnings(probe(video))
    except (FfmpegMissing, subprocess.CalledProcessError) as exc:
        return [f"Could not inspect the video: {exc}"]


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
