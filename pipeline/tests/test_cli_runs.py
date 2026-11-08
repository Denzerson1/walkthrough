"""
The CLI as a user runs it — through Typer, with the expensive stages (frame
decoding, COLMAP, the GPU trainer, registration) replaced by stubs that
record what they were asked and write what the next stage reads.

The audit found `run-all` calling `ingest(video=...)` after `ingest` had
been renamed to take `capture`: nothing exercised the real command, so it
broke silently. These tests also hold the rerun rules: no stage builds on
an older run of the one before it, and a failed run never costs the last
good result.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner
from walkthrough_pipeline import cli, provenance
from walkthrough_pipeline import train as train_mod
from walkthrough_pipeline.paths import ProjectPaths
from walkthrough_pipeline.scene import AlignReport, ExportReport

runner = CliRunner()


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A project under tmp_path, and a log of what each stubbed stage was asked."""
    paths = ProjectPaths(tmp_path / "data" / "projects" / "room")
    monkeypatch.setattr(cli, "_paths", lambda project: paths)
    calls: list[tuple[str, dict]] = []

    def frames_stub(video, raw_dir, out_dir, fps, min_frames, max_frames, long_edge, run):
        calls.append(("frames", {"video": video.name, "fps": fps, "long_edge": long_edge,
                                 "min_frames": min_frames, "max_frames": max_frames}))
        frames = [raw_dir / f"frame_{i:05d}.png" for i in range(3)]
        raw_dir.mkdir(parents=True, exist_ok=True)
        for f in frames:
            f.write_bytes(b"png")
        cli._replace_with(out_dir, frames, run)
        return {"source": "video", "decoded": 3, "kept": 3}

    def poses_stub(paths_, run, matcher, camera_model):
        calls.append(("poses", {"matcher": matcher, "camera_model": camera_model,
                                "frames": sorted(p.name for p in
                                                 (paths_.work / "frames").iterdir())}))
        with provenance.replace_dir(paths_.work / "undistorted") as build:
            (build / "sparse").mkdir()

    def trainer_stub(cmd, cwd, log):
        args = dict(zip(cmd[3::1], cmd[4::1], strict=False))
        calls.append(("train", {"cmd": cmd}))
        result = Path(args["--result-dir"])
        step = int(args["--max-steps"]) - 1
        (result / "ply").mkdir(parents=True)
        (result / "ply" / f"point_cloud_{step}.ply").write_bytes(b"ply " + str(step).encode())
        (result / "stats").mkdir()
        (result / "stats" / f"val_step{step:04d}.json").write_text(json.dumps(
            {"psnr": 30.0, "ssim": 0.95, "lpips": 0.1, "num_GS": 1234}))
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("trained\n")

    def align_stub(paths_, project_id, metric=False, scale_tolerance=0.0):
        calls.append(("align", {"metric": metric, "scale_tolerance": scale_tolerance,
                                "trained": (paths_.work / "trained.ply").read_bytes()}))
        return AlignReport("trained.ply", 1, "cameras", 0.5, "roomplan", 1.0,
                           roomplan={}, floor={"from": "splats"})

    def export_stub(paths_, max_splats, crop_margin, sh_degree):
        calls.append(("export", {"max_splats": max_splats, "crop_margin": crop_margin,
                                 "sh_degree": sh_degree}))
        (paths_.scene / "scene.spz").write_bytes(b"spz")
        return ExportReport(1, 0, 0, sh_degree, {"scene.spz": 0.0}, 0.0)

    monkeypatch.setattr(cli, "_frames_from_video", frames_stub)
    monkeypatch.setattr(cli, "_poses_from_colmap", poses_stub)
    monkeypatch.setattr(cli, "_run_logged", trainer_stub)
    monkeypatch.setattr(cli, "align_scene", align_stub)
    monkeypatch.setattr(cli, "export_scene", export_stub)
    monkeypatch.setattr(train_mod, "trainer_paths",
                        lambda: (Path("/venv/python"), Path("/gsplat/examples")))
    monkeypatch.setattr(train_mod, "trainer_versions", lambda: {"gsplat": "stub"})
    monkeypatch.setattr(cli.frames_mod, "check_video", lambda video: [])
    return paths, calls


def _video(tmp_path, name="room.mov", content=b"one take"):
    clip = tmp_path / name
    clip.write_bytes(content)
    return clip


def _scan(tmp_path, name="scan.json"):
    import sys

    sys.path.insert(0, str(Path(__file__).parent))
    from test_roomplan import _captured_room

    scan = tmp_path / name
    scan.write_text(json.dumps(_captured_room()))
    return scan


def test_run_all_runs_every_stage_in_order_with_its_options(project, tmp_path):
    paths, calls = project
    result = runner.invoke(cli.app, [
        "run-all", str(_video(tmp_path)), "--project", "room", "--roomplan", str(_scan(tmp_path)),
        "--fps", "2.5", "--long-edge", "1200", "--steps", "1000", "--max-splats", "200000",
        "--sh-degree", "2", "--crop-margin", "0.5",
    ])
    assert result.exit_code == 0, result.output
    assert [name for name, _ in calls] == ["frames", "poses", "train", "align", "export"]
    frames = dict(calls)["frames"]
    assert frames == {"video": "video.mov", "fps": 2.5, "long_edge": 1200, "min_frames": 150,
                      "max_frames": 600}
    assert dict(calls)["poses"]["frames"] == ["frame_00000.png", "frame_00001.png",
                                              "frame_00002.png"]
    cmd = dict(calls)["train"]["cmd"]
    for flag, value in (("--max-steps", "1000"), ("--strategy.cap-max", "200000"),
                        ("--sh-degree", "2"), ("--data-factor", "1")):
        assert cmd[cmd.index(flag) + 1] == value
    assert "--depth-loss" not in cmd and "--pose-opt" not in cmd  # a video: COLMAP poses
    assert dict(calls)["align"]["trained"] == b"ply 999"
    assert dict(calls)["align"]["metric"] is False
    assert dict(calls)["export"] == {"max_splats": 200000, "crop_margin": 0.5, "sh_degree": 2}
    for stage in provenance.UPSTREAM:
        assert provenance.freshness(paths, stage) is None, stage
    record = provenance.load_record(paths, "train")
    assert record["settings"]["steps"] == 1000 and record["tools"]["gsplat"] == "stub"
    assert record["code"]["pipeline_source_sha256"]
    ingest = provenance.load_record(paths, "ingest")
    assert ingest["inputs"]["capture"]["sha256"]
    assert ingest["inputs"]["roomplan"]["name"] == "scan.json"


def test_a_new_capture_makes_every_later_stage_stale(project, tmp_path):
    paths, calls = project
    assert runner.invoke(cli.app, ["run-all", str(_video(tmp_path)), "--project", "room",
                                   "--steps", "500"]).exit_code == 0
    assert runner.invoke(cli.app, ["ingest", str(_video(tmp_path, "b.mov", b"another take")),
                                   "--project", "room"]).exit_code == 0
    # Poses would be built from frames of the old video: refused.
    result = runner.invoke(cli.app, ["poses", "--project", "room"])
    assert result.exit_code != 0
    assert isinstance(result.exception, provenance.StaleInput)
    assert "older `ingest`" in str(result.exception)
    for stage in ("frames", "poses", "train", "align", "export"):
        assert provenance.freshness(paths, stage) is not None


def test_an_old_scan_is_never_reused_silently(project, tmp_path):
    paths, _ = project
    scan = _scan(tmp_path)
    assert runner.invoke(cli.app, ["ingest", str(_video(tmp_path)), "--project", "room",
                                   "--roomplan", str(scan)]).exit_code == 0
    result = runner.invoke(cli.app, ["ingest", str(_video(tmp_path, "b.mov", b"new")),
                                     "--project", "room"])
    assert result.exit_code == 0
    assert not (paths.input / "roomplan").exists()
    assert len(list(paths.input.glob("roomplan.set-aside-*"))) == 1
    assert "roomplan" not in provenance.load_record(paths, "ingest")["inputs"]

    aside = next(paths.input.glob("roomplan.set-aside-*"))
    aside.rename(paths.input / "roomplan")
    result = runner.invoke(cli.app, ["ingest", str(_video(tmp_path, "c.mov", b"newer")),
                                     "--project", "room", "--keep-roomplan"])
    assert result.exit_code == 0
    record = provenance.load_record(paths, "ingest")
    assert record["notes"]["roomplan"] == "reused (--keep-roomplan)"
    assert record["inputs"]["roomplan"]["name"] == "scan.json"


def test_a_failed_training_run_keeps_the_last_good_one(project, tmp_path, monkeypatch):
    paths, _ = project
    assert runner.invoke(cli.app, ["run-all", str(_video(tmp_path)), "--project", "room",
                                   "--steps", "500"]).exit_code == 0
    good_ply = (paths.work / "trained.ply").read_bytes()
    good_record = provenance.load_record(paths, "train")

    def crash(cmd, cwd, log):
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("CUDA out of memory\n")
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(cli, "_run_logged", crash)
    result = runner.invoke(cli.app, ["train", "--project", "room", "--steps", "800"])
    assert result.exit_code != 0
    assert (paths.work / "trained.ply").read_bytes() == good_ply
    assert (paths.work / "train" / "ply" / "point_cloud_499.ply").is_file()
    assert provenance.load_record(paths, "train") == good_record
    failed = list((paths.work / "failed").glob("train-*"))
    assert len(failed) == 1
    run = json.loads((failed[0] / "run.json").read_text())
    assert run["status"] == "failed" and "CalledProcessError" in run["error"]
    assert run["settings"]["steps"] == 800
    assert "CUDA out of memory" in (paths.root / run["log"]).read_text()
    # Nothing downstream went stale: the last good train is still current.
    assert provenance.freshness(paths, "export") is None


def test_an_experiment_leaves_the_baseline_alone(project, tmp_path):
    paths, calls = project
    assert runner.invoke(cli.app, ["run-all", str(_video(tmp_path)), "--project", "room",
                                   "--steps", "500"]).exit_code == 0
    baseline = (paths.work / "trained.ply").read_bytes()
    result = runner.invoke(cli.app, ["train", "--project", "room", "--steps", "300",
                                     "--experiment", "short", "--depth", "on"])
    assert result.exit_code == 0, result.output
    assert (paths.work / "trained.ply").read_bytes() == baseline
    assert (paths.work / "experiments" / "short" / "trained.ply").read_bytes() == b"ply 299"
    assert provenance.load_record(paths, "experiment-short")["settings"]["depth"] is True
    assert provenance.freshness(paths, "export") is None


@pytest.mark.parametrize("args", [
    ["train", "--project", "room", "--steps", "10"],
    ["train", "--project", "room", "--depth", "maybe"],
    ["export", "--project", "room", "--sh-degree", "4"],
    ["align", "--project", "room", "--scale-tolerance", "0.2"],
    ["frames", "--project", "room", "--fps", "0"],
])
def test_out_of_range_settings_are_refused(project, args):
    result = runner.invoke(cli.app, args)
    assert result.exit_code != 0
