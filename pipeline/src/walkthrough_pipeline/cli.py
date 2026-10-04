"""
`pipeline` CLI: video (+ optional RoomPlan scan) -> scene (M1).

Runs on the Linux/CUDA machine (WSL2 here; docs/PIPELINE.md). Each stage
reads what the previous one wrote under data/projects/<id>/, so any stage can
be rerun on its own. Stages that cannot run say exactly why rather than
pretending — docs/BRIEF.md §2.6.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

import typer
from rich.console import Console

from . import frames as frames_mod
from . import roomplan as roomplan_mod
from . import train as train_mod
from .manifest import Manifest
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


def _fail(message: str) -> typer.Exit:
    console.print(f"[red]{message}[/red]")
    return typer.Exit(1)


@app.command()
def ingest(
    video: Path = typer.Argument(..., help="The capture (.mov/.mp4); Windows paths are fine"),
    project: str = typer.Option(..., "--project", "-p", help="Project id, e.g. flat-01"),
    roomplan: Path | None = typer.Option(
        None, help="RoomPlan export: a .json or .usdz file, or a folder holding one"
    ),
) -> None:
    """Copy a capture into the project layout and check it before hours go into it."""
    video = _existing(video, "Video")
    roomplan = _existing(roomplan, "RoomPlan export")
    paths = _paths(project)
    paths.ensure()
    for stale in paths.input.glob("video.*"):
        stale.unlink()
    target = paths.input / f"video{video.suffix.lower()}"
    shutil.copy2(video, target)
    console.print(f"[green]Ingested[/green] {video.name} -> {target.relative_to(repo_root())}")
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
            raise typer.BadParameter(f"No .json or .usdz RoomPlan export in {roomplan}")
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
    fps: float = typer.Option(3.0, help="Extraction rate"),
    min_frames: int = typer.Option(150),
    max_frames: int = typer.Option(600),
    long_edge: int = typer.Option(1600, help="Frames are scaled to this long edge (8 GB VRAM)"),
) -> None:
    """Extract frames with ffmpeg and drop blurry ones."""
    paths = _paths(project)
    video = next((p for p in paths.input.glob("video.*")), None)
    if video is None:
        raise typer.BadParameter(f"No video in {paths.input}. Run `pipeline ingest` first.")

    raw_dir = paths.work / "frames_raw"
    console.print(f"Extracting frames at {fps} fps, long edge {long_edge} px ...")
    extracted = frames_mod.extract_frames(video, raw_dir, fps=fps, long_edge=long_edge)
    console.print(f"  {len(extracted)} frames decoded")

    scores = frames_mod.score_directory(raw_dir)
    selection = frames_mod.select_frames(scores, min_frames=min_frames, max_frames=max_frames)
    console.print(f"  {selection.summary}")

    out_dir = paths.work / "frames"
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    for path in selection.keep:
        shutil.copy2(path, out_dir / path.name)

    (paths.work / "frames_report.json").write_text(
        json.dumps(
            {
                "decoded": len(extracted),
                "kept": len(selection.keep),
                "dropped_blurry": len(selection.dropped_blurry),
                "dropped_to_target": len(selection.dropped_to_target),
                "blur_threshold": selection.threshold,
                "fps": fps,
                "long_edge": long_edge,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    console.print(f"[green]{len(selection.keep)} frames ready[/green] in {out_dir}")


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
    """Estimate camera poses with COLMAP (pycolmap, GPU SIFT), then undistort."""
    pycolmap = _pycolmap()
    paths = _paths(project)
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
        max_splats=max_splats, steps=steps, holdout_every=holdout_every
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
        report = align_scene(_paths(project), project)
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


@app.command(name="run-all")
def run_all(
    video: Path = typer.Argument(..., help="The capture (.mov/.mp4); Windows paths are fine"),
    project: str = typer.Option(..., "--project", "-p"),
    roomplan: Path | None = typer.Option(None, help="RoomPlan export (JSON or USDZ)"),
    fps: float = typer.Option(3.0),
) -> None:
    """ingest -> frames -> poses -> train -> align -> export."""
    ingest(video=video, project=project, roomplan=roomplan)
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
