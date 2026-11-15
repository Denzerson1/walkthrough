"""
The houses around a flat as simple blocks in its dressing, from OpenStreetMap
(ODbL, (c) OpenStreetMap contributors): their outlines where they really
stand, as seen from the flat's windows, until photos out of those windows
replace them (the dressing's `viewPhotos`).

Heights: OSM gives no floor count around flat-02, so a block is as tall as
a typical house of its footprint: a shed 2.6 m, a house under 250 m^2 two
storeys (6.8 m), larger three (9.6 m). An assumption, written into each
block. Google's tiles are not used for heights: their terms forbid taking
data out of them.

Run:  python scripts/osm_neighbours.py flat-02 [radius_m]
Reads data/projects/<p>/work/site/buildings.json (an Overpass answer) and
input/site.json; replaces the dressing's `osm-*` extras.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
METRES_PER_DEG_LAT = 110540.0
METRES_PER_DEG_LON_EQUATOR = 111320.0
#: The flat's floor above the ground (site.json `floor`: plinth + one storey).
FLOOR_ABOVE_GROUND = 3.16


def height(area: float, kind: str) -> float:
    if kind in ("shed", "garage", "garages", "carport", "hut"):
        return 2.6
    return 6.8 if area < 250 else 9.6


def scene_xz(east: float, north: float, bearings: dict) -> tuple[float, float]:
    """Metres east/north of the scene origin as scene (x, z): the inverse of the bearings."""
    bx, bmz = math.radians(bearings["plusX_endWall"]), math.radians(bearings["minusZ_balcony"])
    return (east * math.sin(bx) + north * math.cos(bx),
            -(east * math.sin(bmz) + north * math.cos(bmz)))


def blocks(osm: dict, site: dict, own: int, radius: float) -> list[dict]:
    lat0, lon0 = site["sceneOrigin"]["latitude"], site["sceneOrigin"]["longitude"]
    kx = math.cos(math.radians(lat0)) * METRES_PER_DEG_LON_EQUATOR
    nodes = {e["id"]: (e["lat"], e["lon"]) for e in osm["elements"] if e["type"] == "node"}
    out = []
    for way in osm["elements"]:
        if way["type"] != "way" or way["id"] == own or "building" not in way.get("tags", {}):
            continue
        pts = [scene_xz((nodes[n][1] - lon0) * kx, (nodes[n][0] - lat0) * METRES_PER_DEG_LAT,
                        site["bearings"]) for n in way["nodes"][:-1] if n in nodes]
        if len(pts) < 3 or min(math.hypot(*p) for p in pts) > radius:
            continue
        area = abs(sum(a[0] * b[1] - b[0] * a[1]
                       for a, b in zip(pts, pts[1:] + pts[:1], strict=True))) / 2
        xs, zs = [p[0] for p in pts], [p[1] for p in pts]
        # The outline's box in scene axes, shrunk to the outline's own area: the
        # neighbours stand within a few degrees of the flat's grid.
        cx, cz = (min(xs) + max(xs)) / 2, (min(zs) + max(zs)) / 2
        w, d = max(xs) - min(xs), max(zs) - min(zs)
        k = math.sqrt(area / max(w * d, 1e-6))
        w, d = w * k, d * k
        h = height(area, way["tags"]["building"])
        out.append({
            "id": f"osm-{way['id']}", "finish": "backdrop",
            "min": [round(cx - w / 2, 2), -FLOOR_ABOVE_GROUND, round(cz - d / 2, 2)],
            "max": [round(cx + w / 2, 2), round(h - FLOOR_ABOVE_GROUND, 2), round(cz + d / 2, 2)],
            "front": [0, 1], "options": {"surface": "facade"},
            "_source": f"OSM way {way['id']} (ODbL); height {h} m assumed from its footprint",
        })
    return out


def main(project: str, radius: float = 120.0) -> None:
    root = REPO / "data" / "projects" / project
    site = json.loads((root / "input" / "site.json").read_text(encoding="utf-8"))
    osm = json.loads((root / "work" / "site" / "buildings.json").read_text(encoding="utf-8"))
    path = root / "input" / "dressing.json"
    dressing = json.loads(path.read_text(encoding="utf-8"))
    new = blocks(osm, site, site["osm"]["way"], radius)
    dressing["extras"] = [e for e in dressing["extras"]
                          if not str(e.get("id", "")).startswith("osm-")] + new
    path.write_text(json.dumps(dressing, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{len(new)} neighbours within {radius:.0f} m")


if __name__ == "__main__":
    main(sys.argv[1], *(float(a) for a in sys.argv[2:3]))
