"""
`pipeline train`: gsplat's reference trainer (examples/simple_trainer.py at
the pinned gsplat tag), run in its own venv.

The trainer is used as published rather than reimplemented: it is what
gsplat's quality numbers come from. It lives outside the project venv
because it pins numpy<2, a CUDA build of torch and a pycolmap fork that
would clash with the API's dependencies. docs/PIPELINE.md has the setup.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

#: Where docs/PIPELINE.md's setup puts them; override with the env vars.
DEFAULT_PYTHON = Path.home() / ".venvs" / "gsplat" / "bin" / "python"
DEFAULT_EXAMPLES = Path.home() / "gsplat" / "examples"


class TrainerMissing(RuntimeError):
    pass


@dataclass
class TrainSettings:
    max_splats: int = 1_500_000
    steps: int = 30_000
    holdout_every: int = 8
    sh_degree: int = 3
    #: Supervise rendered depth with the dataset's 3D points (LiDAR).
    depth: bool = False
    depth_lambda: float = 1e-2  # gsplat's default
    #: Optimise camera poses alongside the splat (for sensor-given poses).
    refine_poses: bool = False
    pose_opt_lr: float = 1e-5  # gsplat's defaults
    pose_opt_reg: float = 1e-6
    antialiased: bool = False
    #: Per-image appearance embeddings (absorb exposure and white-balance
    #: changes between frames). gsplat bakes them into plain colours on
    #: export, so the PLY loses all view-dependent shading (SH degree 0).
    app_opt: bool = False
    #: Image downscale factor the trainer applies (1 = frames as extracted).
    data_factor: int = 1


@dataclass
class TrainReport:
    steps: int
    splats: int
    psnr: float | None
    ssim: float | None
    lpips: float | None
    holdout_frames: str
    peak_vram_gb: float | None
    minutes: float


def trainer_paths() -> tuple[Path, Path]:
    python = Path(os.environ.get("GSPLAT_PYTHON", DEFAULT_PYTHON))
    examples = Path(os.environ.get("GSPLAT_EXAMPLES", DEFAULT_EXAMPLES))
    if not python.is_file() or not (examples / "simple_trainer.py").is_file():
        raise TrainerMissing(
            f"gsplat's trainer was not found (python: {python}, examples: {examples}). "
            "Set GSPLAT_PYTHON / GSPLAT_EXAMPLES or follow docs/PIPELINE.md setup."
        )
    return python, examples


def command(data_dir: Path, result_dir: Path, settings: TrainSettings) -> list[str]:
    """
    The trainer invocation. MCMC densification because it takes a hard cap on
    the Gaussian count — the 8 GB VRAM budget and the download size both need
    one — and it leaves fewer floaters than the default strategy.

    World-space normalisation is off so the result stays in COLMAP's frame,
    where `align` can read gravity from the camera poses.
    """
    python, examples = trainer_paths()
    last = str(settings.steps)
    return [
        str(python), str(examples / "simple_trainer.py"), "mcmc",
        "--data-dir", str(data_dir),
        "--data-factor", str(settings.data_factor),
        "--result-dir", str(result_dir),
        "--test-every", str(settings.holdout_every),
        "--max-steps", last,
        "--eval-steps", last,
        "--save-steps", last,
        "--ply-steps", last,
        "--save-ply",
        "--disable-viewer",
        "--disable-video",
        "--no-normalize-world-space",
        "--sh-degree", str(settings.sh_degree),
        "--strategy.cap-max", str(settings.max_splats),
        "--packed",
        *(["--depth-loss", "--depth-lambda", str(settings.depth_lambda)]
          if settings.depth else []),
        *(["--pose-opt", "--pose-opt-lr", str(settings.pose_opt_lr),
            "--pose-opt-reg", str(settings.pose_opt_reg)] if settings.refine_poses else []),
        *(["--antialiased"] if settings.antialiased else []),
        *(["--app-opt"] if settings.app_opt else []),
    ]


#: simple_trainer.py seeds every run with 42 + rank (not a CLI option at the
#: pinned tag), so a rerun with the same data and settings is the same run,
#: up to CUDA's non-deterministic atomics.
TRAINER_SEED = 42


def trainer_versions() -> dict:
    """gsplat, torch and CUDA in the trainer's venv, the examples' git tag, the GPU."""
    import subprocess

    python, examples = trainer_paths()
    out: dict = {"trainer_seed": TRAINER_SEED}
    probe = ("import json, gsplat, torch; print(json.dumps({'gsplat': gsplat.__version__, "
             "'torch': torch.__version__, 'cuda': torch.version.cuda, "
             "'gpu': torch.cuda.get_device_name(0) if torch.cuda.is_available() else None}))")
    try:
        out.update(json.loads(subprocess.run([str(python), "-c", probe], capture_output=True,
                                             text=True, check=True, timeout=120).stdout))
        out["gsplat_examples"] = subprocess.run(
            ["git", "-C", str(examples), "describe", "--tags", "--always", "--dirty"],
            capture_output=True, text=True, timeout=20).stdout.strip() or None
        # The setup script patches the trainer (a NaN guard on rendered depth,
        # scripts/setup_pipeline_wsl.sh); the record names exactly what differs.
        diff = subprocess.run(["git", "-C", str(examples), "diff"], capture_output=True,
                              timeout=20).stdout
        if diff:
            import hashlib

            out["gsplat_examples_patch"] = {
                "sha256": hashlib.sha256(diff).hexdigest()[:16],
                "files": subprocess.run(["git", "-C", str(examples), "diff", "--name-only"],
                                        capture_output=True, text=True,
                                        timeout=20).stdout.split(),
                "from": "scripts/setup_pipeline_wsl.sh",
            }
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        out["error"] = str(exc)
    return out


def _rotation_6d(d6: np.ndarray) -> np.ndarray:
    """gsplat's (pytorch3d's) 6D rotation: Gram-Schmidt rows."""
    a1, a2 = d6[:, :3], d6[:, 3:]
    b1 = a1 / np.linalg.norm(a1, axis=1, keepdims=True)
    b2 = a2 - (b1 * a2).sum(axis=1, keepdims=True) * b1
    b2 /= np.linalg.norm(b2, axis=1, keepdims=True)
    return np.stack([b1, b2, np.cross(b1, b2)], axis=1)


def refined_poses(cam_to_world: np.ndarray, embeds: np.ndarray) -> np.ndarray:
    """
    The trainer's corrected camera-to-world poses: CameraOptModule applies
    c2w @ [R(6D delta + identity) | dx] to each training image.
    """
    delta = np.tile(np.eye(4), (len(embeds), 1, 1))
    delta[:, :3, :3] = _rotation_6d(embeds[:, 3:] + np.array([1.0, 0, 0, 0, 1.0, 0]))
    delta[:, :3, 3] = embeds[:, :3]
    return cam_to_world @ delta


def export_refined_poses(data_dir: Path, result_dir: Path, settings: TrainSettings) -> dict:
    """
    Read the pose corrections the trainer learned (its checkpoint's
    `pose_adjust`), apply them to the training images' poses, and write
    them beside the result: refined_poses.json, and a COLMAP model of the
    refined cameras (sparse_refined/) for rendering the training views as
    the splat saw them. Held-out images keep their poses. Returns how far
    the poses moved.
    """
    import subprocess

    from .colmap import read_images_bin, write_images_bin

    python, _ = trainer_paths()
    ckpt = result_dir / "ckpts" / f"ckpt_{settings.steps - 1}_rank0.pt"
    dump = result_dir / "pose_adjust.npy"
    subprocess.run([str(python), "-c",
                    "import sys, numpy, torch; ck = torch.load(sys.argv[1], map_location='cpu', "
                    "weights_only=False); numpy.save(sys.argv[2], "
                    "ck['pose_adjust']['embeds.weight'].numpy())", str(ckpt), str(dump)],
                   check=True, timeout=600)
    embeds = np.load(dump).astype(np.float64)
    cameras = sorted(read_images_bin(data_dir / "sparse" / "images.bin"), key=lambda c: c.name)
    # gsplat's parser sorts by name and holds out every Nth; the rest are
    # the training set, in that order, which is the embedding's index.
    train = [c for i, c in enumerate(cameras) if i % settings.holdout_every != 0]
    if len(train) != len(embeds):
        raise RuntimeError(f"{len(embeds)} pose corrections for {len(train)} training images")
    c2w = np.tile(np.eye(4), (len(train), 1, 1))
    for i, c in enumerate(train):
        c2w[i, :3, :3] = c.rotation.T
        c2w[i, :3, 3] = c.centre
    fixed = refined_poses(c2w, embeds)
    moved_cm = np.linalg.norm(fixed[:, :3, 3] - c2w[:, :3, 3], axis=1) * 100
    rel = np.einsum("nji,njk->nik", c2w[:, :3, :3], fixed[:, :3, :3])
    turned = np.degrees(np.arccos(np.clip((np.trace(rel, axis1=1, axis2=2) - 1) / 2, -1, 1)))
    refined = {c.name: f.tolist() for c, f in zip(train, fixed, strict=True)}
    (result_dir / "refined_poses.json").write_text(json.dumps(refined))
    write_images_bin(data_dir / "sparse" / "images.bin", result_dir / "sparse_refined",
                     {name: np.array(m) for name, m in refined.items()})
    return {
        "images": len(train),
        "moved_cm": {"median": round(float(np.median(moved_cm)), 2),
                     "p95": round(float(np.percentile(moved_cm, 95)), 2),
                     "max": round(float(moved_cm.max()), 2)},
        "turned_deg": {"median": round(float(np.median(turned)), 3),
                       "p95": round(float(np.percentile(turned, 95)), 3),
                       "max": round(float(turned.max()), 3)},
        "worst": [train[i].name for i in np.argsort(-moved_cm)[:5]],
    }


def collect(
    result_dir: Path, settings: TrainSettings, minutes: float, out_ply: Path
) -> TrainReport:
    """Copy the final PLY out of the trainer's folder and read its numbers."""
    step = settings.steps - 1
    ply = result_dir / "ply" / f"point_cloud_{step}.ply"
    if not ply.is_file():
        raise RuntimeError(f"Training finished without writing {ply}")
    out_ply.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ply, out_ply)

    stats_dir = result_dir / "stats"
    val = _read(stats_dir / f"val_step{step:04d}.json")
    train = _read(stats_dir / f"train_step{step:04d}_rank0.json")
    return TrainReport(
        steps=settings.steps,
        splats=int(val.get("num_GS") or train.get("num_GS") or 0),
        psnr=_round(val.get("psnr"), 2),
        ssim=_round(val.get("ssim"), 4),
        lpips=_round(val.get("lpips"), 4),
        holdout_frames=f"every {settings.holdout_every}th",
        peak_vram_gb=_round(train.get("mem"), 2),
        minutes=round(minutes, 1),
    )


def _read(path: Path) -> dict:
    return json.loads(path.read_text()) if path.is_file() else {}


def _round(value, digits: int) -> float | None:
    return None if value is None else round(float(value), digits)


def write_report(report: TrainReport, path: Path) -> None:
    path.write_text(json.dumps(asdict(report), indent=2))
