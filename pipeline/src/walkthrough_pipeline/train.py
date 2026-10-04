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
        "--data-factor", "1",
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
        *(["--depth-loss"] if settings.depth else []),
    ]


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
