"""
How detailed Google's Photorealistic 3D Tiles are at a place, read through
Cesium ion: the tile tree is walked down to the point and the finest tile's
geometric error and size are printed. Photogrammetry ends in tiles a few
tens of metres across with an error well under a metre; elsewhere the tree
stops at coarse terrain.

Only the tree's JSON is read, in memory; no tile content is fetched or
saved (Map Tiles API policies: no caching or offline use).

Run:  uv run python scripts/check_3d_tiles.py <lat> <lon> [<lat> <lon> ...]
      uv run python scripts/check_3d_tiles.py --site flat-02
Needs CESIUM_ION_TOKEN in .env.
"""

from __future__ import annotations

import json
import math
import sys
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
GOOGLE_TILES_ASSET = 2275207  # Cesium ion's id for Google Photorealistic 3D Tiles
A, E2 = 6378137.0, 6.69437999014e-3  # WGS84


def token() -> str:
    for line in (REPO / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("CESIUM_ION_TOKEN=") and line.split("=", 1)[1].strip():
            return line.split("=", 1)[1].strip()
    raise SystemExit("Set CESIUM_ION_TOKEN in .env")


def get(url: str, headers: dict | None = None) -> dict:
    agent = {"User-Agent": "walkthrough-tiles-check"}
    request = urllib.request.Request(url, headers={**agent, **(headers or {})})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read())


def ecef(lat: float, lon: float, h: float) -> tuple[float, float, float]:
    la, lo = math.radians(lat), math.radians(lon)
    n = A / math.sqrt(1 - E2 * math.sin(la) ** 2)
    return ((n + h) * math.cos(la) * math.cos(lo), (n + h) * math.cos(la) * math.sin(lo),
            (n * (1 - E2) + h) * math.sin(la))


def inside(box: list[float], p: tuple[float, float, float]) -> bool:
    """`p` in an oriented box (centre, then three half-axes), with 1 % slack."""
    d = [p[i] - box[i] for i in range(3)]
    for k in range(3):
        axis = box[3 + 3 * k: 6 + 3 * k]
        length2 = sum(a * a for a in axis)
        if length2 == 0 or abs(sum(d[i] * axis[i] for i in range(3))) > length2 * 1.01:
            return False
    return True


def size(box: list[float]) -> float:
    """The box's longest side, metres."""
    return 2 * max(math.sqrt(sum(a * a for a in box[3 + 3 * k: 6 + 3 * k])) for k in range(3))


def finest(lat: float, lon: float) -> dict:
    endpoint = get(f"https://api.cesium.com/v1/assets/{GOOGLE_TILES_ASSET}/endpoint",
                   {"Authorization": f"Bearer {token()}"})
    root_url = endpoint["options"]["url"]
    base = urllib.parse.urlsplit(root_url)
    query = dict(urllib.parse.parse_qsl(base.query))
    # Ground in Vienna is near 200 m on the ellipsoid; any height in a tile's box will do.
    points = [ecef(lat, lon, h) for h in range(-100, 3001, 50)]
    best = {"error": math.inf, "size": math.inf, "depth": 0, "requests": 1}

    def walk(tile: dict, depth: int) -> None:
        box = tile.get("boundingVolume", {}).get("box")
        if box and not any(inside(box, p) for p in points):
            return
        if box and tile.get("content") and tile["geometricError"] < best["error"]:
            best.update(error=tile["geometricError"], size=size(box), depth=depth)
        for child in tile.get("children", []):
            walk(child, depth + 1)
        uri = (tile.get("content") or {}).get("uri", "")
        if ".json" in uri and best["requests"] < 80:
            part = urllib.parse.urlsplit(uri)
            query.update(urllib.parse.parse_qsl(part.query))  # the session id
            url = urllib.parse.urlunsplit(
                (base.scheme, base.netloc, part.path, urllib.parse.urlencode(query), ""))
            best["requests"] += 1
            walk(get(url)["root"], depth + 1)

    walk(get(root_url)["root"], 0)
    return best


def main(args: list[str]) -> None:
    if args[:1] == ["--site"]:
        path = REPO / "data" / "projects" / args[1] / "input" / "site.json"
        site = json.loads(path.read_text(encoding="utf-8"))
        places = [(args[1], site["sceneOrigin"]["latitude"], site["sceneOrigin"]["longitude"])]
        rest = args[2:]
    else:
        places, rest = [], args
    places += [(f"{rest[i]},{rest[i + 1]}", float(rest[i]), float(rest[i + 1]))
               for i in range(0, len(rest) - 1, 2)]
    for name, lat, lon in places:
        found = finest(lat, lon)
        print(f"{name}: finest tile {found['size']:.0f} m across, "
              f"geometric error {found['error']:.2f} m, "
              f"depth {found['depth']}, {found['requests']} requests")


if __name__ == "__main__":
    main(sys.argv[1:])
