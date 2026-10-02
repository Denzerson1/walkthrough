"""
`pipeline` CLI: video -> scene (M1).

Stages that need a GPU (poses, train) shell out to GLOMAP/COLMAP and gsplat.
They raise a clear, actionable error when the tool is missing rather than
pretending to work — docs/BRIEF.md §2.6.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import typer
from rich.console import Console

from . import frames as frames_mod
from .manifest import Manifest
from .paths import ProjectPaths, repo_root

app = typer.Typer(help="walkthrough capture pipeline: video -> scene", no_args_is_help=True)
console = Console()


class ToolMissing(RuntimeError):
    """A required external binary is not installed."""


def require_tool(name: str, hint: str) -> str:
    exe = shutil.which(name)
    if not exe:
        raise ToolMissing(f"`{name}` is not on PATH. {hint}")
    return exe


def _paths(project: str) -> ProjectPaths:
    return ProjectPaths.for_project(repo_root(), project)


@app.command()
def ingest(
    video: Path = typer.Argument(..., exists=True, readable=True),
    project: str = typer.Option(..., "--project", "-p", help="Project id, e.g. demo-01"),
    roomplan: Path | None = typer.Option(None, help="Optional RoomPlan export (USDZ or JSON)"),
) -> None:
    """Copy a capture into the project layout."""
    paths = _paths(project)
    paths.input.mkdir(parents=True, exist_ok=True)
    target = paths.input / f"video{video.suffix.lower()}"
    shutil.copy2(video, target)
    console.print(f"[green]Ingested[/green] {video.name} -> {target.relative_to(repo_root())}")

    if roomplan is not None:
        dest = paths.input / "roomplan"
        dest.mkdir(parents=True, exist_ok=True)
        if roomplan.is_dir():
            shutil.copytree(roomplan, dest, dirs_exist_ok=True)
        else:
            shutil.copy2(roomplan, dest / roomplan.name)
        console.print(f"[green]Ingested RoomPlan[/green] -> {dest.relative_to(repo_root())}")

    if not paths.manifest.exists():
        Manifest(id=project, name=project).write(paths.manifest)
        console.print(f"Created a starter manifest at {paths.manifest.relative_to(repo_root())}")


@app.command()
def frames(
    project: str = typer.Option(..., "--project", "-p"),
    fps: float = typer.Option(3.0, help="Extraction rate"),
    min_frames: int = typer.Option(150),
    max_frames: int = typer.Option(600),
) -> None:
    """Extract frames with ffmpeg and drop blurry ones."""
    paths = _paths(project)
    video = next((p for p in paths.input.glob("video.*")), None)
    if video is None:
        raise typer.BadParameter(f"No video in {paths.input}. Run `pipeline ingest` first.")

    raw_dir = paths.work / "frames_raw"
    console.print(f"Extracting frames at {fps} fps ...")
    extracted = frames_mod.extract_frames(video, raw_dir, fps=fps)
    console.print(f"  {len(extracted)} frames decoded")

    scores = frames_mod.score_directory(raw_dir)
    selection = frames_mod.select_frames(scores, min_frames=min_frames, max_frames=max_frames)
    console.print(f"  {selection.summary}")

    out_dir = paths.work / "frames"
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.glob("*.png"):
        stale.unlink()
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
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    console.print(f"[green]{len(selection.keep)} frames ready[/green] in {out_dir}")


@app.command()
def poses(
    project: str = typer.Option(..., "--project", "-p"),
    matcher: str = typer.Option("sequential", help="sequential | exhaustive"),
) -> None:
    """
    Estimate camera poses with GLOMAP, falling back to COLMAP.

    Uses the OPENCV_FISHEYE camera model, which suits the iPhone ultra-wide
    0.5x lens the capture guide specifies.
    """
    paths = _paths(project)
    frame_dir = paths.work / "frames"
    if not frame_dir.is_dir() or not any(frame_dir.glob("*.png")):
        raise typer.BadParameter("No frames. Run `pipeline frames` first.")

    colmap = shutil.which("colmap")
    glomap = shutil.which("glomap")
    if colmap is None:
        raise ToolMissing(
            "`colmap` is not on PATH. It is required for feature extraction even when "
            "GLOMAP does the mapping. See docs/PIPELINE.md."
        )

    db = paths.work / "colmap.db"
    sparse = paths.work / "sparse"
    sparse.mkdir(parents=True, exist_ok=True)

    console.print("Extracting features ...")
    subprocess.run(
        [colmap, "feature_extractor",
         "--database_path", str(db),
         "--image_path", str(frame_dir),
         "--ImageReader.camera_model", "OPENCV_FISHEYE",
         "--ImageReader.single_camera", "1"],
        check=True,
    )
    console.print(f"Matching ({matcher}) ...")
    subprocess.run(
        [colmap, f"{matcher}_matcher", "--database_path", str(db)],
        check=True,
    )

    if glomap:
        console.print("Mapping with GLOMAP ...")
        subprocess.run(
            [glomap, "mapper",
             "--database_path", str(db),
             "--image_path", str(frame_dir),
             "--output_path", str(sparse)],
            check=True,
        )
    else:
        console.print("[yellow]GLOMAP not found; using COLMAP mapper[/yellow]")
        subprocess.run(
            [colmap, "mapper",
             "--database_path", str(db),
             "--image_path", str(frame_dir),
             "--output_path", str(sparse)],
            check=True,
        )

    total = len(list(frame_dir.glob("*.png")))
    registered = _count_registered(sparse)
    share = registered / total if total else 0.0
    console.print(
        f"[green]Registered {registered}/{total} frames ({share:.0%})[/green]"
    )
    if share < 0.7:
        console.print(
            "[yellow]Below 70%. The capture likely moved too fast or the room is "
            "textureless. See docs/PIPELINE.md 'known failure modes'.[/yellow]"
        )


def _count_registered(sparse_dir: Path) -> int:
    images_bin = next(sparse_dir.rglob("images.bin"), None)
    images_txt = next(sparse_dir.rglob("images.txt"), None)
    if images_txt is not None:
        lines = [
            ln for ln in images_txt.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.startswith("#")
        ]
        return len(lines) // 2
    if images_bin is not None:
        import struct

        with images_bin.open("rb") as fh:
            return struct.unpack("<Q", fh.read(8))[0]
    return 0


@app.command()
def train(
    project: str = typer.Option(..., "--project", "-p"),
    max_splats: int = typer.Option(
        1_500_000, help="Cap for the 8 GB VRAM budget (docs/SPEC.md §2)"
    ),
    long_edge: int = typer.Option(1600, help="Downsample frames to this long edge"),
    holdout: float = typer.Option(0.1, help="Fraction of frames held out for PSNR"),
) -> None:
    """Train the Gaussian splat scene with gsplat (Apache-2.0)."""
    paths = _paths(project)
    if not (paths.work / "sparse").is_dir():
        raise typer.BadParameter("No poses. Run `pipeline poses` first.")
    try:
        import gsplat  # noqa: F401
    except ImportError as exc:
        raise ToolMissing(
            "gsplat is not installed. It needs CUDA and is not a default dependency "
            "because the API and web app must install on machines without a GPU. "
            "Install inside WSL2 with `uv pip install gsplat`. See docs/PIPELINE.md."
        ) from exc

    raise ToolMissing(
        "Training is wired up but has never been executed: this machine has no NVIDIA "
        "GPU (see docs/PROGRESS.md). Run this inside WSL2 on the 2080 Super. "
        f"Settings that would be used: max_splats={max_splats}, long_edge={long_edge}, "
        f"holdout={holdout}."
    )


@app.command()
def align(
    project: str = typer.Option(..., "--project", "-p"),
    scale_ref: float | None = typer.Option(
        None, "--scale-ref", help="Real distance in metres between the two reference points"
    ),
) -> None:
    """Gravity-align the scene and set metric scale."""
    import numpy as np

    from .align import apply_transform, compose, fit_plane_ransac, gravity_alignment
    from .splat import read_ply, write_ply

    paths = _paths(project)
    ply = paths.scene / "scene.ply"
    if not ply.is_file():
        raise typer.BadParameter(f"No scene at {ply}. Run `pipeline train` first.")

    cloud = read_ply(ply)
    console.print(f"Loaded {len(cloud):,} splats")

    # The floor is the dominant near-horizontal plane in the lower part of the
    # scene. "Lower" and "horizontal" both assume the scene is already roughly
    # Y-up, which is true for our synthetic scenes but NOT for a raw COLMAP
    # world frame — so try the Y-up assumption first and fall back to an
    # unconstrained fit rather than silently returning the identity.
    low = cloud.positions[:, 1] < np.percentile(cloud.positions[:, 1], 35)
    fit = fit_plane_ransac(
        cloud.positions[low].astype(np.float64),
        threshold=0.02,
        up_hint=np.array([0.0, 1.0, 0.0]),
    )
    if fit.inlier_ratio < 0.05:
        console.print(
            "[yellow]No near-horizontal plane found. The reconstruction is probably "
            "not Y-up; refitting without an up hint.[/yellow]"
        )
        fit = fit_plane_ransac(cloud.positions.astype(np.float64), threshold=0.02)

    if fit.inlier_ratio < 0.05 or not fit.inliers.any():
        raise ToolMissing(
            "Could not find a floor plane in this scene. Alignment would have "
            "silently written an unaligned scene, so nothing was changed. Check "
            "that the reconstruction succeeded and that the floor was filmed."
        )
    console.print(f"Floor plane {fit.plane} with {fit.inlier_ratio:.0%} inliers")

    transform = gravity_alignment(fit.plane, cloud.positions)
    if scale_ref is not None:
        console.print(f"[yellow]--scale-ref given ({scale_ref} m) but the two reference "
                      "points must be marked in the editor first.[/yellow]")
    aligned = cloud.transformed(transform)
    write_ply(aligned, ply)

    manifest = Manifest.load(paths.manifest) if paths.manifest.is_file() else Manifest(
        id=project, name=project
    )
    manifest.transform = compose(np.asarray(manifest.transform), transform).tolist()
    manifest.floor = {"plane": [0.0, 1.0, 0.0, 0.0]}
    manifest.write(paths.manifest)

    inlier_points = (
        cloud.positions[low][fit.inliers]
        if fit.inliers.shape[0] == int(low.sum())
        else cloud.positions[fit.inliers]
    )
    residual = float(np.abs(apply_transform(inlier_points, transform)[:, 1]).mean())
    console.print(f"[green]Aligned.[/green] Mean floor residual {residual * 1000:.1f} mm")


@app.command()
def export(
    project: str = typer.Option(..., "--project", "-p"),
    max_splats: int = typer.Option(1_500_000),
) -> None:
    """Cap the splat count and compress to SPZ, keeping the PLY."""
    from .splat import SpzUnavailable, cap_splat_count, read_ply, write_ply, write_spz

    paths = _paths(project)
    ply = paths.scene / "scene.ply"
    if not ply.is_file():
        raise typer.BadParameter(f"No scene at {ply}.")

    cloud = read_ply(ply)
    before = len(cloud)
    cloud = cap_splat_count(cloud, max_splats)
    if len(cloud) != before:
        console.print(f"Capped {before:,} -> {len(cloud):,} splats")
        write_ply(cloud, ply)

    spz = paths.scene / "scene.spz"
    try:
        write_spz(ply, spz)
        size_mb = spz.stat().st_size / 1e6
        console.print(f"[green]Wrote {spz.name}[/green] ({size_mb:.1f} MB)")
        if size_mb > 60:
            console.print("[yellow]Over the 60 MB download target (brief M2).[/yellow]")
    except SpzUnavailable as exc:
        console.print(f"[yellow]{exc}[/yellow]")

    manifest = Manifest.load(paths.manifest)
    manifest.assets.scene = "scene.spz" if spz.exists() else "scene.ply"
    manifest.write(paths.manifest)


@app.command(name="floor")
def floor_cmd(
    project: str = typer.Option(..., "--project", "-p"),
) -> None:
    """Detect the floor, split it out and build per-room shading maps (M3)."""
    raise ToolMissing(
        "Floor detection needs a semantic segmentation model with a cleared licence. "
        "Per docs/BRIEF.md §M3 we must shortlist 2-3 candidates and confirm before "
        "adding one — this has not happened yet. The geometry half (voting, plane "
        "refinement, concave hull, shading maps) is implemented and tested in "
        "walkthrough_pipeline.floor."
    )


@app.command(name="run-all")
def run_all(
    video: Path = typer.Argument(..., exists=True),
    project: str = typer.Option(..., "--project", "-p"),
    fps: float = typer.Option(3.0),
) -> None:
    """ingest -> frames -> poses -> train -> align -> export."""
    ingest(video=video, project=project, roomplan=None)
    frames(project=project, fps=fps, min_frames=150, max_frames=600)
    poses(project=project, matcher="sequential")
    train(project=project, max_splats=1_500_000, long_edge=1600, holdout=0.1)
    align(project=project, scale_ref=None)
    export(project=project, max_splats=1_500_000)


@app.command()
def info(project: str = typer.Option(..., "--project", "-p")) -> None:
    """Show what exists for a project."""
    paths = _paths(project)
    rows = [
        ("input video", next((p for p in paths.input.glob("video.*")), None)),
        ("roomplan", paths.input / "roomplan" if (paths.input / "roomplan").exists() else None),
        ("frames", paths.work / "frames" if (paths.work / "frames").exists() else None),
        ("poses", paths.work / "sparse" if (paths.work / "sparse").exists() else None),
        ("scene.ply", paths.scene / "scene.ply" if (paths.scene / "scene.ply").exists() else None),
        ("scene.spz", paths.scene / "scene.spz" if (paths.scene / "scene.spz").exists() else None),
        ("manifest", paths.manifest if paths.manifest.exists() else None),
    ]
    for label, path in rows:
        mark = "[green]yes[/green]" if path else "[dim]no[/dim]"
        console.print(f"  {label:<12} {mark}")


def main() -> None:  # pragma: no cover - entry point
    try:
        app()
    except ToolMissing as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(2) from exc


if __name__ == "__main__":  # pragma: no cover
    main()
