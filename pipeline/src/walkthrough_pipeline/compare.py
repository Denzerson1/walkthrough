"""
`pipeline compare`: the same views at every stage, side by side and scored,
to find where a defect first appears —

    source frame -> trained PLY -> aligned PLY -> exported SPZ

each rendered with gsplat from the same camera (carried through `align`'s
transform for the later stages), at the source's resolution, and scored
against the source frame. The views are the trainer's held-out frames
(every Nth: nearby-view interpolation) and, when one was trained, an
experiment's held-out stretch of the walk (`--displaced`: views genuinely
away from the training cameras).

The browser stage is compared separately (`compare_browser`): Spark draws
an upright camera, so it is matched against gsplat's render of the
exported SPZ from that same upright camera.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

import cv2
import numpy as np

from .colmap import read_images_bin
from .manifest import Manifest
from .paths import ProjectPaths, repo_root
from .rerun_export import read_cameras_bin
from .scene import aligned_dir, trained_ply
from .splat import read_spz, write_ply
from .train import trainer_paths

RENDER_TOOL = repo_root() / "pipeline" / "gsplat_tools" / "render_eval.py"


def _similarity(t: np.ndarray, c2w: np.ndarray) -> np.ndarray:
    """A camera carried through a similarity transform: same view of the moved scene."""
    s = np.cbrt(np.linalg.det(t[:3, :3]))
    out = np.eye(4)
    out[:3, :3] = (t[:3, :3] / s) @ c2w[:3, :3]
    out[:3, 3] = t[:3, :3] @ c2w[:3, 3] + t[:3, 3]
    return out


def dataset_cameras(data_dir: Path, refined: dict | None = None) -> list[dict]:
    """Every image of a posed dataset as a render job's camera (OpenCV c2w)."""
    intr = read_cameras_bin(data_dir / "sparse" / "cameras.bin")
    cams = []
    for cam in sorted(read_images_bin(data_dir / "sparse" / "images.bin"), key=lambda c: c.name):
        _, w, h, params = intr.get(cam.camera_id, next(iter(intr.values())))
        c2w = np.eye(4)
        c2w[:3, :3] = cam.rotation.T
        c2w[:3, 3] = cam.centre
        if refined and cam.name in refined:
            c2w = np.array(refined[cam.name])
        cams.append({"name": cam.name, "width": int(w), "height": int(h),
                     "K": [float(v) for v in params[:4]], "c2w": c2w})
    return cams


def render(ply: Path, cameras: list[dict], out_dir: Path, images_dir: Path | None,
           transform: np.ndarray | None = None) -> dict:
    """Run the gsplat render tool on `ply` from `cameras` (moved by `transform`)."""
    job = {
        "ply": str(ply), "out_dir": str(out_dir),
        "images_dir": str(images_dir) if images_dir else None,
        "cameras": [{**c, "c2w": (_similarity(transform, c["c2w"]) if transform is not None
                                  else c["c2w"]).tolist()} for c in cameras],
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    job_file = out_dir / "job.json"
    job_file.write_text(json.dumps(job))
    python, _ = trainer_paths()
    subprocess.run([str(python), str(RENDER_TOOL), str(job_file)], check=True,
                   env={**os.environ, "PYTHONWARNINGS": "ignore"})
    return json.loads((out_dir / "metrics.json").read_text())


def stage_comparison(paths: ProjectPaths, label: str, views: int = 8,
                     experiment: str | None = None, holdout_every: int = 8) -> dict:
    data = paths.work / "undistorted"
    train_dir = paths.work / ("train" if experiment is None else f"experiments/{experiment}")
    refined_file = train_dir / "refined_poses.json"
    refined = json.loads(refined_file.read_text()) if refined_file.is_file() else None
    every = dataset_cameras(data, refined)
    held = [c for i, c in enumerate(every) if i % holdout_every == 0]
    displaced_file = train_dir / "displaced.json"
    if displaced_file.is_file():  # an experiment trained without a stretch of the walk
        names = set(json.loads(displaced_file.read_text()))
        held = [c for c in every if c["name"] in names]
    pick = [held[i] for i in np.linspace(0, len(held) - 1, min(views, len(held)))
            .round().astype(int)]
    out = paths.work / "compare" / label
    ply = train_dir / "trained.ply" if experiment else trained_ply(paths)
    stages: dict[str, dict] = {"trained": render(ply, pick, out / "trained", data / "images")}
    if experiment is None and (aligned_dir(paths) / "scene.ply").is_file():
        t = np.array(Manifest.load(aligned_dir(paths) / "manifest.json").transform)
        stages["aligned"] = render(aligned_dir(paths) / "scene.ply", pick, out / "aligned",
                                   data / "images", t)
    if experiment is None and paths.manifest.is_file():
        manifest = Manifest.load(paths.manifest)
        if manifest.assets.scene and manifest.assets.scene.endswith(".spz"):
            t = np.array(manifest.transform)
            with tempfile.TemporaryDirectory() as tmp:
                ply_out = Path(tmp) / "exported.ply"
                write_ply(read_spz(paths.scene / manifest.assets.scene), ply_out)
                stages["exported"] = render(ply_out, pick, out / "exported", data / "images", t)
    grid(pick, data / "images", out, list(stages), stages, out / "grid.jpg")
    report = {"label": label, "views": [c["name"] for c in pick],
              "set": "displaced stretch" if displaced_file.is_file() else
              f"held out, every {holdout_every}th",
              # Held-out views are never refined (the trainer only optimises
              # the poses it trains on), so they are scored from the given pose.
              "poses": ("as given (held-out views are not pose-refined)"
                        if not displaced_file.is_file() else "as given"),
              "stages": {k: v["mean"] for k, v in stages.items()}}
    (out / "report.json").write_text(json.dumps(report, indent=2))
    return report


def grid(cams: list[dict], images: Path, out: Path, stages: list[str], metrics: dict,
         path: Path, width: int = 360, source_label: str = "source") -> None:
    """Rows of views; columns: the source frame, then each stage's render, scored."""
    rows = []
    for cam in cams:
        stem = Path(cam["name"]).stem
        tiles = []
        for col in ["source", *stages]:
            src = images / cam["name"] if col == "source" else out / col / f"{stem}.png"
            image = cv2.imread(str(src))
            if image is None:
                image = np.zeros((cam["height"], cam["width"], 3), np.uint8)
            tile = cv2.resize(image, (width, int(width * image.shape[0] / image.shape[1])),
                              interpolation=cv2.INTER_AREA)
            text = source_label if col == "source" else (
                f"{col} {metrics[col]['per_image'].get(cam['name'], {}).get('psnr', '-')} dB")
            cv2.putText(tile, text, (6, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 3)
            cv2.putText(tile, text, (6, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)
            tiles.append(tile)
        h = max(t.shape[0] for t in tiles)
        rows.append(np.hstack([cv2.copyMakeBorder(t, 0, h - t.shape[0], 0, 2,
                                                  cv2.BORDER_CONSTANT) for t in tiles]))
    wmax = max(r.shape[1] for r in rows)
    sheet = np.vstack([cv2.copyMakeBorder(r, 0, 2, 0, wmax - r.shape[1], cv2.BORDER_CONSTANT)
                       for r in rows])
    cv2.imwrite(str(path), sheet, [cv2.IMWRITE_JPEG_QUALITY, 88])


def browser_cameras(paths: ProjectPaths, views: int = 6, holdout_every: int = 8,
                    fov: float = 60.0) -> list[dict]:
    """
    Upright viewer cameras where the held-out photos were taken: the
    camera's place in the published scene, its heading and pitch, no roll.
    """
    manifest = Manifest.load(paths.manifest)
    t = np.array(manifest.transform)
    every = dataset_cameras(paths.work / "undistorted")
    held = [c for i, c in enumerate(every) if i % holdout_every == 0]
    pick = [held[i] for i in np.linspace(0, len(held) - 1, min(views, len(held)))
            .round().astype(int)]
    out = []
    for cam in pick:
        moved = _similarity(t, cam["c2w"])
        f = moved[:3, 2]
        out.append({"name": Path(cam["name"]).stem,
                    "position": [round(float(v), 4) for v in moved[:3, 3]],
                    "yaw": round(float(np.degrees(np.arctan2(-f[0], -f[2]))), 3),
                    "pitch": round(float(np.degrees(np.arcsin(np.clip(f[1], -1, 1)))), 3),
                    "fov": fov})
    return out


def browser_comparison(paths: ProjectPaths, shots: Path) -> dict:
    """
    The viewer's canvas (scripts/browser-compare.mjs) against gsplat's render
    of the same exported SPZ from the same upright camera: what the browser
    stage alone changes.
    """
    cams = json.loads((shots / "cameras.json").read_text())
    size = json.loads((shots / "size.json").read_text())
    manifest = Manifest.load(paths.manifest)
    jobs = [{**upright_camera(c["position"], c["yaw"], c["pitch"], size["fov"],
                              size["width"], size["height"]), "name": f"{c['name']}.png"}
            for c in cams]
    with tempfile.TemporaryDirectory() as tmp:
        ply_out = Path(tmp) / "exported.ply"
        write_ply(read_spz(paths.scene / manifest.assets.scene), ply_out)
        metrics = render(ply_out, jobs, shots / "gsplat", shots)
    grid(jobs, shots, shots, ["gsplat"], {"gsplat": metrics}, shots / "grid.jpg",
         source_label="browser (Spark)")
    report = {"views": len(jobs), "canvas": [size["width"], size["height"]], "fov": size["fov"],
              "browser_errors": size.get("errors", []),
              "browser_vs_gsplat": metrics["mean"]}
    (shots / "report.json").write_text(json.dumps(report, indent=2))
    return report


def upright_camera(position, yaw_deg: float, pitch_deg: float, fov_deg: float,
                   width: int, height: int) -> dict:
    """The viewer's camera (lib/camera.ts: yaw 0 looks down -Z, no roll) as OpenCV c2w + K."""
    yaw, pitch = np.radians(yaw_deg), np.radians(pitch_deg)
    forward = np.array([-np.sin(yaw) * np.cos(pitch), np.sin(pitch), -np.cos(yaw) * np.cos(pitch)])
    right = np.cross(forward, [0.0, 1.0, 0.0])
    right /= np.linalg.norm(right)
    down = np.cross(forward, right)
    c2w = np.eye(4)
    c2w[:3, :3] = np.column_stack([right, down, forward])
    c2w[:3, 3] = position
    f = (height / 2) / np.tan(np.radians(fov_deg) / 2)
    return {"width": width, "height": height, "K": [f, f, width / 2, height / 2], "c2w": c2w}
