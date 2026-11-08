"""
`pipeline` CLI: video (+ optional RoomPlan scan) -> scene (M1).

Runs on the Linux/CUDA machine (WSL2 here; docs/PIPELINE.md). Each stage
reads what the previous one wrote under data/projects/<id>/, so any stage can
be rerun on its own. Stages that cannot run say exactly why rather than
pretending — docs/BRIEF.md §2.6.
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np
import typer
from rich.console import Console

from . import flat_geometry, room_split
from . import frames as frames_mod
from . import outside as outside_mod
from . import provenance as prov
from . import rebuild as rebuild_mod
from . import roomplan as roomplan_mod
from . import stray as stray_mod
from . import train as train_mod
from .manifest import (
    HeroView,
    Lighting,
    Listing,
    Manifest,
    Obstacle,
    Room,
    SunSample,
    Waypoint,
)
from .paths import ProjectPaths, repo_root
from .scene import AlignmentFailed, align_scene, export_scene, trained_ply

app = typer.Typer(help="walkthrough capture pipeline: video -> scene", no_args_is_help=True)
console = Console()


class ToolMissing(RuntimeError):
    """A required external binary or package is not installed."""


def _paths(project: str) -> ProjectPaths:
    return ProjectPaths.for_project(repo_root(), project)


def _pycolmap():
    try:
        import pycolmap  # type: ignore[import-not-found]
    except ImportError as exc:
        raise ToolMissing(
            "pycolmap is not installed. On the pipeline machine run "
            "`uv sync --extra pipeline` — see docs/PIPELINE.md."
        ) from exc
    return pycolmap


def local_path(raw: Path) -> Path:
    r"""
    Accept a Windows path when running inside WSL: `C:\Users\me\room.mov`
    becomes `/mnt/c/Users/me/room.mov`, and a relative `data\room.mov` gets
    forward slashes, so `pnpm run pipeline` from a Windows shell can be
    handed whatever Explorer copies.
    """
    text = str(raw)
    if raw.exists():
        return raw
    if len(text) > 2 and text[1] == ":" and text[0].isalpha():
        rest = text[2:].replace("\\", "/").lstrip("/")
        return Path(f"/mnt/{text[0].lower()}/{rest}")
    return Path(text.replace("\\", "/"))


def _existing(raw: Path | None, what: str) -> Path | None:
    if raw is None:
        return None
    path = local_path(raw)
    if not path.exists():
        raise typer.BadParameter(f"{what} not found: {raw}")
    return path


def _stray(paths: ProjectPaths) -> stray_mod.StrayCapture | None:
    """The project's Stray Scanner capture, if it was ingested from one."""
    root = stray_mod.find_capture(paths.input / "stray")
    return stray_mod.load(root) if root is not None else None


def _fail(message: str) -> typer.Exit:
    console.print(f"[red]{message}[/red]")
    return typer.Exit(1)


def _inside(path: Path, folder: Path) -> bool:
    try:
        path.resolve().relative_to(folder.resolve())
        return True
    except ValueError:
        return False


@app.command()
def ingest(
    capture: Path = typer.Argument(
        ..., help="A video (.mov/.mp4) or a Stray Scanner dataset folder; Windows paths are fine"
    ),
    project: str = typer.Option(..., "--project", "-p", help="Project id, e.g. flat-01"),
    roomplan: Path | None = typer.Option(
        None, help="RoomPlan export: .json, .usdz or Polycam .glb, or a folder holding one"
    ),
    keep_roomplan: bool = typer.Option(
        False, help="Keep the project's existing RoomPlan scan for this new capture. Without "
        "it (and without --roomplan) an old scan is set aside, never silently reused."
    ),
) -> None:
    """Copy a capture into the project layout and check it before hours go into it."""
    capture = _existing(capture, "Capture")
    roomplan = _existing(roomplan, "RoomPlan export")
    if roomplan is not None and keep_roomplan:
        raise typer.BadParameter("Pass --roomplan or --keep-roomplan, not both.")
    paths = _paths(project)
    paths.ensure()
    stray_dir = paths.input / "stray"
    in_place = _inside(capture, stray_dir) or capture.parent.resolve() == paths.input.resolve()
    with prov.stage_run(paths, "ingest", {"keep_roomplan": keep_roomplan},
                        check_upstream=False) as run:
        if not in_place:
            for stale in paths.input.glob("video.*"):
                stale.unlink()
            shutil.rmtree(stray_dir, ignore_errors=True)
        if capture.is_dir():
            if not stray_mod.is_capture(capture):
                raise typer.BadParameter(f"{capture} is a folder but not a Stray Scanner dataset.")
            if not in_place:
                shutil.copytree(capture, stray_dir)
            root = stray_mod.find_capture(stray_dir)
            loaded = stray_mod.load(root)
            run.notes["capture_type"] = "stray"
            run.inputs["capture"] = prov.fingerprint(root)
            console.print(
                f"[green]Ingested Stray Scanner capture[/green]: {len(loaded)} posed frames, "
                f"video {loaded.size[0]}x{loaded.size[1]}"
            )
        else:
            target = capture if in_place else paths.input / f"video{capture.suffix.lower()}"
            if not in_place:
                shutil.copy2(capture, target)
            run.notes["capture_type"] = "video"
            run.inputs["capture"] = prov.fingerprint(target)
            console.print(f"[green]Ingested[/green] {capture.name} -> "
                          f"{target.relative_to(paths.root)}")
            for warning in frames_mod.check_video(target):
                console.print(f"[yellow]{warning}[/yellow]")
        run.inputs["capture"]["source"] = str(capture)

        dest = paths.input / "roomplan"
        if roomplan is not None:
            if dest.exists():
                shutil.rmtree(dest)
            dest.mkdir(parents=True)
            if roomplan.is_dir():
                shutil.copytree(roomplan, dest, dirs_exist_ok=True)
            else:
                shutil.copy2(roomplan, dest / roomplan.name)
            run.notes["roomplan"] = "new"
        elif dest.exists() and not keep_roomplan:
            aside = paths.input / f"roomplan.set-aside-{run.run_id}"
            dest.rename(aside)
            run.notes["roomplan"] = f"old scan set aside to {aside.name}"
            console.print(f"[yellow]The project's old RoomPlan scan was set aside "
                          f"({aside.name}); pass --keep-roomplan to use it with this capture."
                          "[/yellow]")
        elif dest.exists():
            run.notes["roomplan"] = "reused (--keep-roomplan)"
        if dest.exists():
            found = roomplan_mod.find_export(dest)
            if found is None:
                raise typer.BadParameter(f"No RoomPlan export (.json, .usdz, .glb) in {dest}")
            # Parsed now: a scan that cannot be read should fail here, not
            # after an hour of training.
            scan = roomplan_mod.load(found)
            room, items, _ = roomplan_mod.to_rooms(scan)
            run.inputs["roomplan"] = prov.fingerprint(found)
            console.print(
                f"[green]RoomPlan[/green] {found.name} ({run.notes['roomplan']}): "
                f"{len(scan.of('walls'))} walls, {len(room.openings)} doors/windows, "
                f"{len(items)} furniture boxes, {_area(room.floorPolygon):.1f} m², "
                f"ceiling {room.walls[0].height:.2f} m"
            )

    if not paths.manifest.exists():
        Manifest(id=project, name=project).write(paths.manifest)
        console.print(f"Created a starter manifest at {paths.manifest.relative_to(paths.root)}")


def _area(poly: list[tuple[float, float]]) -> float:
    return 0.5 * abs(
        sum(poly[i][0] * poly[i - 1][1] - poly[i - 1][0] * poly[i][1] for i in range(len(poly)))
    )


@app.command()
def frames(
    project: str = typer.Option(..., "--project", "-p"),
    fps: float = typer.Option(3.0, min=0.5, max=15.0, help="Frames kept per second of footage"),
    min_frames: int = typer.Option(150, min=20, max=5000),
    max_frames: int = typer.Option(600, min=20, max=5000),
    long_edge: int = typer.Option(1600, min=480, max=4096,
                                  help="Frames are scaled to this long edge (8 GB VRAM)"),
) -> None:
    """Extract frames with ffmpeg; keep sharp ones that add a view, and all of the walk."""
    if max_frames < min_frames:
        raise typer.BadParameter("--max-frames is below --min-frames")
    paths = _paths(project)
    settings = {"fps": fps, "min_frames": min_frames, "max_frames": max_frames,
                "long_edge": long_edge}
    with prov.stage_run(paths, "frames", settings) as run:
        out_dir = paths.work / "frames"
        capture = _stray(paths)
        if capture is not None:
            report = _frames_from_stray(capture, paths.work / "frames_raw", out_dir, fps,
                                        long_edge, run)
        else:
            video = next((p for p in paths.input.glob("video.*")), None)
            if video is None:
                raise typer.BadParameter(f"No capture in {paths.input}. Run `pipeline ingest`.")
            report = _frames_from_video(video, paths.work / "frames_raw", out_dir, fps,
                                        min_frames, max_frames, long_edge, run)
        report.update(fps=fps, long_edge=long_edge)
        run.metrics.update({k: v for k, v in report.items() if not isinstance(v, list)})
        prov.atomic_write_text(paths.work / "frames_report.json", json.dumps(report, indent=2))
    console.print(f"[green]{report['kept']} frames ready[/green] in {out_dir}")


def _replace_with(out_dir: Path, keep: list[Path], run: prov.StageRun) -> None:
    with prov.replace_dir(out_dir, keep_failed=run.failed_dir / out_dir.name) as build:
        for path in keep:
            shutil.copy2(path, build / path.name)


def _frames_from_video(
    video: Path, raw_dir: Path, out_dir: Path,
    fps: float, min_frames: int, max_frames: int, long_edge: int, run: prov.StageRun,
) -> dict:
    console.print(f"Extracting frames at {fps} fps, long edge {long_edge} px ...")
    extracted = frames_mod.extract_frames(video, raw_dir, fps=fps, long_edge=long_edge)
    console.print(f"  {len(extracted)} frames decoded")
    scores = frames_mod.score_directory(raw_dir)
    # Without poses, motion is how much the picture changed between frames.
    selection = frames_mod.select_by_coverage(
        scores, frames_mod.image_motion(list(scores)), window=1, static=2.0
    )
    keep = selection.keep
    trimmed: list[Path] = []
    if len(keep) > max_frames:  # even thinning keeps the whole walk
        idx = set(np.linspace(0, len(keep) - 1, max_frames).round().astype(int))
        trimmed = [p for i, p in enumerate(keep) if i not in idx]
        keep = [p for i, p in enumerate(keep) if i in idx]
    if len(keep) < min_frames and selection.dropped_blurry:
        # Too few to register: the least blurred of the dropped come back.
        back = sorted(selection.dropped_blurry, key=lambda p: -scores[p])
        keep = sorted(keep + back[: min_frames - len(keep)], key=lambda p: p.name)
    if len(keep) < min_frames:
        console.print(f"[yellow]Only {len(keep)} frames after selection (minimum "
                      f"{min_frames}); the capture is short or mostly static.[/yellow]")
    console.print(f"  {selection.summary()}, {len(trimmed)} trimmed to --max-frames")
    _replace_with(out_dir, keep, run)
    return {"source": "video", "decoded": len(extracted), **selection.summary(),
            "kept": len(keep), "dropped_to_target": len(trimmed),
            "kept_for_coverage_frames": [p.name for p in selection.kept_for_coverage]}


#: Candidates per kept frame when choosing the sharpest from a posed capture.
CANDIDATES_PER_FRAME = 4


def _frames_from_stray(
    capture: stray_mod.StrayCapture, raw_dir: Path, out_dir: Path, fps: float, long_edge: int,
    run: prov.StageRun,
) -> dict:
    """
    The sharpest of every few consecutive frames, keeping each frame's
    number so it lines up with its ARKit pose and depth map; a frame that
    barely moved from the last is a duplicate, and a stretch of the walk
    is never left without a frame.
    """
    video_fps = frames_mod.frame_rate(capture.video) or 60.0
    step = max(1, round(video_fps / (fps * CANDIDATES_PER_FRAME)))
    console.print(f"Decoding every {step}th frame, long edge {long_edge} px ...")
    candidates = frames_mod.extract_numbered(capture.video, raw_dir, step, long_edge)
    scores = frames_mod.score_directory(raw_dir)
    numbers = [int(p.stem.split("_")[1]) for p in sorted(scores, key=lambda p: p.name)]
    selection = frames_mod.select_by_coverage(
        scores, stray_mod.pose_motion(capture, numbers), CANDIDATES_PER_FRAME, static=1.0
    )
    console.print(f"  {len(candidates)} candidates: {selection.summary()}")
    _replace_with(out_dir, selection.keep, run)
    return {"source": "stray", "decoded": len(candidates), "step": step, **selection.summary(),
            "kept_for_coverage_frames": [p.name for p in selection.kept_for_coverage]}


@app.command()
def poses(
    project: str = typer.Option(..., "--project", "-p"),
    matcher: str = typer.Option(
        "auto", help="auto | exhaustive | sequential. auto: exhaustive up to 400 frames"
    ),
    camera_model: str = typer.Option(
        "OPENCV",
        help="OPENCV suits the iPhone's ultra-wide as recorded (iOS corrects most lens "
        "distortion in video). OPENCV_FISHEYE if the frames are visibly fisheye.",
    ),
    max_track: int = typer.Option(stray_mod.MAX_TRACK, min=1, max=50,
                                  help="Stray: frames kept per LiDAR point (depth supervision)"),
    min_frame_obs: int = typer.Option(stray_mod.MIN_FRAME_OBS, min=0, max=20_000,
                                      help="Stray: depth sightings every frame keeps at least"),
) -> None:
    """
    Camera poses for the trainer: from ARKit for a Stray capture, otherwise
    estimated with COLMAP (pycolmap, GPU SIFT) and undistorted.
    """
    if matcher not in ("auto", "exhaustive", "sequential"):
        raise typer.BadParameter(f"Unknown matcher {matcher!r}")
    paths = _paths(project)
    settings = {"matcher": matcher, "camera_model": camera_model, "max_track": max_track,
                "min_frame_obs": min_frame_obs}
    with prov.stage_run(paths, "poses", settings) as run:
        capture = _stray(paths)
        if capture is not None:
            _poses_from_stray(capture, paths, run, max_track, min_frame_obs)
        else:
            _poses_from_colmap(paths, run, matcher, camera_model)


def _poses_from_colmap(paths: ProjectPaths, run: prov.StageRun, matcher: str,
                       camera_model: str) -> None:
    pycolmap = _pycolmap()
    run.tools["pycolmap"] = getattr(pycolmap, "__version__", None)
    frame_dir = paths.work / "frames"
    names = sorted(p.name for p in frame_dir.glob("*.png")) if frame_dir.is_dir() else []
    if not names:
        raise typer.BadParameter("No frames. Run `pipeline frames` first.")

    db = paths.work / "colmap.db"
    db.unlink(missing_ok=True)
    started = time.time()
    console.print(f"Extracting features from {len(names)} frames ...")
    reader = pycolmap.ImageReaderOptions()
    reader.camera_model = camera_model
    pycolmap.extract_features(
        db, frame_dir, camera_mode=pycolmap.CameraMode.SINGLE, reader_options=reader
    )
    if matcher == "auto":
        # A single room is a few hundred frames, where exhaustive matching is
        # affordable and closes the loop a sequential matcher can miss.
        matcher = "exhaustive" if len(names) <= 400 else "sequential"
    console.print(f"Matching ({matcher}) ...")
    if matcher == "exhaustive":
        pycolmap.match_exhaustive(db)
    else:
        pycolmap.match_sequential(db)

    console.print("Mapping ...")
    with prov.replace_dir(paths.work / "sparse", keep_failed=run.failed_dir / "sparse") as sparse:
        models = pycolmap.incremental_mapping(db, frame_dir, sparse)
        if not models:
            raise _fail("COLMAP registered no model. See docs/PIPELINE.md failure modes.")
        best_id, best = max(models.items(), key=lambda kv: kv[1].num_reg_images())
        # The trainer reads sparse/0; COLMAP numbers models by discovery, not size.
        (sparse / "best").mkdir()
        best.write(sparse / "best")
    console.print("Undistorting ...")
    with prov.replace_dir(paths.work / "undistorted",
                          keep_failed=run.failed_dir / "undistorted") as undistorted:
        pycolmap.undistort_images(undistorted, paths.work / "sparse" / "best", frame_dir)

    report = {
        "source": "video (COLMAP)",
        "frames": len(names),
        "matcher": matcher,
        "camera_model": camera_model,
        "minutes": round((time.time() - started) / 60, 1),
        **_colmap_quality(best, models, names),
        "model_used": int(best_id),
    }
    run.metrics.update({k: v for k, v in report.items() if not isinstance(v, list | dict)})
    prov.atomic_write_text(paths.work / "poses_report.json", json.dumps(report, indent=2))
    console.print(
        f"[green]Registered {report['registered']}/{len(names)} frames "
        f"({report['share']:.0%})[/green], {report['points3D']:,} points, mean reprojection "
        f"error {report['mean_reprojection_error_px']} px, {report['minutes']} min"
    )
    for warning in report["warnings"]:
        console.print(f"[yellow]{warning}[/yellow]")


def _colmap_quality(best, models: dict, names: list[str]) -> dict:
    """
    What says whether COLMAP's poses can be trusted, beyond how many frames
    registered: reprojection error, track length, the camera it calibrated,
    whether the walk fell apart into pieces, and where.
    """
    def call(obj, name):
        fn = getattr(obj, name, None)
        try:
            return round(float(fn()), 3) if callable(fn) else None
        except Exception:  # noqa: BLE001 - optional diagnostics across pycolmap versions
            return None

    reg_ids = list(best.reg_image_ids()) if hasattr(best, "reg_image_ids") else list(best.images)
    registered = {best.images[i].name for i in reg_ids}
    order = {n: i for i, n in enumerate(names)}
    missing = sorted(order[n] for n in names if n not in registered)
    runs, start = [], None
    for a, b in zip(missing, [*missing[1:], None], strict=True):
        start = a if start is None else start
        if b != a + 1:
            runs.append([names[start], names[a], a - start + 1])
            start = None
    camera = next(iter(best.cameras.values()))
    params = [round(float(v), 5) for v in camera.params]
    fov = float(np.degrees(2 * np.arctan(camera.width / (2 * params[0])))) if params else None
    share = len(registered) / max(len(names), 1)
    sizes = sorted((m.num_reg_images() for m in models.values()), reverse=True)
    warnings = []
    if len(models) > 1:
        warnings.append(f"COLMAP split the walk into {len(models)} models {sizes}; only the "
                        "largest is used. Usually a doorway walked too fast.")
    long_runs = [r for r in runs if r[2] >= 5]
    if long_runs:
        warnings.append(f"{len(long_runs)} stretch(es) of 5+ frames did not register, e.g. "
                        f"{long_runs[0][0]}..{long_runs[0][1]}: that part of the room has no "
                        "trained views.")
    reproj = call(best, "compute_mean_reprojection_error")
    if reproj is not None and reproj > 1.5:
        warnings.append(f"Mean reprojection error {reproj:.2f} px: the calibration or the "
                        "matches are poor.")
    if share < 0.7:
        warnings.append("Under 70% registered. The capture likely moved too fast or the room "
                        "is textureless. See docs/PIPELINE.md 'known failure modes'.")
    return {
        "registered": len(registered),
        "share": round(share, 3),
        "models": len(models),
        "model_sizes": sizes,
        "points3D": best.num_points3D(),
        "mean_reprojection_error_px": reproj,
        "mean_track_length": call(best, "compute_mean_track_length"),
        "mean_observations_per_image": call(best, "compute_mean_observations_per_reg_image"),
        "camera": {"model": str(camera.model), "width": camera.width,
                   "height": camera.height, "params": params,
                   "horizontal_fov_deg": None if fov is None else round(fov, 1)},
        "unregistered_runs": runs,
        "warnings": warnings,
    }


def _poses_from_stray(capture: stray_mod.StrayCapture, paths: ProjectPaths,
                      run: prov.StageRun, max_track: int, min_frame_obs: int) -> None:
    frame_paths = sorted((paths.work / "frames").glob("frame_*.png"))
    if not frame_paths:
        raise typer.BadParameter("No frames. Run `pipeline frames` first.")
    started = time.time()
    console.print(f"Building the dataset from ARKit poses and LiDAR ({len(frame_paths)} frames)")
    offset, agreement = stray_mod.frame_offset(capture)
    if agreement < 0.5:
        console.print(
            f"[yellow]Video/pose sync unclear ({agreement:.0%} of samples agreed); "
            "assuming they line up.[/yellow]"
        )
        offset = 0
    else:
        console.print(f"  video is {offset:+d} frame(s) from its poses ({agreement:.0%} agree)")
    built = stray_mod.build_dataset(capture, frame_paths, paths.work / "undistorted", offset,
                                    max_track=max_track, min_frame_obs=min_frame_obs)
    per_frame = built.summary()
    report = {
        "source": "stray (ARKit poses, LiDAR depth)",
        "frames": built.frames,
        "registered": built.frames,
        "share": round(built.frames / len(frame_paths), 3),
        "points3D": built.points,
        "depth_observations": built.observations,
        "depth_observations_per_frame": per_frame,
        "max_track": built.max_track,
        "dropped_tracking_jump": built.dropped_tracking,
        "dropped_no_pose": built.dropped_no_pose,
        "dropped_no_depth": built.dropped_no_depth,
        "video_frame_offset": offset,
        "offset_agreement": round(agreement, 2),
        "image_size": [built.width, built.height],
        "minutes": round((time.time() - started) / 60, 1),
    }
    prov.atomic_write_text(paths.work / "poses_report.json", json.dumps(report, indent=2))
    prov.atomic_write_text(paths.work / "depth_observations.json",
                           json.dumps(built.observations_per_frame, indent=1))
    run.metrics.update({k: v for k, v in report.items() if not isinstance(v, list)})
    console.print(
        f"[green]{built.frames} frames posed by ARKit[/green], {built.points:,} LiDAR points "
        f"seen {built.observations:,} times; per frame min {per_frame['min']}, median "
        f"{per_frame['median']}, max {per_frame['max']}"
    )
    dropped = (len(built.dropped_tracking) + len(built.dropped_no_pose)
               + len(built.dropped_no_depth))
    if dropped:
        console.print(f"[yellow]{dropped} frame(s) left out: {len(built.dropped_tracking)} at "
                      f"tracking jumps, {len(built.dropped_no_pose)} without a pose, "
                      f"{len(built.dropped_no_depth)} without usable LiDAR depth "
                      "(see poses_report.json)[/yellow]")


def _switch(value: str, auto: bool, name: str) -> bool:
    if value not in ("auto", "on", "off"):
        raise typer.BadParameter(f"--{name} is auto, on or off, not {value!r}")
    return auto if value == "auto" else value == "on"


def _run_logged(cmd: list[str], cwd: Path, log: Path) -> None:
    """Run `cmd`, its output both shown and kept in `log` (a failed run's evidence)."""
    import os

    log.parent.mkdir(parents=True, exist_ok=True)
    # tqdm redraws ~20 times a second; every 10 s is plenty for a log.
    env = {**os.environ, "TQDM_MININTERVAL": "10"}
    with log.open("w", encoding="utf-8") as out, subprocess.Popen(
        cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
        env=env,
    ) as proc:
        assert proc.stdout is not None
        for line in proc.stdout:
            out.write(line)
            console.out(line.rstrip("\n"), highlight=False)
        code = proc.wait()
    if code:
        raise subprocess.CalledProcessError(code, cmd)


@app.command()
def train(
    project: str = typer.Option(..., "--project", "-p"),
    max_splats: int = typer.Option(1_500_000, min=50_000, max=6_000_000,
                                   help="Gaussian cap (8 GB VRAM, download size)"),
    steps: int = typer.Option(30_000, min=200, max=200_000),
    holdout_every: int = typer.Option(8, min=2, max=100,
                                      help="Every Nth frame is held out to measure PSNR"),
    sh_degree: int = typer.Option(3, min=0, max=3),
    depth: str = typer.Option("auto", help="auto|on|off: supervise depth with the dataset's "
                              "points. auto: on for a Stray capture (LiDAR), off for COLMAP's."),
    depth_lambda: float = typer.Option(1e-2, min=0.0, max=1.0),
    pose_opt: str = typer.Option("auto", help="auto|on|off: refine camera poses in training. "
                                 "auto: on for ARKit poses, off for COLMAP's."),
    pose_opt_lr: float = typer.Option(1e-5, min=0.0, max=1e-2),
    pose_opt_reg: float = typer.Option(1e-6, min=0.0, max=1e-2),
    antialiased: bool = typer.Option(False),
    app_opt: bool = typer.Option(False, help="Per-frame appearance (exposure) embeddings; the "
                                 "exported splat loses view-dependent colour"),
    data_factor: int = typer.Option(1, min=1, max=8, help="Train on frames shrunk this much"),
    experiment: str | None = typer.Option(
        None, help="Train into work/experiments/<name> and leave work/train and trained.ply "
        "alone: for comparing one setting against the baseline."),
    displaced: str | None = typer.Option(
        None, help="With --experiment, on a Stray capture: leave the stretch 'start:end' of the "
        "walk (fractions, e.g. 0.45:0.55) out of training, so `pipeline compare` scores views "
        "away from every training camera, not just between them."),
) -> None:
    """Train the splat with gsplat (Apache-2.0), MCMC strategy, capped count."""
    paths = _paths(project)
    data = paths.work / "undistorted"
    if not (data / "sparse").is_dir():
        raise typer.BadParameter("No undistorted poses. Run `pipeline poses` first.")
    if experiment is not None and not experiment.replace("-", "").replace("_", "").isalnum():
        raise typer.BadParameter("--experiment takes letters, digits, - and _ only")
    left_out: list[str] = []
    if displaced is not None:
        if experiment is None or _stray(paths) is None:
            raise typer.BadParameter("--displaced needs --experiment and a Stray capture")
        try:
            start, end = (float(v) for v in displaced.split(":"))
        except ValueError as exc:
            raise typer.BadParameter("--displaced is start:end, e.g. 0.45:0.55") from exc
        if not 0 <= start < end <= 1 or end - start > 0.5:
            raise typer.BadParameter("--displaced: 0 <= start < end <= 1, at most half the walk")
        data, left_out = _dataset_without(paths, experiment, start, end)
    posed_by_arkit = _stray(paths) is not None
    settings = train_mod.TrainSettings(
        max_splats=max_splats,
        steps=steps,
        holdout_every=holdout_every,
        sh_degree=sh_degree,
        # LiDAR points carry real depth, so supervise with it; COLMAP's sparse
        # points are too few and too noisy on plain walls to be worth it.
        # ARKit poses are good to a centimetre or so, not to a pixel, so they
        # are refined in training; COLMAP's already are, against these images.
        depth=_switch(depth, posed_by_arkit, "depth"),
        depth_lambda=depth_lambda,
        refine_poses=_switch(pose_opt, posed_by_arkit, "pose-opt"),
        pose_opt_lr=pose_opt_lr,
        pose_opt_reg=pose_opt_reg,
        antialiased=antialiased,
        app_opt=app_opt,
        data_factor=data_factor,
    )
    target = paths.work / ("train" if experiment is None else f"experiments/{experiment}")
    record = "train" if experiment is None else f"experiment-{experiment}"
    with prov.stage_run(paths, "train", asdict(settings), record_as=record) as run:
        run.tools.update(train_mod.trainer_versions())
        run.notes["capture_type"] = "stray" if posed_by_arkit else "video"
        console.print(f"Training {steps:,} steps, cap {max_splats:,} splats -> "
                      f"{target.relative_to(paths.root)} (log {run.log_path.name}) ...")
        started = time.time()
        # Into a new folder: the last good result stays until this one is done.
        with prov.replace_dir(target, keep_failed=run.failed_dir / "train") as build:
            cmd = train_mod.command(data, build, settings)
            # The example trainer imports its siblings, so it runs from its folder.
            _run_logged(cmd, Path(cmd[1]).parent, run.log_path)
            report = train_mod.collect(build, settings, (time.time() - started) / 60,
                                       build / "trained.ply")
            if settings.refine_poses:
                run.metrics["pose_refinement"] = train_mod.export_refined_poses(
                    data, build, settings)
            train_mod.write_report(report, build / "train_report.json")
            if left_out:
                (build / "displaced.json").write_text(json.dumps(left_out))
        if experiment is None:
            prov.atomic_copy(target / "trained.ply", trained_ply(paths))
            train_mod.write_report(report, paths.work / "train_report.json")
        run.metrics.update(asdict(report))
        run.outputs["trained_ply"] = str((target / "trained.ply").relative_to(paths.root))
    console.print(
        f"[green]Trained[/green] {report.splats:,} splats in {report.minutes} min. "
        f"Held-out PSNR {report.psnr} dB, SSIM {report.ssim}, LPIPS {report.lpips}, "
        f"peak VRAM {report.peak_vram_gb} GB"
    )
    if "pose_refinement" in run.metrics:
        moved = run.metrics["pose_refinement"]
        console.print(f"  poses refined: median {moved['moved_cm']['median']} cm, "
                      f"{moved['turned_deg']['median']}°; worst {moved['moved_cm']['max']} cm")


def _dataset_without(paths: ProjectPaths, experiment: str, start: float,
                     end: float) -> tuple[Path, list[str]]:
    """The Stray dataset rebuilt without a stretch of the walk; the frames left out."""
    capture = _stray(paths)
    frames_all = sorted((paths.work / "frames").glob("frame_*.png"))
    a, b = int(start * len(frames_all)), int(end * len(frames_all))
    keep = frames_all[:a] + frames_all[b:]
    report = json.loads((paths.work / "poses_report.json").read_text())
    data = paths.work / "experiments" / f"{experiment}.data"
    stray_mod.build_dataset(capture, keep, data, int(report.get("video_frame_offset", 0)))
    console.print(f"  left out frames {a}-{b} of {len(frames_all)} "
                  f"({frames_all[a].name}..{frames_all[b - 1].name}) for a displaced test")
    return data, [p.name for p in frames_all[a:b]]


@app.command()
def compare(
    project: str = typer.Option(..., "--project", "-p"),
    label: str = typer.Option("latest", help="Folder under work/compare/"),
    views: int = typer.Option(8, min=1, max=64),
    experiment: str | None = typer.Option(None, help="Score an experiment's splat instead"),
) -> None:
    """
    Render the same views from the trained, aligned and exported splat with
    gsplat and score each against the source frames: where a defect starts.
    """
    from .compare import stage_comparison

    report = stage_comparison(_paths(project), label, views, experiment)
    console.print(f"[green]Compared[/green] {len(report['views'])} {report['set']} views "
                  f"({report['poses']}):")
    for stage, m in report["stages"].items():
        console.print(f"  {stage:<9} PSNR {m.get('psnr')} dB, SSIM {m.get('ssim')}")


@app.command(name="compare-browser")
def compare_browser(
    project: str = typer.Option(..., "--project", "-p"),
    shots: Path = typer.Option(..., help="Folder for cameras.json and the browser's shots"),
    prepare: bool = typer.Option(False, help="Only write cameras.json for "
                                 "scripts/browser-compare.mjs"),
    views: int = typer.Option(6, min=1, max=40),
) -> None:
    """
    The browser stage: the viewer's canvas against gsplat's render of the
    published SPZ from the same camera. Run with --prepare, then
    `node scripts/browser-compare.mjs <viewer url> <shots>/cameras.json <shots>`, then again.
    """
    from .compare import browser_cameras, browser_comparison

    paths = _paths(project)
    shots = local_path(shots)
    if prepare:
        shots.mkdir(parents=True, exist_ok=True)
        cams = browser_cameras(paths, views)
        (shots / "cameras.json").write_text(json.dumps(cams, indent=1))
        console.print(f"Wrote {len(cams)} cameras to {shots / 'cameras.json'}")
        return
    report = browser_comparison(paths, shots)
    console.print(f"[green]Browser against gsplat[/green]: {report['browser_vs_gsplat']} "
                  f"over {report['views']} views at {report['canvas']}")


@app.command()
def align(
    project: str = typer.Option(..., "--project", "-p"),
    scale_tolerance: float = typer.Option(
        0.0, min=0.0, max=0.05, help="ARKit captures: let scale move this far (0.01 = 1%) "
        "to fit the RoomPlan walls. 0 keeps ARKit's metres exactly."),
) -> None:
    """Level the scene with the floor at y = 0, and fit it to the RoomPlan scan in metres."""
    paths = _paths(project)
    metric = _stray(paths) is not None
    with prov.stage_run(paths, "align", {"scale_tolerance": scale_tolerance}) as run:
        roomplan_file = roomplan_mod.find_export(paths.input / "roomplan")
        run.inputs["roomplan"] = prov.fingerprint(roomplan_file)
        ingest_record = prov.load_record(paths, "ingest") or {}
        recorded = (ingest_record.get("inputs") or {}).get("roomplan")
        if (recorded or {}).get("sha256") != (run.inputs["roomplan"] or {}).get("sha256"):
            raise prov.StaleInput(
                "The RoomPlan scan changed since `ingest`; rerun ingest with --roomplan (or "
                "--keep-roomplan) so the capture and its scan are recorded together.")
        try:
            report = align_scene(paths, project, metric=metric, scale_tolerance=scale_tolerance)
        except AlignmentFailed as exc:
            raise _fail(str(exc)) from exc
        run.metrics.update({"scale": report.scale, "scale_from": report.scale_from,
                            "up_from": report.up_from, **{f"floor_{k}": v for k, v in
                                                          report.floor.items()
                                                          if not isinstance(v, list | dict)}})
        if report.roomplan:
            run.metrics.update({f"plan_{k}": v for k, v in report.roomplan.items()
                                if not isinstance(v, list | dict)})
    console.print(
        f"[green]Aligned[/green] {report.splats:,} splats. Up from {report.up_from}; floor from "
        f"{report.floor.get('from')} covers {report.floor_inlier_ratio:.0%} of the area under "
        f"the cameras; scale {report.scale:.4f} from {report.scale_from}."
    )
    if report.roomplan:
        rp = report.roomplan
        console.print(
            f"  RoomPlan fit: {rp['inlier_ratio']:.0%} of wall points within 5 cm, median "
            f"{rp['median_error_mm']} mm, p90 {rp['p90_error_mm']} mm; "
            f"{rp['wall_coverage']:.0%} of the plan's wall length seen"
        )
    for warning in report.warnings:
        console.print(f"[yellow]{warning}[/yellow]")
    console.print("  Not published yet: `pipeline export` checks and publishes it.")


@app.command()
def export(
    project: str = typer.Option(..., "--project", "-p"),
    max_splats: int = typer.Option(1_500_000, min=10_000, max=6_000_000),
    crop: bool = typer.Option(True, help="Drop what lies beyond the walls (window views)"),
    crop_margin: float = typer.Option(0.75, min=0.0, max=10.0,
                                      help="Metres kept outside the walls when cropping"),
    sh_degree: int = typer.Option(3, min=0, max=3, help="Lower to shrink the download"),
) -> None:
    """Crop, mask, split each room's floor out, compress to SPZ, check, then publish."""
    paths = _paths(project)
    settings = {"max_splats": max_splats, "crop_margin": crop_margin if crop else None,
                "sh_degree": sh_degree}
    with prov.stage_run(paths, "export", settings) as run:
        try:
            report = export_scene(paths, max_splats, crop_margin if crop else None, sh_degree)
        except AlignmentFailed as exc:
            raise _fail(str(exc)) from exc
        run.metrics.update({"splats": report.splats, "download_mb": report.download_mb,
                            "floor_splats": report.floor_splats, "files_mb": report.files_mb})
        run.outputs.update({name: prov.fingerprint(paths.scene / name)
                            for name in report.files_mb})
    files = ", ".join(f"{k} {v} MB" for k, v in report.files_mb.items())
    console.print(
        f"[green]Exported[/green] {report.splats:,} splats (cropped {report.cropped_out:,}, "
        f"floor {report.floor_splats:,}, SH {report.sh_degree}): {files}. "
        f"Viewer download {report.download_mb} MB. Every file read back and checked."
    )
    for warning in report.warnings:
        console.print(f"[yellow]{warning}[/yellow]")


@app.command()
def check(project: str = typer.Option(..., "--project", "-p")) -> None:
    """
    Check a capture before training: format, sync, intrinsics, poses,
    coverage. Pictures and report.json in work/preflight/.
    """
    from .check import preflight

    paths = _paths(project)
    report = preflight(paths)
    console.print(f"[green]Checked[/green] the {report['capture']} capture; pictures and "
                  f"report in {(paths.work / 'preflight').relative_to(paths.root)}")
    for warning in report.get("warnings", []):
        console.print(f"[yellow]{warning}[/yellow]")
    if not report.get("warnings"):
        console.print("  No warnings.")


@app.command()
def verify(project: str = typer.Option(..., "--project", "-p")) -> None:
    """
    Check our splat code against PlayCanvas's splat-transform: every
    published SPZ read by it, and SH rotation against its own.
    """
    from .verify import verify_published, verify_sh_rotation

    paths = _paths(project)
    report = {"published": verify_published(paths), "sh_rotation": verify_sh_rotation()}
    prov.atomic_write_text(paths.work / "verify_report.json", json.dumps(report, indent=2))
    problems = [f"{name}: {p}" for group in report.values() for name, r in group.items()
                for p in r.get("problems", [])]
    for name, r in report["published"].items():
        console.print(f"  {name}: {r['splats']:,} splats, positions within "
                      f"{r['position_m'] * 1000:.3f} mm, SH within {r.get('sh', 0):.2g}")
    for axis, r in report["sh_rotation"].items():
        console.print(f"  rotation about {axis}: centres {r['position_m']:.2g} m, "
                      f"SH {r.get('sh', 0):.2g}, |q.q'| >= {r['rotation_dot_min']:.6f}")
    if problems:
        for p in problems:
            console.print(f"[red]{p}[/red]")
        raise typer.Exit(1)
    console.print("[green]splat-transform agrees with every file and every rotation.[/green]")


@app.command(name="rerun")
def rerun_cmd(
    project: str = typer.Option(..., "--project", "-p"),
    every: int = typer.Option(3, min=1, max=100, help="Log every Nth frame"),
) -> None:
    """
    Save a local Rerun recording of the capture (frames, poses, points, splat,
    plan walls) to work/diagnostics/. Needs the `diagnostics` extra.
    """
    try:
        import rerun  # noqa: F401

        from .rerun_export import export_recording
    except ImportError as exc:
        raise ToolMissing("rerun-sdk is not installed: `uv sync --extra diagnostics` "
                          "(or --extra pipeline --extra diagnostics on the pipeline machine)."
                          ) from exc
    paths = _paths(project)
    out = paths.work / "diagnostics" / f"{project}.rrd"
    summary = export_recording(paths, out, every=every)
    console.print(f"[green]Saved[/green] {out.relative_to(paths.root)}: {summary}. "
                  f"Open it with `rerun {out}`.")


@app.command(name="floor")
def floor_cmd(
    project: str = typer.Option(..., "--project", "-p"),
) -> None:
    """Segmentation-based floor detection (M3). `export` already splits it geometrically."""
    raise ToolMissing(
        "Segmentation-based floor detection needs a model, which per docs/BRIEF.md §M3 "
        "is shortlisted and confirmed before adding. A scanned room does not need it: "
        "`pipeline export` splits the floor out geometrically from the aligned plane "
        "and the room outline."
    )


def _catalog(kind: str) -> list[dict]:
    folder = repo_root() / "catalog" / kind
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("*.json"))]


@app.command()
def rebuild(
    project: str = typer.Option(..., "--project", "-p"),
    roomplan: Path | None = typer.Option(
        None, help="Floor-plan scan to rebuild from (.glb, .json, .usdz); else the manifest's"
    ),
    floor: str = typer.Option("oak-plank-natural", help="Catalog floor id, unless dressed"),
    wall_colour: str | None = typer.Option(None, help="Wall paint as #rrggbb (default: dressing)"),
    lightmap_size: int = typer.Option(2048, help="Baked-light atlas edge in pixels"),
    samples: int = typer.Option(2048, help="Cycles samples for the bake"),
    preview_only: bool = typer.Option(False, help="Only render a still, to judge the look"),
) -> None:
    """Rebuild the flat 1:1 from its floor-plan scan in Blender, with baked light."""
    paths = _paths(project)
    paths.ensure()
    manifest = (
        Manifest.load(paths.manifest) if paths.manifest.is_file()
        else Manifest(id=project, name=project)
    )
    dressing_file = paths.input / "dressing.json"
    dressing = (
        json.loads(dressing_file.read_text(encoding="utf-8")) if dressing_file.is_file() else {}
    )
    scan_file = _existing(roomplan, "Floor-plan scan") or roomplan_mod.find_export(
        paths.input / "roomplan"
    )
    scan = None
    if scan_file is not None:
        # A balcony scanned with the flat is left out (`dropZones`).
        loaded = roomplan_mod.without(roomplan_mod.load(scan_file), dressing.get("dropZones", []))
        room, items, floor_y = roomplan_mod.to_rooms(loaded)
        # What the scan took for furniture and the dressing does not (`drop`),
        # and what stands outside the flat (on a dropped balcony).
        dressed = dressing.get("items", {})
        items = [i for i in items if not dressed.get(i.id, {}).get("drop")
                 and flat_geometry.point_in_polygon(i.box.center[0], i.box.center[2],
                                                    room.floorPolygon)]
        manifest.rooms, manifest.items = [room], items
        scan = {
            "ceilings": flat_geometry.merge_ceilings(roomplan_mod.ceilings(loaded, floor_y)),
            "partitions": roomplan_mod.partitions(loaded, np.array(room.floorPolygon), floor_y),
            "zones": [(label, poly.tolist()) for label, poly in roomplan_mod.floor_zones(loaded)],
        }
        if dressing.get("plan"):  # the architect's floor plan over the scan's walls
            rebuild_mod.apply_plan(room, scan, dressing["plan"])
        rebuild_mod.add_walls(room, scan, dressing.get("partitions", []))
        rebuild_mod.add_openings(room, scan, dressing.get("openings", []))
        if dressing.get("widen"):
            rebuild_mod.widen(room, items, scan, **dressing["widen"])
        # The viewer keeps a standing person's head under a slope.
        room.ceilings = [[tuple(p) for p in piece] for piece in scan["ceilings"]]
        if dressing.get("hero"):
            # Open where the preview stands: the view chosen to show the flat.
            (ex, _, ez), (tx, _, tz) = dressing["hero"]["eye"], dressing["hero"]["target"]
            room.waypoint = Waypoint(
                position=(ex, roomplan_mod.EYE_HEIGHT, ez),
                yaw=round(math.degrees(math.atan2(-(tx - ex), -(tz - ez))) % 360, 1),
            )
    if not manifest.rooms:
        raise typer.BadParameter("No rooms: pass --roomplan with a Polycam Floorplan export.")

    catalog = {f["id"]: f for f in _catalog("floors")}
    if floor not in catalog:
        raise typer.BadParameter(f"Unknown floor {floor!r}. Known: {', '.join(sorted(catalog))}")
    floors = {"main": rebuild_mod.catalog_floor(catalog[floor])}
    for name, entry in dressing.get("floors", {}).items():
        if "catalog" in entry:  # where the photos give no floor to re-lay
            floors[name] = rebuild_mod.catalog_floor(catalog[entry["catalog"]])
            console.print(f"  floor {name}: {entry['catalog']}")
            continue
        floors[name] = rebuild_mod.photo_floor(paths, name, entry)
        source = f"re-laid from {entry['photo']}" if "photo" in entry else "generated"
        console.print(f"  floor {name}: {source}, {floors[name]['colour']}")
    spec = rebuild_mod.scene_spec(
        paths, manifest, floors, _catalog("furniture"),
        wall_colour or dressing.get("wallColour", "#eeeae3"), lightmap_size, samples,
        scan=scan, dressing=dressing,
    )
    finished = sum(1 for i in spec["items"] if i["finish"])
    stand_ins = sum(1 for i in spec["items"] if not i["own"] and i["model"])
    blocks = sum(1 for i in spec["items"] if not i["model"] and not i["finish"])
    console.print(
        f"Rebuilding {len(manifest.rooms)} room(s), {len(spec['items'])} pieces "
        f"({finished} drawn from their finish, {stand_ins} catalog stand-ins, {blocks} blocks), "
        f"{len(spec['skylights'])} roof window(s) ..."
    )
    if spec["sun"]:
        console.print(f"  sun at bearing {spec['sun']['bearing']}, "
                      f"{spec['sun']['elevation']} degrees up")
    started = time.time()
    try:
        rebuild_mod.run(spec, paths.work / "rebuild_spec.json", preview_only)
    except rebuild_mod.BlenderMissing as exc:
        raise ToolMissing(str(exc)) from exc
    console.print(f"  preview: {spec['out']['preview']}")
    if not preview_only:
        manifest.assets.model = Path(spec["out"]["glb"]).name
        meta = json.loads(Path(spec["out"]["meta"]).read_text())
        manifest.assets.lightmap = meta["lightmap"]
        manifest.assets.lightmapScale = meta["lightmapScale"]
        manifest.assets.lighting = [Lighting(**t) for t in meta["lighting"] if t["time"]]
        manifest.assets.sunPath = [SunSample(**t) for t in meta.get("sunPath", [])]
        manifest.rooms[0].obstacles = [Obstacle(**o) for o in meta.get("obstacles", [])]
        _outside(paths, manifest, spec, dressing)
        if scan is not None:
            manifest.rooms[0] = rebuild_mod.with_partitions(manifest.rooms[0], scan["partitions"])
        (paths.work / SCANNED_ROOM).write_text(manifest.rooms[0].model_dump_json(indent=1))
        room_split.split(manifest, manifest.rooms[0], dressing.get("rooms", []))
        _listing(paths, manifest, dressing)
        manifest.write(paths.manifest)
    console.print(f"[green]Rebuilt[/green] in {(time.time() - started) / 60:.1f} min")


def _outside(paths: ProjectPaths, manifest: Manifest, spec: dict, dressing: dict) -> None:
    result = outside_mod.build(paths, spec, dressing, [t.time for t in manifest.assets.lighting])
    outside_mod.apply(manifest, result)
    for info in result["views"]:
        console.print(f"  view from a photo: {info}")
    console.print(f"  {len(result['skies'])} skies")


#: The scan's one room, as the rebuild left it, before `rooms` cuts it up.
SCANNED_ROOM = "scanned_room.json"


def _listing(paths: ProjectPaths, manifest: Manifest, dressing: dict) -> None:
    """
    The dressing's `listing` (level, bedrooms, bathrooms; the area is the
    rooms' unless it gives one) and its `hero` as the entry view, unless the
    editor has set one; the still is the editor's (scene/hero.webp), if any.
    """
    listing = dressing.get("listing") or {}
    area = sum(r.areaM2 or 0.0 for r in manifest.rooms)
    manifest.listing = Listing(**{"areaM2": round(area) if area else None, **listing})
    if dressing.get("hero") and not (manifest.heroView and manifest.heroView.image):
        (ex, ey, ez), (tx, ty, tz) = dressing["hero"]["eye"], dressing["hero"]["target"]
        manifest.heroView = HeroView(
            position=(ex, roomplan_mod.EYE_HEIGHT, ez),
            yaw=round(math.degrees(math.atan2(-(tx - ex), -(tz - ez))) % 360, 1),
            pitch=round(math.degrees(math.atan2(ty - ey, math.hypot(tx - ex, tz - ez))), 1),
            image="hero.webp" if (paths.scene / "hero.webp").is_file() else None,
        )


@app.command()
def rooms(project: str = typer.Option(..., "--project", "-p")) -> None:
    """
    Cut the rebuilt flat into the rooms the dressing names, and take its
    listing and entry view, without baking it again.
    """
    paths = _paths(project)
    manifest = Manifest.load(paths.manifest)
    scanned = paths.work / SCANNED_ROOM
    if not scanned.is_file():
        if len(manifest.rooms) != 1:
            raise typer.BadParameter(f"No {SCANNED_ROOM}: rebuild the flat first.")
        scanned.write_text(manifest.rooms[0].model_dump_json(indent=1))
    dressing = json.loads((paths.input / "dressing.json").read_text(encoding="utf-8"))
    room_split.split(manifest, Room.model_validate_json(scanned.read_text()),
                     dressing.get("rooms", []))
    _listing(paths, manifest, dressing)
    manifest.write(paths.manifest)
    for r in manifest.rooms:
        console.print(f"  {r.name}: {len(r.walls)} walls, {len(r.openings)} openings, "
                      f"{len(r.slots)} slots")


@app.command()
def outside(project: str = typer.Option(..., "--project", "-p")) -> None:
    """The view out of the windows and the skies again, without baking the flat again."""
    paths = _paths(project)
    manifest = Manifest.load(paths.manifest)
    dressing = json.loads((paths.input / "dressing.json").read_text(encoding="utf-8"))
    spec = json.loads((paths.work / "rebuild_spec.json").read_text(encoding="utf-8"))
    _outside(paths, manifest, spec, dressing)
    manifest.write(paths.manifest)


@app.command(name="run-all")
def run_all(
    capture: Path = typer.Argument(
        ..., help="A video (.mov/.mp4) or a Stray Scanner dataset folder; Windows paths are fine"
    ),
    project: str = typer.Option(..., "--project", "-p"),
    roomplan: Path | None = typer.Option(None, help="RoomPlan export (.json, .usdz or .glb)"),
    keep_roomplan: bool = typer.Option(False, help="Reuse the project's existing scan"),
    fps: float = typer.Option(3.0, min=0.5, max=15.0),
    long_edge: int = typer.Option(1600, min=480, max=4096),
    steps: int = typer.Option(30_000, min=200, max=200_000),
    max_splats: int = typer.Option(1_500_000, min=50_000, max=6_000_000),
    sh_degree: int = typer.Option(3, min=0, max=3),
    crop_margin: float = typer.Option(0.75, min=0.0, max=10.0),
) -> None:
    """ingest -> frames -> poses -> train -> align -> export."""
    ingest(capture=capture, project=project, roomplan=roomplan, keep_roomplan=keep_roomplan)
    frames(project=project, fps=fps, min_frames=150, max_frames=600, long_edge=long_edge)
    poses(project=project, matcher="auto", camera_model="OPENCV",
          max_track=stray_mod.MAX_TRACK, min_frame_obs=stray_mod.MIN_FRAME_OBS)
    train(project=project, max_splats=max_splats, steps=steps, holdout_every=8,
          sh_degree=sh_degree, depth="auto", depth_lambda=1e-2, pose_opt="auto",
          pose_opt_lr=1e-5, pose_opt_reg=1e-6, antialiased=False, app_opt=False, data_factor=1,
          experiment=None, displaced=None)
    align(project=project, scale_tolerance=0.0)
    export(project=project, max_splats=max_splats, crop=True, crop_margin=crop_margin,
           sh_degree=sh_degree)
    console.print(f"[green]Done.[/green] Open http://localhost:5173/p/{project}")


@app.command()
def info(project: str = typer.Option(..., "--project", "-p")) -> None:
    """Show what exists for a project, whether each stage is current, and its report."""
    paths = _paths(project)
    rows = [
        ("input video", next((p for p in paths.input.glob("video.*")), None)),
        ("stray capture", stray_mod.find_capture(paths.input / "stray")),
        ("roomplan", roomplan_mod.find_export(paths.input / "roomplan")),
        ("frames", paths.work / "frames"),
        ("poses", paths.work / "undistorted"),
        ("trained", trained_ply(paths)),
        ("aligned", paths.work / "aligned" / "scene.ply"),
        ("published", paths.scene / "scene.spz"),
    ]
    for label, path in rows:
        mark = "[green]yes[/green]" if path is not None and path.exists() else "[dim]no[/dim]"
        console.print(f"  {label:<14} {mark}")
    for stage in prov.UPSTREAM:
        record = prov.load_record(paths, stage)
        if record is None:
            console.print(f"  [dim]{stage}: no run record[/dim]")
            continue
        stale = prov.freshness(paths, stage)
        state = "[green]current[/green]" if stale is None else f"[yellow]stale: {stale}[/yellow]"
        console.print(f"  {stage}: run {record['run_id']}, {state}")
    for name in ("frames", "poses", "train", "align", "export"):
        report = paths.work / f"{name}_report.json"
        if report.is_file():
            console.print(f"  [dim]{name}:[/dim] {report.read_text().strip()}")


def main() -> None:  # pragma: no cover - entry point
    try:
        app()
    except (ToolMissing, train_mod.TrainerMissing, roomplan_mod.RoomPlanError,
            prov.StaleInput, stray_mod.StrayError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(2) from exc


if __name__ == "__main__":  # pragma: no cover
    main()
