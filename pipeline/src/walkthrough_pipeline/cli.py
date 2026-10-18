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
from pathlib import Path

import numpy as np
import typer
from rich.console import Console

from . import flat_geometry
from . import frames as frames_mod
from . import rebuild as rebuild_mod
from . import roomplan as roomplan_mod
from . import stray as stray_mod
from . import train as train_mod
from .manifest import Manifest, Obstacle, Waypoint
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


@app.command()
def ingest(
    capture: Path = typer.Argument(
        ..., help="A video (.mov/.mp4) or a Stray Scanner dataset folder; Windows paths are fine"
    ),
    project: str = typer.Option(..., "--project", "-p", help="Project id, e.g. flat-01"),
    roomplan: Path | None = typer.Option(
        None, help="RoomPlan export: .json, .usdz or Polycam .glb, or a folder holding one"
    ),
) -> None:
    """Copy a capture into the project layout and check it before hours go into it."""
    capture = _existing(capture, "Capture")
    roomplan = _existing(roomplan, "RoomPlan export")
    paths = _paths(project)
    paths.ensure()
    for stale in paths.input.glob("video.*"):
        stale.unlink()
    shutil.rmtree(paths.input / "stray", ignore_errors=True)

    if capture.is_dir():
        if not stray_mod.is_capture(capture):
            raise typer.BadParameter(f"{capture} is a folder but not a Stray Scanner dataset.")
        shutil.copytree(capture, paths.input / "stray")
        loaded = stray_mod.load(paths.input / "stray")
        console.print(
            f"[green]Ingested Stray Scanner capture[/green]: {len(loaded)} posed frames, "
            f"video {loaded.size[0]}x{loaded.size[1]}"
        )
    else:
        target = paths.input / f"video{capture.suffix.lower()}"
        shutil.copy2(capture, target)
        console.print(
            f"[green]Ingested[/green] {capture.name} -> {target.relative_to(repo_root())}"
        )
        for warning in frames_mod.check_video(target):
            console.print(f"[yellow]{warning}[/yellow]")

    if roomplan is not None:
        dest = paths.input / "roomplan"
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True)
        if roomplan.is_dir():
            shutil.copytree(roomplan, dest, dirs_exist_ok=True)
        else:
            shutil.copy2(roomplan, dest / roomplan.name)
        found = roomplan_mod.find_export(dest)
        if found is None:
            raise typer.BadParameter(f"No RoomPlan export (.json, .usdz, .glb) in {roomplan}")
        # Parsed now: a scan that cannot be read should fail here, not after
        # an hour of training.
        scan = roomplan_mod.load(found)
        room, items, _ = roomplan_mod.to_rooms(scan)
        console.print(
            f"[green]RoomPlan[/green] {found.name}: {len(scan.of('walls'))} walls, "
            f"{len(room.openings)} doors/windows, {len(items)} furniture boxes, "
            f"{_area(room.floorPolygon):.1f} m², ceiling {room.walls[0].height:.2f} m"
        )

    if not paths.manifest.exists():
        Manifest(id=project, name=project).write(paths.manifest)
        console.print(f"Created a starter manifest at {paths.manifest.relative_to(repo_root())}")


def _area(poly: list[tuple[float, float]]) -> float:
    return 0.5 * abs(
        sum(poly[i][0] * poly[i - 1][1] - poly[i - 1][0] * poly[i][1] for i in range(len(poly)))
    )


@app.command()
def frames(
    project: str = typer.Option(..., "--project", "-p"),
    fps: float = typer.Option(3.0, help="Frames kept per second of footage"),
    min_frames: int = typer.Option(150),
    max_frames: int = typer.Option(600),
    long_edge: int = typer.Option(1600, help="Frames are scaled to this long edge (8 GB VRAM)"),
) -> None:
    """Extract frames with ffmpeg and drop blurry ones."""
    paths = _paths(project)
    out_dir = paths.work / "frames"
    capture = _stray(paths)
    if capture is not None:
        report = _frames_from_stray(capture, paths.work / "frames_raw", out_dir, fps, long_edge)
    else:
        video = next((p for p in paths.input.glob("video.*")), None)
        if video is None:
            raise typer.BadParameter(f"No capture in {paths.input}. Run `pipeline ingest` first.")
        report = _frames_from_video(
            video, paths.work / "frames_raw", out_dir, fps, min_frames, max_frames, long_edge
        )
    report.update(fps=fps, long_edge=long_edge)
    (paths.work / "frames_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    console.print(f"[green]{report['kept']} frames ready[/green] in {out_dir}")


def _replace_with(out_dir: Path, keep: list[Path]) -> None:
    shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True)
    for path in keep:
        shutil.copy2(path, out_dir / path.name)


def _frames_from_video(
    video: Path, raw_dir: Path, out_dir: Path,
    fps: float, min_frames: int, max_frames: int, long_edge: int,
) -> dict:
    console.print(f"Extracting frames at {fps} fps, long edge {long_edge} px ...")
    extracted = frames_mod.extract_frames(video, raw_dir, fps=fps, long_edge=long_edge)
    console.print(f"  {len(extracted)} frames decoded")
    selection = frames_mod.select_frames(
        frames_mod.score_directory(raw_dir), min_frames=min_frames, max_frames=max_frames
    )
    console.print(f"  {selection.summary}")
    _replace_with(out_dir, selection.keep)
    return {
        "source": "video",
        "decoded": len(extracted),
        "kept": len(selection.keep),
        "dropped_blurry": len(selection.dropped_blurry),
        "dropped_to_target": len(selection.dropped_to_target),
        "blur_threshold": selection.threshold,
    }


#: Candidates per kept frame when choosing the sharpest from a posed capture.
CANDIDATES_PER_FRAME = 4


def _frames_from_stray(
    capture: stray_mod.StrayCapture, raw_dir: Path, out_dir: Path, fps: float, long_edge: int
) -> dict:
    """
    The sharpest of every few consecutive frames, keeping each frame's
    number so it lines up with its ARKit pose and depth map.
    """
    video_fps = frames_mod.frame_rate(capture.video) or 60.0
    step = max(1, round(video_fps / (fps * CANDIDATES_PER_FRAME)))
    console.print(f"Decoding every {step}th frame, long edge {long_edge} px ...")
    candidates = frames_mod.extract_numbered(capture.video, raw_dir, step, long_edge)
    keep = frames_mod.sharpest_per_window(
        frames_mod.score_directory(raw_dir), CANDIDATES_PER_FRAME
    )
    console.print(f"  {len(candidates)} candidates, kept the sharpest {len(keep)}")
    _replace_with(out_dir, keep)
    return {"source": "stray", "decoded": len(candidates), "kept": len(keep), "step": step}


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
) -> None:
    """
    Camera poses for the trainer: from ARKit for a Stray capture, otherwise
    estimated with COLMAP (pycolmap, GPU SIFT) and undistorted.
    """
    paths = _paths(project)
    capture = _stray(paths)
    if capture is not None:
        _poses_from_stray(capture, paths)
        return
    pycolmap = _pycolmap()
    frame_dir = paths.work / "frames"
    names = sorted(p.name for p in frame_dir.glob("*.png")) if frame_dir.is_dir() else []
    if not names:
        raise typer.BadParameter("No frames. Run `pipeline frames` first.")

    db = paths.work / "colmap.db"
    sparse = paths.work / "sparse"
    undistorted = paths.work / "undistorted"
    db.unlink(missing_ok=True)
    for stale in (sparse, undistorted):
        shutil.rmtree(stale, ignore_errors=True)
    sparse.mkdir(parents=True)

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
    elif matcher == "sequential":
        pycolmap.match_sequential(db)
    else:
        raise typer.BadParameter(f"Unknown matcher {matcher!r}")

    console.print("Mapping ...")
    models = pycolmap.incremental_mapping(db, frame_dir, sparse)
    if not models:
        raise _fail("COLMAP registered no model. See docs/PIPELINE.md failure modes.")
    best_id, best = max(models.items(), key=lambda kv: kv[1].num_reg_images())
    registered = best.num_reg_images()
    share = registered / len(names)
    # The trainer reads sparse/0; COLMAP numbers models by discovery, not size.
    final = sparse / "best"
    final.mkdir()
    best.write(final)

    console.print("Undistorting ...")
    pycolmap.undistort_images(undistorted, final, frame_dir)

    report = {
        "frames": len(names),
        "registered": registered,
        "share": round(share, 3),
        "models": len(models),
        "model_used": int(best_id),
        "points3D": best.num_points3D(),
        "camera_model": camera_model,
        "matcher": matcher,
        "minutes": round((time.time() - started) / 60, 1),
    }
    (paths.work / "poses_report.json").write_text(json.dumps(report, indent=2))
    console.print(
        f"[green]Registered {registered}/{len(names)} frames ({share:.0%})[/green], "
        f"{best.num_points3D():,} points, {report['minutes']} min"
    )
    if len(models) > 1 and registered < len(names):
        console.print(
            f"[yellow]COLMAP split the capture into {len(models)} models; only the largest is "
            "used. Usually a doorway walked too fast.[/yellow]"
        )
    if share < 0.7:
        console.print(
            "[yellow]Below 70%. The capture likely moved too fast or the room is "
            "textureless. See docs/PIPELINE.md 'known failure modes'.[/yellow]"
        )


def _poses_from_stray(capture: stray_mod.StrayCapture, paths: ProjectPaths) -> None:
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
    built = stray_mod.build_dataset(capture, frame_paths, paths.work / "undistorted", offset)
    report = {
        "source": "stray (ARKit poses, LiDAR depth)",
        "frames": built.frames,
        "registered": built.frames,
        "share": 1.0,
        "points3D": built.points,
        "depth_observations": built.observations,
        "video_frame_offset": offset,
        "offset_agreement": round(agreement, 2),
        "image_size": [built.width, built.height],
        "minutes": round((time.time() - started) / 60, 1),
    }
    (paths.work / "poses_report.json").write_text(json.dumps(report, indent=2))
    console.print(
        f"[green]{built.frames} frames posed by ARKit[/green], {built.points:,} LiDAR points "
        f"seen {built.observations:,} times"
    )


@app.command()
def train(
    project: str = typer.Option(..., "--project", "-p"),
    max_splats: int = typer.Option(1_500_000, help="Gaussian cap (8 GB VRAM, download size)"),
    steps: int = typer.Option(30_000),
    holdout_every: int = typer.Option(8, help="Every Nth frame is held out to measure PSNR"),
) -> None:
    """Train the splat with gsplat (Apache-2.0), MCMC strategy, capped count."""
    paths = _paths(project)
    data = paths.work / "undistorted"
    if not (data / "sparse").is_dir():
        raise typer.BadParameter("No undistorted poses. Run `pipeline poses` first.")
    settings = train_mod.TrainSettings(
        max_splats=max_splats,
        steps=steps,
        holdout_every=holdout_every,
        # LiDAR points carry real depth, so supervise with it; COLMAP's sparse
        # points are too few and too noisy on plain walls to be worth it.
        # ARKit poses are good to a centimetre or so, not to a pixel, so they
        # are refined in training; COLMAP's already are, against these images.
        depth=_stray(paths) is not None,
        refine_poses=_stray(paths) is not None,
    )
    result_dir = paths.work / "train"
    shutil.rmtree(result_dir, ignore_errors=True)
    cmd = train_mod.command(data, result_dir, settings)

    console.print(f"Training {steps:,} steps, cap {max_splats:,} splats ...")
    started = time.time()
    # The example trainer imports its siblings, so it runs from its folder.
    subprocess.run(cmd, check=True, cwd=Path(cmd[1]).parent)
    report = train_mod.collect(
        result_dir, settings, (time.time() - started) / 60, trained_ply(paths)
    )
    train_mod.write_report(report, paths.work / "train_report.json")
    console.print(
        f"[green]Trained[/green] {report.splats:,} splats in {report.minutes} min. "
        f"Held-out PSNR {report.psnr} dB, SSIM {report.ssim}, LPIPS {report.lpips}, "
        f"peak VRAM {report.peak_vram_gb} GB"
    )


@app.command()
def align(project: str = typer.Option(..., "--project", "-p")) -> None:
    """Level the scene with the floor at y = 0, and fit it to the RoomPlan scan in metres."""
    try:
        paths = _paths(project)
        report = align_scene(paths, project, metric=_stray(paths) is not None)
    except AlignmentFailed as exc:
        raise _fail(str(exc)) from exc
    console.print(
        f"[green]Aligned[/green] {report.splats:,} splats. Up from {report.up_from}; floor fit "
        f"{report.floor_inlier_ratio:.0%}; scale {report.scale:.4f} from {report.scale_from}."
    )
    if report.roomplan:
        rp = report.roomplan
        console.print(
            f"  RoomPlan fit: {rp['inlier_ratio']:.0%} of wall points within 5 cm, "
            f"median {rp['median_error_mm']} mm"
        )
    for warning in report.warnings:
        console.print(f"[yellow]{warning}[/yellow]")


@app.command()
def export(
    project: str = typer.Option(..., "--project", "-p"),
    max_splats: int = typer.Option(1_500_000),
    crop: bool = typer.Option(True, help="Drop what lies beyond the walls (window views)"),
    crop_margin: float = typer.Option(0.75, help="Metres kept outside the walls when cropping"),
    sh_degree: int = typer.Option(3, min=0, max=3, help="Lower to shrink the download"),
) -> None:
    """Crop, split the floor out geometrically and compress to SPZ."""
    try:
        report = export_scene(
            _paths(project), max_splats, crop_margin if crop else None, sh_degree
        )
    except AlignmentFailed as exc:
        raise _fail(str(exc)) from exc
    files = ", ".join(f"{k} {v} MB" for k, v in report.files_mb.items())
    console.print(
        f"[green]Exported[/green] {report.splats:,} splats (cropped {report.cropped_out:,}, "
        f"floor {report.floor_splats:,}, SH {report.sh_degree}): {files}. "
        f"Viewer download {report.download_mb} MB."
    )
    for warning in report.warnings:
        console.print(f"[yellow]{warning}[/yellow]")


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
        loaded = roomplan_mod.load(scan_file)
        room, items, floor_y = roomplan_mod.to_rooms(loaded)
        manifest.rooms, manifest.items = [room], items
        scan = {
            "ceilings": flat_geometry.merge_ceilings(roomplan_mod.ceilings(loaded, floor_y)),
            "partitions": roomplan_mod.partitions(loaded, np.array(room.floorPolygon), floor_y),
            "zones": [(label, poly.tolist()) for label, poly in roomplan_mod.floor_zones(loaded)],
        }
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
        floors[name] = rebuild_mod.photo_floor(paths, name, entry)
        console.print(f"  floor {name}: re-laid from {entry['photo']}, {floors[name]['colour']}")
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
        manifest.assets.lightmap = Path(spec["out"]["lightmap"]).name
        meta = json.loads(Path(spec["out"]["meta"]).read_text())
        manifest.assets.lightmapScale = meta["lightmapScale"]
        manifest.rooms[0].obstacles = [Obstacle(**o) for o in meta.get("obstacles", [])]
        manifest.write(paths.manifest)
    console.print(f"[green]Rebuilt[/green] in {(time.time() - started) / 60:.1f} min")


@app.command(name="run-all")
def run_all(
    capture: Path = typer.Argument(
        ..., help="A video (.mov/.mp4) or a Stray Scanner dataset folder; Windows paths are fine"
    ),
    project: str = typer.Option(..., "--project", "-p"),
    roomplan: Path | None = typer.Option(None, help="RoomPlan export (.json, .usdz or .glb)"),
    fps: float = typer.Option(3.0),
) -> None:
    """ingest -> frames -> poses -> train -> align -> export."""
    ingest(capture=capture, project=project, roomplan=roomplan)
    frames(project=project, fps=fps, min_frames=150, max_frames=600, long_edge=1600)
    poses(project=project, matcher="auto", camera_model="OPENCV")
    train(project=project, max_splats=1_500_000, steps=30_000, holdout_every=8)
    align(project=project)
    export(project=project, max_splats=1_500_000, crop=True, crop_margin=0.75, sh_degree=3)
    console.print(f"[green]Done.[/green] Open http://localhost:5173/p/{project}")


@app.command()
def info(project: str = typer.Option(..., "--project", "-p")) -> None:
    """Show what exists for a project, and each stage's report."""
    paths = _paths(project)
    rows = [
        ("input video", next((p for p in paths.input.glob("video.*")), None)),
        ("stray capture", stray_mod.find_capture(paths.input / "stray")),
        ("roomplan", roomplan_mod.find_export(paths.input / "roomplan")),
        ("frames", paths.work / "frames"),
        ("poses", paths.work / "undistorted"),
        ("trained", trained_ply(paths)),
        ("aligned", paths.scene / "scene.ply"),
        ("exported", paths.scene / "scene.spz"),
    ]
    for label, path in rows:
        mark = "[green]yes[/green]" if path is not None and path.exists() else "[dim]no[/dim]"
        console.print(f"  {label:<12} {mark}")
    for name in ("frames", "poses", "train", "align", "export"):
        report = paths.work / f"{name}_report.json"
        if report.is_file():
            console.print(f"  [dim]{name}:[/dim] {report.read_text().strip()}")


def main() -> None:  # pragma: no cover - entry point
    try:
        app()
    except (ToolMissing, train_mod.TrainerMissing, roomplan_mod.RoomPlanError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(2) from exc


if __name__ == "__main__":  # pragma: no cover
    main()
