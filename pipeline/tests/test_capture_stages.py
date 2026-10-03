"""The capture-side stages that need no GPU: video checks and trainer wiring."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from walkthrough_pipeline import train as train_mod
from walkthrough_pipeline.cli import local_path
from walkthrough_pipeline.frames import extract_frames, scale_filter, video_warnings


def _probe(width=3840, height=2160, rate="60/1", transfer="bt709", duration=75.0):
    return {
        "streams": [
            {"width": width, "height": height, "avg_frame_rate": rate,
             "color_transfer": transfer, "codec_name": "hevc"}
        ],
        "format": {"duration": str(duration)},
    }


def test_a_clip_shot_to_the_guide_raises_no_warnings():
    assert video_warnings(_probe()) == []
    assert video_warnings(_probe(width=2160, height=3840)) == []  # held upright


@pytest.mark.parametrize(
    ("kwargs", "needle"),
    [
        ({"transfer": "arib-std-b67"}, "HDR"),
        ({"transfer": "smpte2084"}, "HDR"),
        ({"width": 1920, "height": 1080}, "4K"),
        ({"rate": "30000/1001"}, "60"),
        ({"duration": 20}, "60-90 s"),
    ],
)
def test_each_departure_from_the_capture_guide_is_named(kwargs, needle):
    (warning,) = video_warnings(_probe(**kwargs))
    assert needle in warning


def test_a_file_without_video_is_reported():
    assert video_warnings({"streams": []}) == ["No video stream found in this file."]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed here")
@pytest.mark.parametrize(("size", "expected"), [("640x360", (320, 180)), ("360x640", (180, 320))])
def test_frames_are_scaled_to_the_long_edge_either_way_round(tmp_path, size, expected):
    import cv2

    clip = tmp_path / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc=size={size}:rate=6",
         "-t", "1", str(clip)],
        check=True,
    )
    frames = extract_frames(clip, tmp_path / "out", fps=3, long_edge=320)
    assert len(frames) == 3
    h, w = cv2.imread(str(frames[0])).shape[:2]
    assert (w, h) == expected


def test_scale_filter_never_enlarges():
    assert "min(1600,iw)" in scale_filter(1600) and "min(1600,ih)" in scale_filter(1600)


# --------------------------------------------------------------------------
# trainer wiring
# --------------------------------------------------------------------------


@pytest.fixture
def fake_trainer(tmp_path, monkeypatch):
    python = tmp_path / "venv" / "bin" / "python"
    examples = tmp_path / "gsplat" / "examples"
    python.parent.mkdir(parents=True)
    examples.mkdir(parents=True)
    python.write_text("")
    (examples / "simple_trainer.py").write_text("")
    monkeypatch.setenv("GSPLAT_PYTHON", str(python))
    monkeypatch.setenv("GSPLAT_EXAMPLES", str(examples))
    return python, examples


def test_trainer_command_caps_splats_and_keeps_the_colmap_frame(fake_trainer, tmp_path):
    cmd = train_mod.command(
        tmp_path / "data", tmp_path / "out", train_mod.TrainSettings(max_splats=900_000, steps=7000)
    )
    joined = " ".join(cmd)
    assert cmd[2] == "mcmc"
    assert "--strategy.cap-max 900000" in joined
    assert "--max-steps 7000" in joined and "--ply-steps 7000" in joined
    # align reads gravity from the COLMAP cameras, so the splat must stay in
    # their frame.
    assert "--no-normalize-world-space" in cmd
    assert "--save-ply" in cmd and "--disable-viewer" in cmd


def test_missing_trainer_is_explained(monkeypatch, tmp_path):
    monkeypatch.setenv("GSPLAT_PYTHON", str(tmp_path / "nope"))
    with pytest.raises(train_mod.TrainerMissing, match="docs/PIPELINE.md"):
        train_mod.trainer_paths()


def test_collect_reads_the_trainers_numbers(tmp_path):
    settings = train_mod.TrainSettings(steps=7000)
    result = tmp_path / "train"
    (result / "ply").mkdir(parents=True)
    (result / "stats").mkdir()
    (result / "ply" / "point_cloud_6999.ply").write_bytes(b"ply")
    (result / "stats" / "val_step6999.json").write_text(
        json.dumps({"psnr": 27.4567, "ssim": 0.87654, "lpips": 0.2134, "num_GS": 812345})
    )
    (result / "stats" / "train_step6999_rank0.json").write_text(
        json.dumps({"mem": 5.4321, "num_GS": 812345})
    )
    out = tmp_path / "trained.ply"
    report = train_mod.collect(result, settings, 41.26, out)
    assert out.read_bytes() == b"ply"
    assert (report.psnr, report.ssim, report.splats, report.peak_vram_gb) == (
        27.46, 0.8765, 812345, 5.43
    )
    assert report.minutes == 41.3


def test_collect_fails_loudly_without_output(tmp_path):
    with pytest.raises(RuntimeError, match="without writing"):
        train_mod.collect(tmp_path, train_mod.TrainSettings(), 1.0, tmp_path / "x.ply")



def test_windows_paths_are_translated_for_wsl():
    assert local_path(Path(r"C:\Users\me\room.mov")).as_posix() == "/mnt/c/Users/me/room.mov"
    assert local_path(Path("D:/caps/scan.json")).as_posix() == "/mnt/d/caps/scan.json"
    assert local_path(Path("data/room.mov")) == Path("data/room.mov")
