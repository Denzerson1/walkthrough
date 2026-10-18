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

What a floor-plan scan cannot see comes from the project's dressing file,
`input/dressing.json`, read off the customer's photos: roof windows in a
slope, which way the windows face, floors re-laid from photos of them, the
finish of each piece, built-ins the scanner missed, and the preview's view.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
from pathlib import Path

import numpy as np

from . import flat_geometry, photo_texture
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


#: What the pieces drawn from parts are made of: colour (sRGB), roughness,
#: and a generated texture where flat colour reads as plastic. Colours read
#: off the capture photos against their white walls.
SURFACES: dict[str, tuple[str, float, str | None]] = {
    "gloss": ("#f4f4f2", 0.12, None),        # kitchen fronts, high gloss
    "white": ("#efefec", 0.6, None),         # carcasses, appliances
    "plinth": ("#dedcd8", 0.6, None),
    "stone": ("#7d7c79", 0.45, "stone"),     # concrete-look worktop
    "alu": ("#a3a4a2", 0.35, None),          # grip channels
    "steel": ("#b6b7b5", 0.3, None),         # handles, tap, oven trim
    "black": ("#0f1011", 0.15, None),        # hob, oven glass, TV
    "basin": ("#f3f3f1", 0.25, None),
    "oak": ("#d3c0a5", 0.55, "veneer"),      # the wardrobe: pale oak
    "oak-warm": ("#a98b6b", 0.5, "veneer"),  # kitchen backsplash, island, shelves
    "wenge": ("#3c2b24", 0.5, "veneer"),     # the bed base
    "linen": ("#f2f0eb", 0.9, None),
    "boucle": ("#e8e3d9", 1.0, "boucle"),
    "fabric": ("#a8a39d", 0.9, "boucle"),    # bar stools
    "coats": ("#2e2b2c", 0.95, None),
    "shade": ("#f3efe5", 0.8, None),
    "glass-shelf": ("#dfe7e6", 0.05, None),
    "frame": ("#6b5038", 0.5, None),
    "picture": ("#e6e1d6", 0.8, None),
    "vent": ("#8d8f91", 0.5, None),
    "basket": ("#7d7f80", 0.4, None),
    "boxes": ("#bdb8b0", 0.9, None),         # felt boxes in the wardrobe
}


def surfaces(paths: ProjectPaths) -> dict[str, dict]:
    """The surfaces as build_flat.py takes them, textures written into the scene folder."""
    out = {}
    for i, (name, (colour, roughness, kind)) in enumerate(SURFACES.items()):
        texture = None
        if kind:
            path = paths.scene / f"surface-{name}.jpg"
            photo_texture.surface_texture(kind, colour, seed=i).save(path, quality=90)
            texture = str(path)
        out[name] = {"colour": colour, "roughness": roughness, "texture": texture}
    return out


def outside_view(paths: ProjectPaths, wanted: list[dict]) -> dict | None:
    """The view out of the windows: the first photo out of one, its sky cut out, and the
    sky's colours for the dome behind it."""
    first = next((w for w in wanted if w.get("view")), None)
    if first is None:
        return None
    image, zenith, horizon, below = photo_texture.window_view(
        repo_root() / first["view"], tuple(first.get("viewCrop", (0.0, 0.0, 1.0, 1.0)))
    )
    out = paths.scene / "view.png"
    image.save(out)
    return {"view": str(out), "zenith": zenith, "horizon": horizon, "below": below}


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


def sized_for(front, size: list[float], yaw: float) -> list[float]:
    """
    A scanned box's size as (across its front, height, depth) for a front
    the dressing names. The scan sizes a box along its long side first, so
    a bed comes as (length, height, width); facing along that side, a model
    fitted to it would be stretched sideways. Swap when the front runs along
    the box's first axis rather than its depth.
    """
    yaw_r = math.radians(yaw)
    depth_axis = (-math.sin(yaw_r), math.cos(yaw_r))
    if abs(front[0] * depth_axis[0] + front[1] * depth_axis[1]) < 0.5:
        return [size[2], size[1], size[0]]
    return list(size)


def catalog_floor(floor: dict) -> dict:
    """A catalog floor as build_flat.py takes it: local texture files and tile size."""
    maps = floor.get("maps") or {}
    return {
        "id": floor["id"],
        "albedo": asset_path(maps.get("albedo")),
        "normal": asset_path(maps.get("normal")),
        "roughness": asset_path(maps.get("roughness")),
        "tile": floor.get("tileSizeM", [1.0, 1.0]),
        "colour": floor.get("baseColor", "#b08a5a"),
    }


def photo_floor(paths: ProjectPaths, name: str, entry: dict) -> dict:
    """A floor re-laid from the customer's photo of it, written into the scene folder."""
    source = repo_root() / entry["photo"]
    if "planks" in entry:
        image, size = photo_texture.plank_texture(source, entry["planks"])
    else:
        image, size = photo_texture.tile_texture(source, tuple(entry["tiles"]),
                                                 entry.get("offset", 0.0))
    out = paths.scene / f"floor-{name.lower()}.jpg"
    image.save(out, quality=90)
    return {"id": f"photo-{name.lower()}", "albedo": str(out), "normal": None, "roughness": None,
            "tile": list(size), "colour": photo_texture.mean_albedo(source)}


def box_from_corners(lo, hi, front) -> tuple[list[float], list[float]]:
    """An axis-aligned box from two corners: its centre, and its size as
    (across its front, height, depth) the way furniture boxes are sized."""
    centre = [(a + b) / 2 for a, b in zip(lo, hi, strict=True)]
    dx, dy, dz = (abs(b - a) for a, b in zip(lo, hi, strict=True))
    return centre, [dz, dy, dx] if abs(front[0]) > abs(front[1]) else [dx, dy, dz]


def roof_windows(ceilings: list, wanted: list[dict]) -> list[dict]:
    """Each roof window the dressing places, worked out in the slope it sits in."""
    out = []
    for w in wanted:
        index = next(
            (i for i, c in enumerate(ceilings) if not flat_geometry.is_flat(c)
             and flat_geometry.point_in_polygon(*w["at"], [[p[0], p[2]] for p in c])),
            None,
        )
        if index is None:
            raise ValueError(f"No sloped ceiling over the roof window at {w['at']}")
        frame = flat_geometry.slope_frame(ceilings[index])
        window = flat_geometry.roof_window(frame, w["at"], w["width"], w["length"],
                                           w["depth"], w.get("bottom", 0.0))
        out.append({"ceiling": index, "frame": frame, **window})
    return out


def sun(dressing: dict, facing_dir: tuple[float, float]) -> dict | None:
    """Where the sun stands at the dressing's date and time, as a scene direction towards it."""
    place, when = dressing.get("location"), dressing.get("sun")
    if not place or not when or "windowsFace" not in dressing:
        return None
    year, month, day = (int(v) for v in when["date"].split("-"))
    hours, minutes = (int(v) for v in when["time"].split(":"))
    bearing, elevation = flat_geometry.sun_position(
        year, month, day, hours + minutes / 60, place["utcOffset"],
        place["latitude"], place["longitude"],
    )
    x, z = flat_geometry.compass_to_scene(bearing, dressing["windowsFace"], facing_dir)
    e = math.radians(elevation)
    return {"bearing": round(bearing, 1), "elevation": round(elevation, 1),
            "direction": [x * math.cos(e), math.sin(e), z * math.cos(e)]}


def scene_spec(
    paths: ProjectPaths,
    manifest: Manifest,
    floors: dict[str, dict],
    furniture: list[dict],
    wall_colour: str,
    lightmap_size: int,
    samples: int,
    scan: dict | None = None,
    dressing: dict | None = None,
) -> dict:
    """
    Everything build_flat.py needs, in scene space: metres, Y up. `floors`
    holds the main floor under "main" and a floor zone's own floor under its
    scan label; `scan` carries what the manifest does not keep (ceiling
    pieces, partitions, floor zones), for the first room.
    """
    scan, dressing = scan or {}, dressing or {}
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
                "ceilings": [],
                "partitions": [],
                "zones": [],
            }
        )
    if rooms and scan:
        rooms[0]["ceilings"] = scan.get("ceilings", [])
        rooms[0]["partitions"] = scan.get("partitions", [])
        rooms[0]["zones"] = [
            {"label": label, "polygon": [list(p) for p in polygon]}
            for label, polygon in scan.get("zones", [])
            if label in floors
        ]

    room_of = {r.id: r for r in manifest.rooms}
    dressed = dressing.get("items", {})
    items = []
    for item in manifest.items:
        own = paths.scene / item.model if item.model else None
        has_own = bool(own and own.is_file())
        dress = dressed.get(item.id, {})
        # A scan box has no front; the dressing names it where the wall rule
        # guesses wrong (an island's drawers face the kitchen, not a wall).
        front = list(dress.get("front") or facing(item, room_of[item.roomId]))
        centre, size = list(item.box.center), list(item.box.size)
        if "front" in dress:
            size = sized_for(front, size, item.box.yaw)
        if "min" in dress:
            centre, size = box_from_corners(dress["min"], dress["max"], front)
        finish = None if has_own else dress.get("finish")
        items.append(
            {
                "id": item.id,
                "label": item.label,
                "center": centre,
                "size": size,
                "front": front,
                # A finish draws the piece from parts; the piece's own model still wins.
                "finish": finish,
                "options": dress.get("options", {}),
                "model": str(own) if has_own else (None if finish else stand_in(item, furniture)),
                "own": has_own,
            }
        )
    for extra in dressing.get("extras", []):
        centre, size = box_from_corners(extra["min"], extra["max"], extra["front"])
        items.append({"id": extra["id"], "label": extra["finish"], "center": centre,
                      "size": size, "front": list(extra["front"]), "finish": extra["finish"],
                      "options": extra.get("options", {}), "model": None, "own": False})

    wanted = dressing.get("roofWindows", [])
    skylights = roof_windows(rooms[0]["ceilings"], wanted) if rooms else []
    light = None
    if skylights:
        n = skylights[0]["frame"]["n"]
        across = math.hypot(n[0], n[2])
        light = sun(dressing, (n[0] / across, n[2] / across))
    return {
        "rooms": rooms,
        "items": items,
        "floors": floors,
        "skylights": skylights,
        "outside": outside_view(paths, wanted),
        "surfaces": surfaces(paths),
        "sun": light,
        "hero": dressing.get("hero"),
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
