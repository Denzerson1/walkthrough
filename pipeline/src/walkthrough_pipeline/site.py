"""
The flat's surroundings, from OpenStreetMap: the buildings around it as
blocks at their real floor counts, placed where they stand relative to the
flat, so the views out of its windows are the real ones from wherever one
stands in the room.

OSM data is © OpenStreetMap contributors (ODbL): free to use commercially,
with attribution. Vienna's building outlines and floor counts in it come
from the city's open data (source=wien.at).

Placement needs two facts the scan cannot give: which building the flat is
in (the OSM way id) and the flat's floor height above the street. The side
of that building the windows look out of is found from the windows' compass
bearing; the scan's window wall is laid on that side.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from . import flat_geometry

GROUND_FLOOR_M = 4.2  # Gründerzeit ground floors are tall
FLOOR_M = 3.7
ROOF_FLOOR_M = 3.4
DEFAULT_LEVELS = 3  # an untagged building is mostly a courtyard wing
METRES_PER_DEG_LAT = 110540.0
METRES_PER_DEG_LON_EQUATOR = 111320.0


def load(path: Path) -> tuple[dict[int, tuple[float, float]], list[dict]]:
    """Nodes (id -> lat, lon) and building ways from an Overpass JSON answer."""
    data = json.loads(path.read_text(encoding="utf-8"))
    nodes = {e["id"]: (e["lat"], e["lon"]) for e in data["elements"] if e["type"] == "node"}
    ways = [e for e in data["elements"]
            if e["type"] == "way" and "building" in e.get("tags", {}) and len(e["nodes"]) > 3]
    return nodes, ways


def to_metres(lat: float, lon: float, origin: tuple[float, float]) -> tuple[float, float]:
    """A point as metres east and north of `origin` (lat, lon); flat-earth, fine over a block."""
    lat0, lon0 = origin
    return ((lon - lon0) * math.cos(math.radians(lat0)) * METRES_PER_DEG_LON_EQUATOR,
            (lat - lat0) * METRES_PER_DEG_LAT)


def outline(way: dict, nodes: dict, origin: tuple[float, float]) -> list[list[float]]:
    """A way's footprint in metres (east, north), counter-clockwise, unclosed."""
    poly = [list(to_metres(*nodes[n], origin)) for n in way["nodes"][:-1]]
    return poly if flat_geometry.signed_area(poly) > 0 else poly[::-1]


def facing_side(poly: list[list[float]], bearing: float) -> tuple[list[float], list[float]]:
    """The edge of a footprint whose outward side faces closest to `bearing`, start to end
    with the building on its left (counter-clockwise order)."""
    best, best_off = None, math.inf
    for i in range(len(poly)):
        a, b = poly[i], poly[(i + 1) % len(poly)]
        ex, ey = b[0] - a[0], b[1] - a[1]
        if math.hypot(ex, ey) < 3:
            continue
        out = math.degrees(math.atan2(ey, -ex)) % 360  # outward normal (ey, -ex) as a bearing
        off = abs((out - bearing + 180) % 360 - 180)
        if off < best_off:
            best, best_off = (a, b), off
    if best is None:
        raise ValueError("The building has no side long enough to hold the flat")
    return best


def frame(edge, knee_wall_z: float, facade_offset: float, along: float = 0.0) -> dict:
    """
    How metres (east, north) map to scene (x, z): the scene's +z (the way
    the windows face) is the edge's outward normal, the knee wall lies
    `facade_offset` inside the facade line, and the flat's x = 0 sits
    `along` metres from the edge's middle.
    """
    (ax, ay), (bx, by) = edge
    length = math.hypot(bx - ax, by - ay)
    tx, ty = (bx - ax) / length, (by - ay) / length  # along the edge
    nx, ny = ty, -tx  # outward: the building is to the edge's left
    mx, my = (ax + bx) / 2 + tx * along, (ay + by) / 2 + ty * along
    # Scene +x runs along the edge, start to end: seen from inside looking
    # out, that is to the left, as scene +x is when looking along +z.
    return {"origin": [mx, my], "x": [tx, ty], "z": [nx, ny],
            "z0": knee_wall_z + facade_offset,
            "facing": math.degrees(math.atan2(nx, ny)) % 360}


def to_scene(point, f: dict) -> list[float]:
    dx, dy = point[0] - f["origin"][0], point[1] - f["origin"][1]
    return [round(dx * f["x"][0] + dy * f["x"][1], 3),
            round(dx * f["z"][0] + dy * f["z"][1] + f["z0"], 3)]


def heights(tags: dict) -> tuple[float, float]:
    """Eaves and ridge height above the street, from floor counts."""
    try:
        levels = max(1, round(float(tags.get("building:levels", DEFAULT_LEVELS))))
    except ValueError:
        levels = DEFAULT_LEVELS
    try:
        roof = float(tags.get("roof:levels", 1 if tags.get("roof:shape") != "flat" else 0))
    except ValueError:
        roof = 1.0
    eaves = GROUND_FLOOR_M + (levels - 1) * FLOOR_M
    return eaves, eaves + max(roof * ROOF_FLOOR_M, 0.6)


def surroundings(osm: Path, origin: tuple[float, float], building: int, bearing: float,
                 floor_height: float, knee_wall_z: float, facade_offset: float = 0.45,
                 along: float = 0.0, radius: float = 160.0) -> dict:
    """
    The buildings around the flat in scene space (metres, y up, the flat's
    floor at y = 0), and the bearing the flat's window side truly faces.
    Each: footprint (x, z), the street's height, eaves and roof top, and a
    colour index so neighbours differ. The flat's own building is left out:
    from inside it nothing of it shows, and its shell is the flat's.
    """
    nodes, ways = load(osm)
    own = next((w for w in ways if w["id"] == building), None)
    if own is None:
        raise ValueError(f"Building {building} is not in {osm.name}")
    f = frame(facing_side(outline(own, nodes, origin), bearing), knee_wall_z, facade_offset, along)
    out = []
    for w in ways:
        if w["id"] == building:
            continue
        poly = outline(w, nodes, origin)
        if min(math.hypot(*p) for p in poly) > radius:
            continue
        eaves, top = heights(w["tags"])
        scene = [to_scene(p, f) for p in poly]
        if flat_geometry.signed_area(scene) < 0:  # the frame flips handedness back
            scene = scene[::-1]
        out.append({
            "id": w["id"],
            "polygon": scene,
            "base": round(-floor_height, 3),
            "eaves": round(eaves - floor_height, 3),
            "top": round(top - floor_height, 3),
            "colour": w["id"] % 5,
            "flat": w["tags"].get("roof:shape") == "flat",
        })
    return {"buildings": out, "facing": round(f["facing"], 1), "street": round(-floor_height, 3)}
