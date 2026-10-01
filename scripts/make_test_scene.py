"""
Generate a synthetic Gaussian-splat apartment for development (M0).

Why synthetic: docs/SPEC.md §2 — no licence risk, no download, reproducible,
and it exercises the whole render path before any real capture exists. It is
deliberately simple; real validation starts at M1.

Usage:  pnpm seed:testscene
        uv run python scripts/make_test_scene.py --project demo-01 --splats 220000
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline" / "src"))

from walkthrough_pipeline.manifest import (  # noqa: E402
    Manifest,
    ManifestItem,
    Opening,
    Room,
    Wall,
    Waypoint,
)
from walkthrough_pipeline.paths import ProjectPaths, repo_root  # noqa: E402
from walkthrough_pipeline.splat import (  # noqa: E402
    SplatCloud,
    SpzUnavailable,
    write_ply,
    write_spz,
)

RNG_SEED = 7

# Two rooms sharing a wall with a doorway, in metres.
LIVING = {"x0": 0.0, "z0": 0.0, "x1": 4.2, "z1": 3.8, "height": 2.6}
BEDROOM = {"x0": 4.2, "z0": 0.4, "x1": 7.4, "z1": 3.8, "height": 2.6}


def _scatter_plane(
    rng: np.random.Generator,
    n: int,
    x_range: tuple[float, float],
    z_range: tuple[float, float],
    y: float,
    jitter: float = 0.004,
) -> np.ndarray:
    x = rng.uniform(*x_range, n)
    z = rng.uniform(*z_range, n)
    yy = np.full(n, y) + rng.normal(0, jitter, n)
    return np.stack([x, yy, z], axis=1)


def _scatter_wall(
    rng: np.random.Generator,
    n: int,
    axis: str,
    fixed: float,
    span: tuple[float, float],
    height: float,
    jitter: float = 0.004,
) -> np.ndarray:
    t = rng.uniform(*span, n)
    y = rng.uniform(0.0, height, n)
    off = rng.normal(0, jitter, n)
    if axis == "x":  # wall runs along X at fixed Z
        return np.stack([t, y, np.full(n, fixed) + off], axis=1)
    return np.stack([np.full(n, fixed) + off, y, t], axis=1)


def _box(
    rng: np.random.Generator,
    n: int,
    centre: tuple[float, float, float],
    size: tuple[float, float, float],
) -> np.ndarray:
    """Points on the surface of a box — a crude stand-in for furniture."""
    cx, cy, cz = centre
    sx, sy, sz = size
    pts = rng.uniform(-0.5, 0.5, (n, 3)) * np.array([sx, sy, sz])
    # Push each point onto the nearest face so the box reads as a surface.
    axis = rng.integers(0, 3, n)
    sign = rng.choice([-0.5, 0.5], n)
    for a in range(3):
        sel = axis == a
        pts[sel, a] = sign[sel] * (sx, sy, sz)[a]
    return pts + np.array([cx, cy, cz])


def _wood_colour(rng: np.random.Generator, n: int) -> np.ndarray:
    base = np.array([0.52, 0.38, 0.24])
    grain = rng.normal(0, 0.045, (n, 1))
    return np.clip(base + grain, 0, 1)


def _wall_colour(rng: np.random.Generator, n: int) -> np.ndarray:
    base = np.array([0.88, 0.87, 0.84])
    return np.clip(base + rng.normal(0, 0.02, (n, 1)), 0, 1)


def _fabric_colour(rng: np.random.Generator, n: int, rgb: tuple[float, float, float]) -> np.ndarray:
    return np.clip(np.array(rgb) + rng.normal(0, 0.03, (n, 1)), 0, 1)


def build_cloud(total_splats: int) -> SplatCloud:
    rng = np.random.default_rng(RNG_SEED)

    # Budget split: floors and walls dominate, furniture adds landmarks.
    n_floor = int(total_splats * 0.32)
    n_wall = int(total_splats * 0.42)
    n_ceiling = int(total_splats * 0.10)
    n_furniture = total_splats - n_floor - n_wall - n_ceiling

    chunks: list[tuple[np.ndarray, np.ndarray]] = []

    # Floors
    living_floor = int(n_floor * 0.6)
    chunks.append(
        (
            _scatter_plane(rng, living_floor, (LIVING["x0"], LIVING["x1"]),
                           (LIVING["z0"], LIVING["z1"]), 0.0),
            _wood_colour(rng, living_floor),
        )
    )
    bed_floor = n_floor - living_floor
    chunks.append(
        (
            _scatter_plane(rng, bed_floor, (BEDROOM["x0"], BEDROOM["x1"]),
                           (BEDROOM["z0"], BEDROOM["z1"]), 0.0),
            _wood_colour(rng, bed_floor),
        )
    )

    # Walls — six segments forming the two rooms with a doorway between them.
    per_wall = n_wall // 6
    wall_specs = [
        ("x", LIVING["z0"], (LIVING["x0"], LIVING["x1"])),
        ("x", LIVING["z1"], (LIVING["x0"], BEDROOM["x1"])),
        ("z", LIVING["x0"], (LIVING["z0"], LIVING["z1"])),
        ("z", BEDROOM["x1"], (BEDROOM["z0"], BEDROOM["z1"])),
        ("x", BEDROOM["z0"], (BEDROOM["x0"], BEDROOM["x1"])),
        ("z", LIVING["x1"], (LIVING["z0"], 1.2)),  # partial: doorway above z=1.2
    ]
    for axis, fixed, span in wall_specs:
        pts = _scatter_wall(rng, per_wall, axis, fixed, span, LIVING["height"])
        chunks.append((pts, _wall_colour(rng, per_wall)))

    # Ceiling
    chunks.append(
        (
            _scatter_plane(rng, n_ceiling, (LIVING["x0"], BEDROOM["x1"]),
                           (LIVING["z0"], LIVING["z1"]), LIVING["height"]),
            _wall_colour(rng, n_ceiling),
        )
    )

    # Furniture: sofa, coffee table, bed.
    furniture = [
        ((1.4, 0.40, 0.55), (2.1, 0.80, 0.90), (0.42, 0.45, 0.50)),
        ((1.5, 0.22, 1.80), (1.0, 0.45, 0.60), (0.35, 0.26, 0.18)),
        ((5.8, 0.28, 2.30), (1.6, 0.55, 2.00), (0.80, 0.78, 0.74)),
    ]
    per_item = n_furniture // len(furniture)
    for centre, size, rgb in furniture:
        pts = _box(rng, per_item, centre, size)
        chunks.append((pts, _fabric_colour(rng, per_item, rgb)))

    positions = np.vstack([c[0] for c in chunks]).astype(np.float32)
    colours = np.vstack([c[1] for c in chunks]).astype(np.float32)
    n = len(positions)

    # Add soft vertical shading so the shading-map work in M3 has something real.
    height_factor = np.clip(1.15 - positions[:, 1] * 0.12, 0.75, 1.15)[:, None]
    colours = np.clip(colours * height_factor, 0, 1).astype(np.float32)

    # At this count the mean spacing between splats on a surface is about
    # 1.7 cm, so the radius has to exceed that for neighbours to overlap into
    # a continuous surface instead of speckling.
    scales = np.full((n, 3), 0.014, dtype=np.float32)
    scales += rng.normal(0, 0.002, (n, 3)).astype(np.float32)
    scales = np.clip(scales, 0.008, 0.03)

    rotations = np.zeros((n, 4), dtype=np.float32)
    rotations[:, 0] = 1.0  # identity quaternion, wxyz

    opacities = np.clip(rng.normal(0.9, 0.06, n), 0.3, 1.0).astype(np.float32)

    return SplatCloud(
        positions=positions,
        colours=colours,
        opacities=opacities,
        scales=scales,
        rotations=rotations,
    )


def build_manifest(project_id: str, scene_asset: str) -> Manifest:
    living = Room(
        id="living",
        name="Living room",
        type="living",
        waypoint=Waypoint(position=(2.1, 1.55, 3.25), yaw=0.0),
        floorPolygon=[
            (LIVING["x0"], LIVING["z0"]),
            (LIVING["x1"], LIVING["z0"]),
            (LIVING["x1"], LIVING["z1"]),
            (LIVING["x0"], LIVING["z1"]),
        ],
        walls=[
            Wall(start=(LIVING["x0"], LIVING["z0"]), end=(LIVING["x1"], LIVING["z0"]), height=2.6),
            Wall(start=(LIVING["x1"], LIVING["z0"]), end=(LIVING["x1"], LIVING["z1"]), height=2.6),
            Wall(start=(LIVING["x1"], LIVING["z1"]), end=(LIVING["x0"], LIVING["z1"]), height=2.6),
            Wall(start=(LIVING["x0"], LIVING["z1"]), end=(LIVING["x0"], LIVING["z0"]), height=2.6),
        ],
        openings=[
            Opening(type="door", wallIndex=1, offset=2.0, width=0.9, height=2.0),
            Opening(type="window", wallIndex=3, offset=1.9, width=1.4, height=1.3),
        ],
    )
    bedroom = Room(
        id="bedroom",
        name="Bedroom",
        type="bedroom",
        waypoint=Waypoint(position=(5.0, 1.55, 1.1), yaw=200.0),
        floorPolygon=[
            (BEDROOM["x0"], BEDROOM["z0"]),
            (BEDROOM["x1"], BEDROOM["z0"]),
            (BEDROOM["x1"], BEDROOM["z1"]),
            (BEDROOM["x0"], BEDROOM["z1"]),
        ],
        walls=[
            Wall(start=(BEDROOM["x0"], BEDROOM["z0"]), end=(BEDROOM["x1"], BEDROOM["z0"]),
                 height=2.6),
            Wall(start=(BEDROOM["x1"], BEDROOM["z0"]), end=(BEDROOM["x1"], BEDROOM["z1"]),
                 height=2.6),
            Wall(start=(BEDROOM["x1"], BEDROOM["z1"]), end=(BEDROOM["x0"], BEDROOM["z1"]),
                 height=2.6),
            Wall(start=(BEDROOM["x0"], BEDROOM["z1"]), end=(BEDROOM["x0"], BEDROOM["z0"]),
                 height=2.6),
        ],
        openings=[
            Opening(type="door", wallIndex=3, offset=1.6, width=0.9, height=2.0),
            Opening(type="window", wallIndex=1, offset=1.7, width=1.2, height=1.3),
        ],
    )

    manifest = Manifest(
        id=project_id,
        name="Synthetic test apartment",
        rooms=[living, bedroom],
        items=[
            ManifestItem(
                id="sofa-1", label="sofa", roomId="living",
                box={"center": (1.4, 0.40, 0.55), "size": (2.1, 0.80, 0.90), "yaw": 0.0},
                source="editor",
            ),
            ManifestItem(
                id="table-1", label="coffee table", roomId="living",
                box={"center": (1.5, 0.22, 1.80), "size": (1.0, 0.45, 0.60), "yaw": 0.0},
                source="editor",
            ),
            ManifestItem(
                id="bed-1", label="bed", roomId="bedroom",
                box={"center": (5.8, 0.28, 2.30), "size": (1.6, 0.55, 2.00), "yaw": 0.0},
                source="editor",
            ),
        ],
    )
    manifest.assets.scene = scene_asset
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default="demo-01")
    parser.add_argument("--splats", type=int, default=350_000)
    args = parser.parse_args()

    paths = ProjectPaths.for_project(repo_root(), args.project)
    paths.ensure()

    print(f"Generating {args.splats:,} splats ...")
    cloud = build_cloud(args.splats)

    ply = paths.scene / "scene.ply"
    write_ply(cloud, ply)
    print(f"  wrote {ply.relative_to(repo_root())} ({ply.stat().st_size / 1e6:.1f} MB)")

    scene_asset = "scene.ply"
    spz = paths.scene / "scene.spz"
    try:
        write_spz(ply, spz)
        scene_asset = "scene.spz"
        print(f"  wrote {spz.relative_to(repo_root())} ({spz.stat().st_size / 1e6:.1f} MB)")
    except SpzUnavailable as exc:
        print(f"  note: {exc}")

    manifest = build_manifest(args.project, scene_asset)
    manifest.write(paths.manifest)
    print(f"  wrote {paths.manifest.relative_to(repo_root())}")
    print(f"Done. Open http://localhost:5173/p/{args.project}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
