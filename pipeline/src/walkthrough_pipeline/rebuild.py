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
finish of each piece (or its own model, `"model"`: a .glb in the project's
folder), built-ins the scanner missed, and the preview's view.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
from pathlib import Path

import numpy as np

from . import flat_geometry, photo_texture, roomplan, site
from .manifest import Manifest, ManifestItem, Opening, Room, Wall
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
    "stone": ("#8b8a87", 0.45, "stone"),     # concrete-look worktop (kitchentray1-3)
    "alu": ("#a3a4a2", 0.35, None),          # grip channels
    "steel": ("#b6b7b5", 0.3, None),         # handles, tap, oven trim
    "black": ("#0f1011", 0.15, None),        # hob, oven glass, TV
    "basin": ("#f3f3f1", 0.55, None),
    "oak": ("#d3c0a5", 0.55, "veneer"),      # the wardrobe: pale oak
    "oak-warm": ("#a98b6b", 0.5, "veneer"),  # kitchen backsplash, island, shelves
    "wenge": ("#2f231e", 0.5, "veneer"),     # the bed base
    "linen": ("#f2f1ee", 0.9, "linen"),
    "boucle": ("#e8e3d9", 1.0, "boucle"),
    "fabric": ("#8c8884", 0.9, None),        # bar stools: a smooth grey suede
    "beech": ("#c4915f", 0.45, None),        # the stools' legs
    "coat-light": ("#b3a792", 0.95, None),   # clothes on the rails: a pale trench,
    "coat-navy": ("#262c38", 0.95, None),    # a navy wool coat,
    "coat-camel": ("#9c7a58", 0.95, None),   # a camel one,
    "leather": ("#4a1f22", 0.45, None),      # the burgundy leather jacket (garderobe1),
    "denim": ("#44556e", 0.9, None),         # and a denim jacket;
    "lining": ("#3b3633", 0.8, None),        # their insides, seen in the V
    "shade": ("#f3efe5", 0.8, None),
    "glass-shelf": ("#dfe7e6", 0.05, None),
    "frame": ("#6b5038", 0.5, None),
    "picture": ("#e6e1d6", 0.8, None),
    "vent": ("#8d8f91", 0.5, None),
    "basket": ("#7d7f80", 0.4, None),
    "boxes": ("#bdb8b0", 0.9, None),         # felt boxes in the wardrobe
    # flat-02's pieces (build_flat.PIECES)
    "mat": ("#202021", 0.95, None),          # the shoes' mat
    "glass-frost": ("#dfe3e1", 0.15, None),  # glazed kitchen doors
    "handle": ("#151516", 0.35, None),       # black bar handles
    "screen": ("#0b0c0e", 0.1, None),        # a TV's or a monitor's glass
    "mirror": ("#3b3f40", 0.05, None),       # the viewer reflects the room in it
    "paper": ("#f4f4f2", 0.8, None),
    "box-white": ("#ececea", 0.5, None),
    "feet": ("#a07a50", 0.5, None),
    "frame-white": ("#f2f2f0", 0.4, None),
    "felt": ("#19191a", 1.0, None),
    "bamboo": ("#d2a462", 0.5, "veneer"),
    "towel-grey": ("#4b4d50", 1.0, "boucle"),
    "robe": ("#d9cdb8", 1.0, "boucle"),
    "puffer": ("#1b1c1f", 0.5, None),
    "brass": ("#b08d4f", 0.3, None),
    "mat-white": ("#f4f2ee", 0.9, None),
}

#: A picture whose photo has not been cut out yet: a neutral canvas.
PLACEHOLDER = "#c8c4bc"


def surfaces(paths: ProjectPaths, photos: dict | None = None,
             palette: dict | None = None, pictures: dict | None = None) -> dict[str, dict]:
    """
    The surfaces as build_flat.py takes them, textures written into the
    scene folder. A surface the dressing gives a photo patch for is cut from
    the customer's photo of it (photo_texture.photo_patch); the rest get a
    generated texture or a plain colour. `tile` is the metres one texture
    repeat spans. The dressing's `palette` adds surfaces of the flat's own
    (name: [colour, roughness, generated texture or null]), which a piece's
    `options.surfaces` draws its parts in.
    """
    photos = photos or {}
    out = {}
    table = {**SURFACES, **{k: tuple(v) for k, v in (palette or {}).items()}}
    for i, (name, (colour, roughness, kind)) in enumerate(table.items()):
        texture, tile = None, [1.0, 1.0]
        path = paths.scene / f"surface-{name}.jpg"
        if name in photos:
            spec = photos[name]
            image, size = photo_texture.photo_patch(
                repo_root() / spec["photo"], tuple(spec["box"]), tuple(spec["size"]),
                tuple(spec["wall"]) if spec.get("wall") else None,
                spec.get("colour"), spec.get("rotate", False),
            )
            image.save(path, quality=92)
            texture, tile = str(path), list(size)
        elif kind == "tiles":
            image, size = photo_texture.tile_surface(colour, seed=i)
            image.save(path, quality=92)
            texture, tile = str(path), list(size)
        elif kind == "facade":  # one bay of one floor of a house front, tiling
            photo_texture.facade_texture(colour, seed=i).save(path, quality=88)
            texture, tile = str(path), [photo_texture.BAY_M, photo_texture.STOREY_M]
        elif kind:
            photo_texture.surface_texture(kind, colour, seed=i).save(path, quality=90)
            texture = str(path)
        out[name] = {"colour": colour, "roughness": roughness, "texture": texture, "tile": tile}
    for name, spec in (pictures or {}).items():
        out[name] = picture(paths, name, spec)
    return out


def picture(paths: ProjectPaths, name: str, spec: dict) -> dict:
    """
    A picture on the wall cut from the owner's photo of it (the dressing's
    `pictures`): its `corners` in the photo (fractions, clockwise from top
    left) straightened to its `size` in metres, white-balanced on a `wall`
    box of white wall beside it; `cutout` keeps only what is darker than the
    wall (the clock's metal), as alpha. Without corners, a neutral canvas.
    """
    if not spec.get("corners"):
        return {"colour": PLACEHOLDER, "roughness": 0.7, "texture": None, "tile": [1.0, 1.0]}
    image = photo_texture.photo_picture(repo_root() / spec["photo"], spec["corners"],
                                        tuple(spec["size"]), spec.get("wall"),
                                        spec.get("cutout", False))
    path = paths.scene / f"surface-{name}.{'png' if spec.get('cutout') else 'jpg'}"
    image.save(path, **({} if spec.get("cutout") else {"quality": 93}))
    return {"colour": "#ffffff", "roughness": 0.7, "texture": str(path),
            "tile": list(spec["size"]), "cutout": bool(spec.get("cutout"))}


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


#: Plaster colours the neighbours are painted in (Viennese cream, ochre,
#: grey), and their clay roofs.
# Sampled from the owner's photos out of the windows (shade lifted to
# albedo): pale stone, grey render, a cream, an ochre; weathered dark tiles.
FACADE_COLOURS = ("#d3cbb7", "#bfb9a8", "#e0dccb", "#cdc3a6", "#a9a596")
ROOF_TILES = "#5e4b43"


def site_spec(paths: ProjectPaths, dressing: dict, knee_wall_z: float) -> dict | None:
    """The buildings around the flat from OpenStreetMap, with their textures (see site.py)."""
    place = dressing.get("site")
    if not place:
        return None
    data = site.surroundings(
        repo_root() / place["osm"], tuple(place["origin"]), place["building"],
        dressing["windowsFace"], place["floorHeight"], knee_wall_z,
        place.get("facadeOffset", 0.45), place.get("along", 0.0),
    )
    facades = []
    for i, colour in enumerate(FACADE_COLOURS):
        path = paths.scene / f"facade-{i}.jpg"
        photo_texture.facade_texture(colour, seed=i).save(path, quality=88)
        facades.append(str(path))
    tiles = paths.scene / "roof-tiles.jpg"
    photo_texture.roof_tile_texture(ROOF_TILES).save(tiles, quality=88)
    return {**data, "facades": facades, "tiles": str(tiles),
            "bay": [photo_texture.BAY_M, photo_texture.STOREY_M],
            "groundFloor": site.GROUND_FLOOR_M}


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
    if "stone" in entry:  # tiles with no photo to re-lay them from: generated
        tiles = tuple(entry.get("tiles", (0.6, 0.3)))
        image, size = photo_texture.tile_surface(entry["stone"], tiles)
        out = paths.scene / f"floor-{name.lower()}.jpg"
        image.save(out, quality=92)
        return {"id": f"stone-{name.lower()}", "albedo": str(out), "normal": None,
                "roughness": None, "tile": list(size), "colour": entry["stone"]}
    source = repo_root() / entry["photo"]
    if "parquet" in entry:
        # Strip floors whose lattice a shot cannot give (flat-02): generated,
        # in the shot's own strip tones.
        image, size = photo_texture.parquet_texture(photo_texture.strip_colours(source),
                                                    entry.get("colour"), strip=entry["parquet"])
        out = paths.scene / f"floor-{name.lower()}.jpg"
        image.save(out, quality=92)
        return {"id": f"photo-{name.lower()}", "albedo": str(out), "normal": None,
                "roughness": None, "tile": list(size),
                "colour": entry.get("colour") or photo_texture.mean_albedo(source)}
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


def add_walls(room: Room, scan: dict, walls: list[dict]) -> None:
    """
    The dressing's `partitions`: inner walls the scan missed (one beside an
    open door can come out as no wall at all), `start` to `end` in plan,
    floor to ceiling, as the scanned ones are.
    """
    height = max((w.height for w in room.walls), default=2.5)
    for wall in walls:
        scan["partitions"].append({"start": list(wall["start"]), "end": list(wall["end"]),
                                   "height": round(height, 3), "openings": []})


def add_openings(room: Room, scan: dict, openings: list[dict]) -> None:
    """
    The dressing's `openings`: doors and windows the scan missed, each
    centred `at` a plan point, `width` and `height` in metres (a window's
    `sill` above the floor). A door on the outline is the outline's; one
    further in hangs in the partition it stands in, with how it is hung
    (`hinge`: "start" or "end" of the wall, `swing`: 1 or -1, the side of
    the wall it opens to). One on the outline that overlaps a scanned
    opening replaces it: the scan can take a balcony door for a window on
    a sill (flat-02's beside the sofa).
    """
    for o in openings:
        at = np.asarray(o["at"], dtype=float)
        index, offset, off = roomplan._nearest_wall(room.walls, at)
        if o["type"] == "window" or off <= roomplan.ON_OUTLINE_M:
            if off > roomplan.ON_OUTLINE_M:
                raise ValueError(f"The window at {o['at']} is not on the outline")
            # Openings that only touch (a balcony door and the fixed pane
            # beside it) both stay.
            room.openings = [x for x in room.openings if x.wallIndex != index
                             or abs(x.offset - offset) >= (x.width + o["width"]) / 2 - 1e-6]
            room.openings.append(Opening(
                type=o["type"], wallIndex=index, offset=round(offset, 3), width=o["width"],
                height=o["height"], sill=o.get("sill", 0.0 if o["type"] == "door" else None)))
        else:
            hung = {k: o[k] for k in ("hinge", "swing") if k in o}
            roomplan.place_door(scan["partitions"], at, o["width"], o["height"], **hung)


def apply_plan(room: Room, scan: dict, plan: dict) -> None:
    """
    The flat as its architect's floor plan draws it, in place of the scan's
    walls where the two disagree by more than a scan's error (flat-02's
    living room came out 41 cm deeper than built): `outline`, the outer
    walls' inner faces in the scan's turning sense, and its `openings` (as
    add_openings takes them); `partitions`, inner walls from end to end on
    their centre lines, each with its `thickness` and `doors` (centred `at`
    a plan point, `width`, `height`, how hung); the floor `zones`. The
    ceiling is one flat slab at the scanned height; the scan's furniture
    stays.
    """
    height = round(max((w.height for w in room.walls), default=2.5), 3)
    outline = [(float(x), float(z)) for x, z in plan["outline"]]
    room.floorPolygon = outline
    room.walls = [Wall(start=a, end=outline[(i + 1) % len(outline)], height=height)
                  for i, a in enumerate(outline)]
    room.openings = []
    scan["ceilings"] = []
    scan["zones"] = [(z["label"], [list(p) for p in z["polygon"]]) for z in plan.get("zones", [])]
    scan["partitions"] = []
    for p in plan.get("partitions", []):
        (sx, sz), (ex, ez) = p["start"], p["end"]
        length = math.hypot(ex - sx, ez - sz)
        doors = []
        for o in p.get("doors", []):
            ax, az = o["at"]
            along = ((ax - sx) * (ex - sx) + (az - sz) * (ez - sz)) / length
            hung = {k: v for k, v in o.items() if k not in ("at", "width", "height")}
            doors.append({"type": "door", "offset": round(along, 3), "width": o["width"],
                          "height": o["height"], "bottom": 0.0, **hung})
        scan["partitions"].append({"start": [sx, sz], "end": [ex, ez], "height": height,
                                   "thickness": p["thickness"], "openings": doors})
    add_openings(room, scan, plan.get("openings", []))


def with_partitions(room: Room, partitions: list[dict]) -> Room:
    """
    The room with its partitions as walls too, their doors as its openings:
    the walk then stops at a flat's inner walls but for their doorways, and
    the rooms the dressing cuts it into each keep the walls along their
    edges (room_split).
    """
    walls, openings = list(room.walls), list(room.openings)
    for p in partitions:
        walls.append(Wall(start=tuple(p["start"]), end=tuple(p["end"]), height=p["height"]))
        openings += [Opening(type="door", wallIndex=len(walls) - 1, offset=o["offset"],
                             width=o["width"], height=o["height"], sill=o["bottom"])
                     for o in p.get("openings", [])]
    return room.model_copy(update={"walls": walls, "openings": openings})


def widen(room: Room, items: list[ManifestItem], scan: dict, at: float, by: float) -> None:
    """
    Let a strip `by` metres deep into the scanned plan at z = `at`, where the
    owner knows the flat is roomier than it came out: all past it moves on
    by `by` (walls, slopes, pieces, whole, by their centre) and the walls
    across it grow longer, their openings staying where they were.
    """

    def z(v: float) -> float:
        return v + by if v > at else v

    def plan(p) -> tuple[float, float]:
        return (p[0], z(p[1]))

    before = [(w.start, w.end) for w in room.walls]
    room.floorPolygon = [plan(p) for p in room.floorPolygon]
    for w in room.walls:
        w.start, w.end = plan(w.start), plan(w.end)
    for o in room.openings:
        (sx, sz), (ex, ez) = before[o.wallIndex]
        t = o.offset / math.hypot(ex - sx, ez - sz)
        cx, cz = plan((sx + (ex - sx) * t, sz + (ez - sz) * t))
        (sx, sz), (ex, ez) = room.walls[o.wallIndex].start, room.walls[o.wallIndex].end
        o.offset = ((cx - sx) * (ex - sx) + (cz - sz) * (ez - sz)) / math.hypot(ex - sx, ez - sz)
    x, y, wz = room.waypoint.position
    room.waypoint.position = (x, y, z(wz))
    for item in items:
        x, y, iz = item.box.center
        item.box.center = (x, y, z(iz))
    scan["ceilings"] = [[[x, y, z(v)] for x, y, v in piece] for piece in scan["ceilings"]]
    scan["partitions"] = [{**p, "start": list(plan(p["start"])), "end": list(plan(p["end"]))}
                          for p in scan["partitions"]]
    scan["zones"] = [(label, [list(plan(p)) for p in polygon]) for label, polygon in scan["zones"]]


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


def sun(dressing: dict, facing_dir: tuple[float, float], bearing: float | None = None,
        time: str | None = None) -> dict | None:
    """
    The sun at the dressing's date and `time` (default its "time"): a scene
    direction towards it, its colour and strength, the sky's strength, and
    a tint for the sky dome. `bearing` is the true bearing of the windows'
    side, when the map gives it; else the dressing's compass reading.

    Sunlight warms as the sun sinks, through more air: warm white high up,
    gold near the horizon, gone below it, the sky dimming into dusk.
    """
    place, when = dressing.get("location"), dressing.get("sun")
    if not place or not when or "windowsFace" not in dressing:
        return None
    facing = dressing["windowsFace"] if bearing is None else bearing
    time = time or when["time"]
    year, month, day = (int(v) for v in when["date"].split("-"))
    hours, minutes = (int(v) for v in time.split(":"))
    bearing, elevation = flat_geometry.sun_position(
        year, month, day, hours + minutes / 60, place["utcOffset"],
        place["latitude"], place["longitude"],
    )
    x, z = flat_geometry.compass_to_scene(bearing, facing, facing_dir)
    e = math.radians(elevation)
    high = min(max((elevation - 2) / 40, 0.0), 1.0) ** 0.7
    colour = [1.0, 0.56 + (0.84 - 0.56) * high, 0.30 + (0.66 - 0.30) * high]
    energy = 5.5 * min(max((elevation + 1) / 12, 0.0), 1.0) * (0.75 + 0.25 * high)
    dusk = min(max((elevation + 6) / 16, 0.12), 1.0)
    tint = [1.0, 0.8 + 0.2 * high, 0.68 + 0.32 * high]
    return {"time": time, "bearing": round(bearing, 1), "elevation": round(elevation, 1),
            "direction": [x * math.cos(e), math.sin(e), z * math.cos(e)],
            "colour": [round(c, 3) for c in colour], "energy": round(energy, 3),
            "sky": round(0.5 * dusk, 3), "tint": [round(c * dusk, 3) for c in tint]}


#: Minutes between the samples of the sun's path the viewer moves the sun along.
SUN_PATH_STEP = 10


def sun_path(dressing: dict, facing_dir: tuple[float, float],
             bearing: float | None = None) -> list[dict]:
    """
    The sun every SUN_PATH_STEP minutes from the first baked time to the
    last: its light is drawn live in the viewer, so it moves as the time of
    day does instead of fading between where it stood at two bakes.
    """
    times = [clock_minutes(t) for t in (dressing.get("sun") or {}).get("times", [])]
    if not times:
        return []
    path = []
    for minutes in [*range(min(times), max(times), SUN_PATH_STEP), max(times)]:
        entry = sun(dressing, facing_dir, bearing, f"{minutes // 60:02d}:{minutes % 60:02d}")
        if entry:
            path.append({"minutes": minutes, "direction": entry["direction"],
                         "colour": entry["colour"], "energy": entry["energy"]})
    return path


def clock_minutes(time: str) -> int:
    """"17:30" -> 1050."""
    hours, minutes = (int(v) for v in time.split(":"))
    return hours * 60 + minutes


def own_model(paths: ProjectPaths, entry: dict) -> Path | None:
    """The piece's own model, when the dressing names one (relative to the project's folder)."""
    if not entry.get("model"):
        return None
    path = paths.root / entry["model"]
    if not path.is_file():
        raise FileNotFoundError(f"The dressing names a model that is not there: {path}")
    return path


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
                                # How the door is hung (`latch` side, `frame`),
                                # from the photos: the scan cannot tell.
                                **(dressing.get("doors", {}).get(str(i), {})
                                   if o.type == "door" else {}),
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
    if rooms and (dressing.get("plan") or {}).get("wallT"):
        rooms[0]["wallT"] = dressing["plan"]["wallT"]  # the outer walls' thickness, drawn
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
        dress = dressed.get(item.id, {})
        own = own_model(paths, dress) or (paths.scene / item.model if item.model else None)
        has_own = bool(own and own.is_file())
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
        own = own_model(paths, extra)
        items.append({"id": extra["id"], "label": extra["finish"], "center": centre,
                      "size": size, "front": list(extra["front"]),
                      "finish": None if own else extra["finish"],
                      "options": extra.get("options", {}),
                      "model": str(own) if own else None, "own": bool(own)})

    wanted = dressing.get("roofWindows", [])
    skylights = roof_windows(rooms[0]["ceilings"], wanted) if rooms else []
    surroundings = (site_spec(paths, dressing, max(p[1] for p in rooms[0]["polygon"]))
                    if rooms else None)
    light, suns, path, windows_face = None, [], [], None
    if skylights:
        n = skylights[0]["frame"]["n"]
        across = math.hypot(n[0], n[2])
        facing_dir = (n[0] / across, n[2] / across)
        true_bearing = surroundings["facing"] if surroundings else None
        light = sun(dressing, facing_dir, true_bearing)
        times = (dressing.get("sun") or {}).get("times", [])
        suns = [sun(dressing, facing_dir, true_bearing, t) for t in times]
        path = sun_path(dressing, facing_dir, true_bearing)
        windows_face = {"dir": list(facing_dir), "bearing": true_bearing}
    return {
        "rooms": rooms,
        "items": items,
        "floors": floors,
        "skylights": skylights,
        "lights": dressing.get("lights", []),
        "strips": dressing.get("strips", []),
        "outside": outside_view(paths, wanted),
        # Open air outside the outline (a balcony): the walls facing it are baked.
        "outdoors": dressing.get("outdoors", []),
        "site": surroundings,
        "surfaces": surfaces(paths, dressing.get("surfaces"), dressing.get("palette"),
                             dressing.get("pictures")),
        "sun": light,
        "suns": suns,
        "sun_path": path,
        "facing": windows_face,
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
