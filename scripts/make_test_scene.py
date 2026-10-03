"""
Generate a synthetic Gaussian-splat apartment for development (M0).

Why synthetic: docs/SPEC.md §2 — no licence risk, no download, reproducible,
and it exercises the whole render path before any real capture exists.

The flat is a seven-room apartment of about 108 m²: a corridor the length of
the plan with three rooms off each side. Everything is derived from the
`ROOMS` and `WALLS` tables below, so changing the layout is editing data.

On sharpness: surfaces are given coherent procedural texture — floorboards
with grain, tile grout, skirting boards — rather than per-splat colour noise.
Noise at splat scale reads as fog; a plank edge reads as a plank. This is
still a stand-in. A real capture trained with gsplat resolves far more
detail than anything generated here.

Usage:  pnpm seed:testscene
        uv run python scripts/make_test_scene.py --project demo-01 --splats 900000
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline" / "src"))

from walkthrough_pipeline.manifest import (  # noqa: E402
    Manifest,
    Opening,
    Room,
    Wall,
    Waypoint,
)
from walkthrough_pipeline.paths import ProjectPaths, repo_root  # noqa: E402
from walkthrough_pipeline.splat import (  # noqa: E402
    SplatCloud,
    write_ply,
    write_spz,
)

RNG_SEED = 7
CEILING = 2.6
#: Half-thickness of a wall. Splats are scattered on both faces, which is what
#: a capture of a real wall gives you.
WALL_HALF = 0.06


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RoomSpec:
    id: str
    name: str
    type: str
    #: Floor rectangle, metres: (x0, z0, x1, z1).
    rect: tuple[float, float, float, float]
    #: 'board' for floorboards, 'tile' for a tiled grid.
    floor: str
    #: Base floor colour, linear RGB.
    floor_rgb: tuple[float, float, float]
    #: Where the camera stands on arrival, and which way it faces.
    stand: tuple[float, float]
    yaw: float
    #: World-space centres of this room's doors and windows.
    doors: tuple[tuple[float, float], ...] = ()
    windows: tuple[tuple[float, float], ...] = ()
    door_width: float = 0.9
    window_width: float = 1.4


#: A corridor the length of the plan, three rooms north of it and three south.
#: Every room opens onto the corridor, so every room is reachable on foot.
ROOMS: tuple[RoomSpec, ...] = (
    RoomSpec(
        id="hall", name="Hallway", type="hallway",
        rect=(0.0, 3.6, 12.0, 5.4), floor="board", floor_rgb=(0.46, 0.33, 0.21),
        stand=(1.4, 4.5), yaw=270.0,
        # The corridor repeats every door that opens onto it. A doorway has to
        # be declared by BOTH rooms it joins: `canStand` tests rooms
        # independently, so a door the corridor does not know about is a wall
        # you get stuck against on the corridor side.
        doors=(
            (2.6, 3.6), (7.0, 3.6), (10.4, 3.6),
            (2.0, 5.4), (5.2, 5.4), (9.2, 5.4),
        ),
    ),
    RoomSpec(
        id="living", name="Living room", type="living",
        rect=(0.0, 0.0, 5.2, 3.6), floor="board", floor_rgb=(0.54, 0.40, 0.25),
        stand=(2.6, 3.2), yaw=0.0,
        doors=((2.6, 3.6),), windows=((2.0, 0.0), (0.0, 1.8)),
        window_width=1.6,
    ),
    RoomSpec(
        id="kitchen", name="Kitchen", type="kitchen",
        rect=(5.2, 0.0, 8.8, 3.6), floor="tile", floor_rgb=(0.72, 0.70, 0.67),
        stand=(7.0, 3.2), yaw=0.0,
        doors=((7.0, 3.6),), windows=((7.0, 0.0),),
        window_width=1.2,
    ),
    RoomSpec(
        id="bedroom-1", name="Main bedroom", type="bedroom",
        rect=(8.8, 0.0, 12.0, 3.6), floor="board", floor_rgb=(0.50, 0.36, 0.23),
        stand=(10.4, 3.2), yaw=0.0,
        doors=((10.4, 3.6),), windows=((10.4, 0.0), (12.0, 1.8)),
    ),
    RoomSpec(
        id="bedroom-2", name="Second bedroom", type="bedroom",
        rect=(0.0, 5.4, 4.0, 9.0), floor="board", floor_rgb=(0.50, 0.36, 0.23),
        stand=(2.0, 5.9), yaw=180.0,
        doors=((2.0, 5.4),), windows=((2.0, 9.0),),
    ),
    RoomSpec(
        id="bathroom", name="Bathroom", type="bathroom",
        rect=(4.0, 5.4, 6.4, 9.0), floor="tile", floor_rgb=(0.78, 0.77, 0.75),
        stand=(5.2, 5.9), yaw=180.0,
        doors=((5.2, 5.4),), windows=((5.2, 9.0),),
        window_width=0.8,
    ),
    RoomSpec(
        id="office", name="Study", type="office",
        rect=(6.4, 5.4, 12.0, 9.0), floor="board", floor_rgb=(0.52, 0.38, 0.24),
        stand=(9.2, 5.9), yaw=180.0,
        doors=((9.2, 5.4),), windows=((9.0, 9.0), (12.0, 7.2)),
        window_width=1.6,
    ),
)


@dataclass
class WallRun:
    """A continuous run of wall, with the openings cut out of it."""

    #: 'x' runs along X at a fixed Z; 'z' runs along Z at a fixed X.
    axis: str
    fixed: float
    span: tuple[float, float]
    #: (centre along the run, width, sill height, head height) per opening.
    gaps: list[tuple[float, float, float, float]] = field(default_factory=list)


DOOR_HEAD = 2.05
WINDOW_SILL = 0.95
WINDOW_HEAD = 2.25


def _wall_runs() -> list[WallRun]:
    """
    Every structural wall in the flat, once.

    Built here rather than from each room's four edges, because an interior
    partition is shared by two rooms and scattering splats for it twice leaves
    two coincident sheets that flicker against each other.
    """
    runs = [
        # Exterior shell.
        WallRun("x", 0.0, (0.0, 12.0)),
        WallRun("x", 9.0, (0.0, 12.0)),
        WallRun("z", 0.0, (0.0, 9.0)),
        WallRun("z", 12.0, (0.0, 9.0)),
        # The corridor's two long walls.
        WallRun("x", 3.6, (0.0, 12.0)),
        WallRun("x", 5.4, (0.0, 12.0)),
        # Partitions between the rooms north of the corridor.
        WallRun("z", 5.2, (0.0, 3.6)),
        WallRun("z", 8.8, (0.0, 3.6)),
        # ... and south of it.
        WallRun("z", 4.0, (5.4, 9.0)),
        WallRun("z", 6.4, (5.4, 9.0)),
    ]
    index = {(r.axis, r.fixed): r for r in runs}

    for room in ROOMS:
        for point, width, sill, head in (
            *((d, room.door_width, 0.0, DOOR_HEAD) for d in room.doors),
            *((w, room.window_width, WINDOW_SILL, WINDOW_HEAD) for w in room.windows),
        ):
            x, z = point
            # A horizontal run is pinned by z and positioned along x, and the
            # other way round for a vertical one.
            for axis, fixed, along in (("x", z, x), ("z", x, z)):
                run = index.get((axis, fixed))
                if run is not None:
                    run.gaps.append((along, width, sill, head))
                    break

    return runs


def _opening_for(room: RoomSpec, kind: str, point: tuple[float, float], width: float) -> Opening:
    """
    Turn a world-space opening centre into the room-relative form the manifest
    uses: which of the room's four walls it sits on, and how far along it.

    Computed rather than written out by hand — `offset` is measured from each
    wall's start, the walls wind counter-clockwise, so half of them run
    backwards along their axis and a hand-written number is easy to get wrong.
    `canStand` depends on these, so a wrong one is a wall you walk through.
    """
    x0, z0, x1, z1 = room.rect
    corners = [(x0, z0), (x1, z0), (x1, z1), (x0, z1)]
    px, pz = point
    for index in range(4):
        ax, az = corners[index]
        bx, bz = corners[(index + 1) % 4]
        if ax == bx and abs(px - ax) < 1e-6 and min(az, bz) <= pz <= max(az, bz):
            offset = abs(pz - az)
        elif az == bz and abs(pz - az) < 1e-6 and min(ax, bx) <= px <= max(ax, bx):
            offset = abs(px - ax)
        else:
            continue
        return Opening(
            type=kind,  # type: ignore[arg-type]
            wallIndex=index,
            offset=round(offset, 4),
            width=width,
            height=DOOR_HEAD if kind == "door" else WINDOW_HEAD - WINDOW_SILL,
        )
    raise ValueError(f"{room.id}: opening at {point} is not on any of its walls")


# ---------------------------------------------------------------------------
# Surfaces
# ---------------------------------------------------------------------------


#: Surface normals, for orienting the flattened splats.
UP = (0.0, 1.0, 0.0)

#: How far from a corner the ambient darkening fades out, in metres.
AO_REACH = 0.45
#: How dark a corner gets, as a fraction of the open-surface colour.
AO_DEPTH = 0.80


def _grid(
    rng: np.random.Generator,
    u0: float,
    u1: float,
    v0: float,
    v1: float,
    spacing: float,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Sample a rectangle on a jittered grid.

    Not `uniform`. Random scatter clumps — Poisson, not even — so discs sized
    to the mean spacing leave gaps between the clumps. Covering those gaps
    meant widening the discs, and wide discs blur the very corners and edges
    that make a room read as a room, which left the walls looking like a soft
    white void. A jittered grid covers evenly at a radius just over half the
    spacing, so the discs stay small and the corners stay sharp. The jitter is
    what stops it reading as a lattice.
    """
    nu = max(1, round((u1 - u0) / spacing))
    nv = max(1, round((v1 - v0) / spacing))
    du = (u1 - u0) / nu
    dv = (v1 - v0) / nv
    gu, gv = np.meshgrid(
        u0 + (np.arange(nu) + 0.5) * du,
        v0 + (np.arange(nv) + 0.5) * dv,
        indexing="ij",
    )
    u = gu.ravel() + rng.uniform(-0.32, 0.32, gu.size) * du
    v = gv.ravel() + rng.uniform(-0.32, 0.32, gv.size) * dv
    return u, v


def _smoothstep(x: np.ndarray) -> np.ndarray:
    t = np.clip(x, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _ao(distance: np.ndarray) -> np.ndarray:
    """
    Ambient darkening by distance to the nearest edge.

    A plain white box with no corner shading does not read as a room — it
    reads as a rendering fault, because every real interior has light falling
    off where two surfaces meet. This is the cheapest honest approximation of
    it: the real thing arrives with the capture.
    """
    return AO_DEPTH + (1.0 - AO_DEPTH) * _smoothstep(distance / AO_REACH)


def _floor_surface(
    rng: np.random.Generator,
    rect: tuple[float, float, float, float],
    spacing: float,
    base: tuple[float, float, float],
    kind: str,
) -> tuple[np.ndarray, np.ndarray]:
    """Floorboards or tiles, darkened towards the walls."""
    x0, z0, x1, z1 = rect
    x, z = _grid(rng, x0, x1, z0, z1, spacing)
    y = rng.normal(0, 0.0005, x.size)

    if kind == "board":
        board = np.floor(z / 0.19).astype(np.int64)
        # Hash the board index to a stable shade, so a board is one colour
        # along its whole length rather than fading.
        tone = 0.88 + (((board * 2654435761) % 1000) / 1000.0) * 0.24
        grain = 1.0 + 0.05 * np.sin(x * 11.0 + board * 1.7) + rng.normal(0, 0.010, x.size)
        edge = np.abs((z / 0.19) % 1.0 - 0.5) * 2.0
        detail = tone * grain * np.where(edge > 0.93, 0.68, 1.0)
    else:
        size = 0.30
        u = np.abs((x / size) % 1.0 - 0.5) * 2.0
        v = np.abs((z / size) % 1.0 - 0.5) * 2.0
        grout = np.where((u > 0.92) | (v > 0.92), 0.62, 1.0)
        tile = (np.floor(x / size) * 7919 + np.floor(z / size) * 104729).astype(np.int64)
        detail = grout * (0.95 + ((tile % 100) / 100.0) * 0.1)

    to_wall = np.minimum.reduce([x - x0, x1 - x, z - z0, z1 - z])
    rgb = np.array(base)[None, :] * (detail * _ao(to_wall))[:, None]
    return np.stack([x, y, z], axis=1), np.clip(rgb, 0, 1)


def _ceiling(
    rng: np.random.Generator,
    rect: tuple[float, float, float, float],
    spacing: float,
) -> tuple[np.ndarray, np.ndarray]:
    x0, z0, x1, z1 = rect
    x, z = _grid(rng, x0, x1, z0, z1, spacing)
    y = np.full(x.size, CEILING) + rng.normal(0, 0.0005, x.size)
    to_wall = np.minimum.reduce([x - x0, x1 - x, z - z0, z1 - z])
    rgb = np.array([0.97, 0.97, 0.962])[None, :] * _ao(to_wall)[:, None]
    return np.stack([x, y, z], axis=1), np.clip(rgb, 0, 1)


def _place(axis: str, t: np.ndarray, y: np.ndarray, fixed: np.ndarray) -> np.ndarray:
    """Lift a (along, height) pair onto a wall run's plane."""
    if axis == "x":
        return np.stack([t, y, fixed], axis=1)
    return np.stack([fixed, y, t], axis=1)


def _wall_face(
    rng: np.random.Generator,
    run: WallRun,
    offset: float,
    spacing: float,
    crossings: np.ndarray,
) -> tuple[np.ndarray, np.ndarray] | None:
    """
    One face of a wall run: flat white, openings cut out, darkened where it
    meets the floor, the ceiling and the walls that cross it.
    """
    lo, hi = run.span
    t, y = _grid(rng, lo, hi, 0.0, CEILING, spacing)

    keep = np.ones(t.size, dtype=bool)
    for centre, width, sill, head in run.gaps:
        keep &= ~((np.abs(t - centre) <= width / 2) & (y >= sill) & (y <= head))
    t, y = t[keep], y[keep]
    if not t.size:
        return None

    fixed = np.full(t.size, run.fixed + offset) + rng.normal(0, 0.0005, t.size)
    points = _place(run.axis, t, y, fixed)

    # Flat white. No mottle, no gradient, no per-splat noise: on a soft blob
    # none of that reads as texture, it reads as grime. All the variation a
    # painted wall should show is the corner shading below.
    rgb = np.tile(np.array([0.955, 0.953, 0.945]), (t.size, 1))
    rgb[y < 0.11] = np.array([0.975, 0.973, 0.967])  # skirting

    to_corner = (
        np.min(np.abs(t[:, None] - crossings[None, :]), axis=1)
        if crossings.size
        else np.full(t.size, AO_REACH)
    )
    distance = np.minimum.reduce([to_corner, y, CEILING - y])
    return points, np.clip(rgb * _ao(distance)[:, None], 0, 1)


def _opening_reveals(
    rng: np.random.Generator, run: WallRun, spacing: float
) -> list[tuple[np.ndarray, np.ndarray, tuple[float, float, float]]]:
    """
    The returns around each opening — the thickness of the wall you see when
    you stand beside a window or door.

    Without them an opening is a slot straight through the wall, showing
    whatever is behind the scene, so every window reads as a hole punched in a
    sheet of paper rather than an opening in something solid.
    """
    out = []
    side_normal = (1.0, 0.0, 0.0) if run.axis == "x" else (0.0, 0.0, 1.0)
    for centre, width, sill, head in run.gaps:
        # Jambs: vertical faces at each side of the opening, facing along the run.
        for side in (-1.0, 1.0):
            d, h = _grid(rng, -WALL_HALF, WALL_HALF, sill, head, spacing)
            t = np.full(d.size, centre + side * width / 2)
            points = _place(run.axis, t, h, np.full(d.size, run.fixed) + d)
            shade = _ao(np.minimum(h - sill, head - h)) * 0.95
            rgb = np.array([0.95, 0.948, 0.94])[None, :] * shade[:, None]
            out.append((points, np.clip(rgb, 0, 1), side_normal))

        # Head, and the sill where there is one.
        for level, normal in ((head, UP), *(((sill, UP),) if sill > 0.01 else ())):
            d, s = _grid(rng, -WALL_HALF, WALL_HALF, -width / 2, width / 2, spacing)
            t = centre + s
            points = _place(run.axis, t, np.full(d.size, level), np.full(d.size, run.fixed) + d)
            rgb = np.tile(np.array([0.95, 0.948, 0.94]) * 0.93, (d.size, 1))
            out.append((points, np.clip(rgb, 0, 1), normal))
    return out


def _area(rect: tuple[float, float, float, float]) -> float:
    x0, z0, x1, z1 = rect
    return (x1 - x0) * (z1 - z0)


def _flat_quaternion(normal: tuple[float, float, float]) -> np.ndarray:
    """
    The rotation, as wxyz, that turns a splat's thin axis onto `normal`.

    Scales below are (r, r, t) with t the thin one, which is the splat's local
    Z, so this is the rotation taking +Z to the surface normal.
    """
    z = np.array([0.0, 0.0, 1.0])
    n = np.array(normal, dtype=float)
    n /= np.linalg.norm(n)
    dot = float(np.clip(np.dot(z, n), -1.0, 1.0))
    if dot > 1.0 - 1e-9:
        return np.array([1.0, 0.0, 0.0, 0.0])
    if dot < -1.0 + 1e-9:
        return np.array([0.0, 1.0, 0.0, 0.0])  # 180 degrees about X
    axis = np.cross(z, n)
    axis /= np.linalg.norm(axis)
    angle = np.arccos(dot)
    return np.array([np.cos(angle / 2), *(axis * np.sin(angle / 2))])


def build_cloud(total_splats: int) -> tuple[SplatCloud, np.ndarray]:
    """
    Lay the flat out on a jittered grid, at one spacing everywhere, so density
    — and so sharpness — is even throughout rather than concentrated wherever
    the budget happened to land.
    """
    rng = np.random.default_rng(RNG_SEED)
    runs = _wall_runs()

    floor_area = sum(_area(r.rect) for r in ROOMS)
    wall_area = sum((r.span[1] - r.span[0]) * CEILING * 2 for r in runs) * 0.92
    total_area = floor_area * 2 + wall_area  # floors + ceilings + walls
    spacing = float(np.sqrt(total_area / max(total_splats, 1)))

    # Where each run is crossed by a perpendicular one. Those are the room
    # corners, and corners are where the shading has to land.
    cross_for_x = np.array(sorted({r.fixed for r in runs if r.axis == "z"}))
    cross_for_z = np.array(sorted({r.fixed for r in runs if r.axis == "x"}))

    chunks: list[tuple[np.ndarray, np.ndarray]] = []
    is_floor: list[bool] = []
    normals: list[tuple[float, float, float]] = []

    def add(chunk, normal, floor=False):
        if chunk is None or not len(chunk[0]):
            return
        chunks.append((chunk[0], chunk[1]))
        normals.append(normal)
        is_floor.append(floor)

    for room in ROOMS:
        add(_floor_surface(rng, room.rect, spacing, room.floor_rgb, room.floor), UP, floor=True)
        add(_ceiling(rng, room.rect, spacing), UP)

    for run in runs:
        # A run along X faces along Z, and the other way round.
        normal = (0.0, 0.0, 1.0) if run.axis == "x" else (1.0, 0.0, 0.0)
        crossings = cross_for_x if run.axis == "x" else cross_for_z
        for offset in (-WALL_HALF, WALL_HALF):
            add(_wall_face(rng, run, offset, spacing, crossings), normal)
        for points, rgb, reveal_normal in _opening_reveals(rng, run, spacing):
            add((points, rgb), reveal_normal)

    positions = np.vstack([c[0] for c in chunks]).astype(np.float32)
    colours = np.vstack([c[1] for c in chunks]).astype(np.float32)
    floor_mask = np.concatenate(
        [np.full(len(c[0]), flag, dtype=bool) for c, flag in zip(chunks, is_floor, strict=True)]
    )
    n = len(positions)

    # Splat shape. This is the whole difference between a scene that reads as
    # surfaces and one that reads as fog.
    #
    # Real 3DGS training flattens its Gaussians onto the surfaces they
    # describe: a wall ends up covered in wide, paper-thin discs lying in the
    # plane of the wall. An early version of this generator used isotropic
    # spheres with an identity rotation, and 1.7 cm balls floating around a
    # plane is what blur looks like however many of them you add.
    #
    # The radius is just over half the grid spacing — enough that neighbours
    # overlap and the surface closes, small enough that a corner stays a line.
    # That only works because the positions are on a grid; it was 1.45x this
    # when they were scattered, and the walls looked like a white void.
    radius = float(np.clip(spacing * 0.80, 0.004, 0.02))
    thickness = max(radius * 0.11, 0.0012)

    scales = np.empty((n, 3), dtype=np.float32)
    scales[:, 0] = radius
    scales[:, 1] = radius
    scales[:, 2] = thickness

    rotations = np.concatenate(
        [
            np.tile(_flat_quaternion(normal), (len(chunk[0]), 1))
            for chunk, normal in zip(chunks, normals, strict=True)
        ]
    ).astype(np.float32)

    # Fully opaque. Varying opacity on an interior surface just lets the splat
    # behind show through unevenly, which is more of the blotchiness the flat
    # colour above is there to remove.
    opacities = np.ones(n, dtype=np.float32)

    return (
        SplatCloud(
            positions=positions,
            colours=colours,
            opacities=opacities,
            scales=scales,
            rotations=rotations,
        ),
        floor_mask,
    )


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


def build_manifest(
    project_id: str,
    scene_asset: str,
    floor_colours: dict[str, str],
) -> Manifest:
    rooms: list[Room] = []
    for spec in ROOMS:
        x0, z0, x1, z1 = spec.rect
        corners = [(x0, z0), (x1, z0), (x1, z1), (x0, z1)]
        rooms.append(
            Room(
                id=spec.id,
                name=spec.name,
                type=spec.type,  # type: ignore[arg-type]
                waypoint=Waypoint(position=(spec.stand[0], 1.6, spec.stand[1]), yaw=spec.yaw),
                floorPolygon=corners,
                walls=[
                    Wall(start=corners[i], end=corners[(i + 1) % 4], height=CEILING)
                    for i in range(4)
                ],
                openings=[
                    *(_opening_for(spec, "door", d, spec.door_width) for d in spec.doors),
                    *(_opening_for(spec, "window", w, spec.window_width) for w in spec.windows),
                ],
                originalFloorColor=floor_colours.get(spec.id),
            )
        )

    manifest = Manifest(id=project_id, name="Maple Court, flat 4", rooms=rooms, items=[])
    manifest.assets.scene = scene_asset
    return manifest


def _average_colour(colours: np.ndarray) -> str:
    rgb = np.clip(colours.mean(axis=0) * 255, 0, 255).astype(int)
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default="demo-01")
    parser.add_argument("--splats", type=int, default=900_000)
    args = parser.parse_args()

    paths = ProjectPaths.for_project(repo_root(), args.project)
    paths.ensure()

    area = sum(_area(r.rect) for r in ROOMS)
    print(f"Generating {args.splats:,} splats over {len(ROOMS)} rooms ({area:.0f} m²) ...")
    cloud, floor_mask = build_cloud(args.splats)

    def write(cloud_part: SplatCloud, name: str) -> str:
        # The PLY is kept for tools; the viewer loads the SPZ.
        write_ply(cloud_part, paths.scene / f"{name}.ply")
        spz = paths.scene / f"{name}.spz"
        write_spz(cloud_part, spz)
        size = spz.stat().st_size / 1e6
        print(f"  {spz.name:<22} {len(cloud_part):>9,} splats  {size:6.1f} MB")
        return spz.name

    # The real pipeline produces this split in `pipeline floor` (M3). The
    # generator knows which splats are floor, so it can emit the same three
    # assets and let the viewer's floor-replacement path be exercised without
    # the segmentation model.
    scene_asset = write(cloud, "scene")
    nofloor_asset = write(cloud.select(~floor_mask), "scene_nofloor")
    floor_asset = write(cloud.select(floor_mask), "floor")

    # Average floor colour per room, so a room whose floor was not replaced can
    # still be drawn once the floor splats are hidden.
    floor_points = cloud.positions[floor_mask]
    floor_colours = cloud.colours[floor_mask]
    averages: dict[str, str] = {}
    for spec in ROOMS:
        x0, z0, x1, z1 = spec.rect
        inside = (
            (floor_points[:, 0] >= x0)
            & (floor_points[:, 0] <= x1)
            & (floor_points[:, 2] >= z0)
            & (floor_points[:, 2] <= z1)
        )
        if inside.any():
            averages[spec.id] = _average_colour(floor_colours[inside])

    manifest = build_manifest(args.project, scene_asset, averages)
    manifest.assets.sceneNoFloor = nofloor_asset
    manifest.assets.floor = floor_asset
    manifest.write(paths.manifest)
    print(f"  manifest               {paths.manifest.relative_to(repo_root())}")
    print(f"Done. Open http://localhost:5173/p/{args.project}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
