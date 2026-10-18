"""
`pipeline rebuild`: the flat rebuilt 1:1 from its floor-plan scan, lit in
Blender, as a glTF model the viewer walks through.

The scan gives exact walls, doors, windows and a box for every piece of
furniture; each piece is drawn by its own model when there is one (made from
a photo of it) and by a catalog stand-in of the same kind until then. Light
is designed — sun through the real windows, bounced by Cycles — and baked,
so a phone shows what a GPU path tracer computed.

This module turns the manifest into a plain JSON spec; `pipeline/blender/
build_flat.py` runs inside Blender and builds from it, so nothing here needs
Blender and nothing there needs this package.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
from pathlib import Path

import numpy as np

from .manifest import Manifest, ManifestItem, Room
from .paths import ProjectPaths, repo_root

DEFAULT_BLENDER = Path("/opt/blender/blender")
BUILD_SCRIPT = Path(__file__).resolve().parents[2] / "blender" / "build_flat.py"

#: Scan labels to the catalog categories that can stand in for them. Kinds
#: with no convincing catalog model (beds, sofas — Poly Haven has only period
#: ones) are drawn as plain blocks of the right size instead.
STAND_INS = {
    "chair": ("chair",),
    "table": ("dining_table", "desk", "coffee_table"),
    "storage": ("wardrobe", "shelf", "sideboard"),
    "plant": ("plant",),
}


class BlenderMissing(RuntimeError):
    pass


def blender_path() -> Path:
    path = Path(os.environ.get("BLENDER", DEFAULT_BLENDER))
    if not path.is_file():
        raise BlenderMissing(
            f"Blender not found at {path}. Run `pnpm run setup:pipeline`, or set BLENDER."
        )
    return path


def asset_path(url: str | None) -> str | None:
    """A catalog URL (/files/assets/...) as a local file, if it was downloaded."""
    if not url or not url.startswith("/files/"):
        return None
    local = repo_root() / "data" / url.removeprefix("/files/")
    return str(local) if local.is_file() else None


def stand_in(item: ManifestItem, furniture: list[dict]) -> str | None:
    """
    The catalog model closest in proportions to the scanned box, among the
    kinds that can stand in for its label. None: draw a block.
    """
    kinds = STAND_INS.get(item.label)
    if not kinds:
        return None
    target = np.log(np.maximum(item.box.size, 0.05))
    best, best_cost = None, math.inf
    for entry in furniture:
        path = asset_path(entry.get("glb"))
        if entry.get("category") not in kinds or path is None:
            continue
        cost = float(np.abs(np.log(np.maximum(entry["dimensionsM"], 0.05)) - target).sum())
        if cost < best_cost:
            best, best_cost = path, cost
    return best


def facing(item: ManifestItem, room: Room) -> tuple[float, float]:
    """
    Which way the piece faces, as (x, z): away from the wall it stands
    nearest. A scan box has an axis but no front; beds, sofas, wardrobes and
    desks all put their back to a wall, so this is right far more often than
    a fixed side of the box.
    """
    yaw = math.radians(item.box.yaw)
    depth_axis = np.array([-math.sin(yaw), math.cos(yaw)])
    centre = np.array([item.box.center[0], item.box.center[2]])
    half = item.box.size[2] / 2

    def wall_distance(point: np.ndarray) -> float:
        best = math.inf
        for wall in room.walls:
            a, b = np.array(wall.start), np.array(wall.end)
            ab = b - a
            t = float(np.clip(np.dot(point - a, ab) / max(float(ab @ ab), 1e-9), 0, 1))
            best = min(best, float(np.linalg.norm(point - (a + t * ab))))
        return best

    ahead = wall_distance(centre + depth_axis * half)
    behind = wall_distance(centre - depth_axis * half)
    front = depth_axis if behind <= ahead else -depth_axis
    return float(front[0]), float(front[1])


def scene_spec(
    paths: ProjectPaths,
    manifest: Manifest,
    floor: dict,
    furniture: list[dict],
    wall_colour: str,
    lightmap_size: int,
    samples: int,
) -> dict:
    """Everything build_flat.py needs, in scene space: metres, Y up."""
    rooms = []
    for room in manifest.rooms:
        rooms.append(
            {
                "id": room.id,
                "polygon": [list(p) for p in room.floorPolygon],
                "height": max((w.height for w in room.walls), default=2.5),
                "waypoint": {"position": list(room.waypoint.position), "yaw": room.waypoint.yaw},
                "walls": [
                    {
                        "start": list(w.start),
                        "end": list(w.end),
                        "openings": [
                            {
                                "type": o.type,
                                "offset": o.offset,
                                "width": o.width,
                                "height": o.height,
                                "bottom": o.bottom,
                            }
                            for o in room.openings
                            if o.wallIndex == i
                        ],
                    }
                    for i, w in enumerate(room.walls)
                ],
            }
        )

    room_of = {r.id: r for r in manifest.rooms}
    items = []
    for item in manifest.items:
        own = paths.scene / item.model if item.model else None
        items.append(
            {
                "id": item.id,
                "label": item.label,
                "center": list(item.box.center),
                "size": list(item.box.size),
                "front": list(facing(item, room_of[item.roomId])),
                "model": str(own) if own and own.is_file() else stand_in(item, furniture),
                "own": bool(own and own.is_file()),
            }
        )

    maps = floor.get("maps") or {}
    return {
        "rooms": rooms,
        "items": items,
        "floor": {
            "id": floor["id"],
            "albedo": asset_path(maps.get("albedo")),
            "normal": asset_path(maps.get("normal")),
            "roughness": asset_path(maps.get("roughness")),
            "tile": floor.get("tileSizeM", [1.0, 1.0]),
            "colour": floor.get("baseColor", "#b08a5a"),
        },
        "wall_colour": wall_colour,
        "lightmap_size": lightmap_size,
        "samples": samples,
        "out": {
            "glb": str(paths.scene / "flat.glb"),
            "lightmap": str(paths.scene / "flat_light.jpg"),
            "meta": str(paths.work / "rebuild_meta.json"),
            "preview": str(paths.work / "rebuild_preview.png"),
        },
    }


def run(spec: dict, spec_path: Path, preview_only: bool = False) -> None:
    spec_path.write_text(json.dumps(spec, indent=2), encoding="utf-8")
    # --python-exit-code: otherwise Blender exits 0 when the script raises.
    command = [str(blender_path()), "-b", "--factory-startup", "--python-exit-code", "1",
               "-P", str(BUILD_SCRIPT), "--", str(spec_path)]
    if preview_only:
        command.append("--preview-only")
    subprocess.run(command, check=True)
