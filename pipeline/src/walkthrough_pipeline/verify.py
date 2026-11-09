"""
`pipeline verify`: our splat code checked against an implementation we did
not write — PlayCanvas's splat-transform (MIT), pinned below.

Two checks:

* every published SPZ is read by splat-transform and written back as PLY,
  and must hold what our own reader says it holds — count, positions,
  colours, opacities, scales, orientations and spherical harmonics. That
  covers our SPZ writer's quantisation and layout, which the viewer's
  loader (Spark) would otherwise be the first to disagree with;
* a cloud with random degree-3 harmonics is rotated about each axis by
  splat-transform and by `SplatCloud.transformed`; centres, orientations
  and every SH band must agree, so a scene levelled and turned by `align`
  keeps its view-dependent colour pointing the right way.

Runs with Node (npx); nothing leaves the machine.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from .manifest import Manifest
from .paths import ProjectPaths
from .splat import SplatCloud, read_ply, read_spz, write_ply

SPLAT_TRANSFORM = "@playcanvas/splat-transform@3.10.3"


class VerifyFailed(RuntimeError):
    pass


def _windows_path(arg: str) -> str:
    """A WSL path as Windows' Node sees it (`wslpath -w`), for npx run through interop."""
    if not (arg.startswith("/") and (Path(arg).exists() or Path(arg).parent.is_dir())):
        return arg
    return subprocess.run(["wslpath", "-w", arg], capture_output=True, text=True,
                          check=True).stdout.strip()


def splat_transform(*args: str) -> str:
    npx = shutil.which("npx") or shutil.which("npx.cmd")
    if npx is None:
        raise VerifyFailed("npx (Node) is not on PATH; splat-transform needs it.")
    if npx.startswith("/mnt/"):  # Windows' Node from inside WSL: Windows paths
        args = tuple(_windows_path(a) for a in args)
    done = subprocess.run([npx, "-y", SPLAT_TRANSFORM, "-q", "-w", *args],
                          capture_output=True, text=True, timeout=1800)
    if done.returncode:
        raise VerifyFailed(f"splat-transform {' '.join(args)} failed: {done.stderr[-2000:]}")
    return done.stdout


def compare(ours: SplatCloud, theirs: SplatCloud) -> dict:
    """Largest disagreements between two clouds of the same splats in the same order."""
    if len(ours) != len(theirs):
        raise VerifyFailed(f"{len(ours)} splats against {len(theirs)}")
    if len(ours) == 0:
        return {"splats": 0}
    q1 = ours.rotations / np.linalg.norm(ours.rotations, axis=1, keepdims=True)
    q2 = theirs.rotations / np.linalg.norm(theirs.rotations, axis=1, keepdims=True)
    out = {
        "splats": len(ours),
        "position_m": float(np.abs(ours.positions - theirs.positions).max()),
        "colour": float(np.abs(ours.colours - theirs.colours).max()),
        "opacity": float(np.abs(ours.opacities - theirs.opacities).max()),
        "log_scale": float(np.abs(np.log(ours.scales) - np.log(theirs.scales)).max()),
        "rotation_dot_min": float(np.abs((q1 * q2).sum(axis=1)).min()),
        "sh_degree": [ours.sh_degree, theirs.sh_degree],
    }
    if ours.sh_rest is not None and theirs.sh_rest is not None:
        out["sh"] = float(np.abs(ours.sh_rest - theirs.sh_rest).max())
    return out


#: What "the same" allows: float32 and 8-bit opacity/colour round trips.
TOLERANCE = {"position_m": 1e-4, "colour": 0.02, "opacity": 0.01, "log_scale": 1e-3,
             "sh": 1e-3}


def _within(diff: dict, tol: dict) -> list[str]:
    bad = [f"{k} off by {diff[k]:.3g} (allowed {v})" for k, v in tol.items()
           if k in diff and diff[k] > v]
    if diff.get("rotation_dot_min", 1.0) < 0.9999:
        bad.append(f"orientations differ (|q.q'| down to {diff['rotation_dot_min']:.5f})")
    if diff.get("sh_degree", [0, 0])[0] != diff.get("sh_degree", [0, 0])[1]:
        bad.append(f"SH degree {diff['sh_degree']}")
    return bad


def verify_published(paths: ProjectPaths) -> dict:
    manifest = Manifest.load(paths.manifest)
    names = [n for n in {manifest.assets.scene, manifest.assets.sceneNoFloor,
                         manifest.assets.floor, *manifest.assets.floors.values()}
             if n and n.endswith(".spz")]
    out: dict = {}
    with tempfile.TemporaryDirectory() as tmp:
        for name in sorted(names):
            ply = Path(tmp) / f"{Path(name).stem}.ply"
            splat_transform(str(paths.scene / name), str(ply))
            diff = compare(read_spz(paths.scene / name), read_ply(ply))
            stats = json.loads(splat_transform(str(paths.scene / name), "--stats", "json",
                                               "null") or "{}")
            diff["problems"] = _within(diff, {**TOLERANCE, "position_m": 1e-6})
            diff["splat_transform_stats_keys"] = sorted(stats)[:10]
            out[name] = diff
    return out


def verify_sh_rotation(n: int = 3000, seed: int = 0) -> dict:
    """Rotate random degree-3 splats about each axis, ours against splat-transform's."""
    from .align import _axis_angle

    rng = np.random.default_rng(seed)
    q = rng.normal(size=(n, 4))
    cloud = SplatCloud(
        positions=rng.uniform(-2, 2, (n, 3)).astype(np.float32),
        colours=rng.uniform(0.1, 0.9, (n, 3)).astype(np.float32),
        opacities=rng.uniform(0.2, 0.95, n).astype(np.float32),
        scales=np.exp(rng.uniform(-5, -1, (n, 3))).astype(np.float32),
        rotations=(q / np.linalg.norm(q, axis=1, keepdims=True)).astype(np.float32),
        sh_rest=rng.normal(0, 0.2, (n, 15, 3)).astype(np.float32),
    )
    out = {}
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "in.ply"
        write_ply(cloud, src)
        for axis, euler in (("x", "30,0,0"), ("y", "0,47,0"), ("z", "0,0,-63")):
            dst = Path(tmp) / f"out_{axis}.ply"
            splat_transform(str(src), "-r", euler, str(dst))
            angle = np.radians(float(next(v for v in euler.split(",") if v != "0")))
            # splat-transform rotates in PlayCanvas's frame, which is the PLY's
            # turned 180° about Z (x and y negated): a turn about X or Y there
            # is the opposite turn here. Measured: with it, centres agree
            # exactly and SH to 1e-7; without it, by up to 2 m.
            if axis in "xy":
                angle = -angle
            unit = {"x": [1.0, 0, 0], "y": [0, 1.0, 0], "z": [0, 0, 1.0]}[axis]
            m = np.eye(4)
            m[:3, :3] = _axis_angle(np.array(unit), angle)
            diff = compare(cloud.transformed(m), read_ply(dst))
            diff["problems"] = _within(diff, TOLERANCE)
            out[axis] = diff
    return out
