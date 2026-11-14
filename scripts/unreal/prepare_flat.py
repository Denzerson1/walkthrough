"""
flat-02 for Unreal: the web flat's model without what only the browser needs,
and one scene file with everything the editor script places it by.

- `flat.glb`: `scene/flat.glb` without the painted backdrop outside the
  windows (Google's tiles are the outside now) and without the baked light
  sets (`_LIGHT_*`): Unreal lights the flat itself.
- `scene.json`: where the flat stands (site.json), its outline in metres
  east/north (the ground under it is measured there), the sun's date and
  time, a few node centres in glTF space (so the editor script can measure
  how its importer turned the axes), and the cameras.

Run:  python scripts/unreal/prepare_flat.py [project]   (stdlib only)
Out:  data/projects/<project>/unreal/
"""

from __future__ import annotations

import json
import math
import struct
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DROP_NODES = {"backdrop-houses", "backdrop-street"}
#: Nodes whose centres pin the importer's axes (spread over the flat, all three axes).
PINS = ["balcony-floor", "yucca", "tv-1", "bed-1", "storage-21-basin", "ceiling"]


def read_glb(path: Path) -> tuple[dict, bytes]:
    data = path.read_bytes()
    length = struct.unpack_from("<I", data, 12)[0]
    gltf = json.loads(data[20:20 + length])
    rest = data[20 + length:]
    bin_length = struct.unpack_from("<I", rest, 0)[0]
    return gltf, rest[8:8 + bin_length]


def write_glb(path: Path, gltf: dict, binary: bytes) -> None:
    text = json.dumps(gltf, separators=(",", ":")).encode()
    text += b" " * (-len(text) % 4)
    binary += b"\0" * (-len(binary) % 4)
    body = (struct.pack("<II", len(text), 0x4E4F534A) + text
            + struct.pack("<II", len(binary), 0x004E4942) + binary)
    path.write_bytes(struct.pack("<III", 0x46546C67, 2, 12 + len(body)) + body)


def quat_rotate(q: list[float], v: list[float]) -> list[float]:
    x, y, z, w = q
    tx, ty, tz = 2 * (y * v[2] - z * v[1]), 2 * (z * v[0] - x * v[2]), 2 * (x * v[1] - y * v[0])
    return [v[0] + w * tx + y * tz - z * ty, v[1] + w * ty + z * tx - x * tz,
            v[2] + w * tz + x * ty - y * tx]


def node_centre(gltf: dict, node: dict) -> list[float]:
    """The centre of a root node's mesh bounds, in glTF (scene) space."""
    lo, hi = [math.inf] * 3, [-math.inf] * 3
    for prim in gltf["meshes"][node["mesh"]]["primitives"]:
        acc = gltf["accessors"][prim["attributes"]["POSITION"]]
        for corner in range(8):
            p = [(acc["max"] if corner >> k & 1 else acc["min"])[k] for k in range(3)]
            p = [p[k] * node.get("scale", [1, 1, 1])[k] for k in range(3)]
            p = quat_rotate(node.get("rotation", [0, 0, 0, 1]), p)
            p = [p[k] + node.get("translation", [0, 0, 0])[k] for k in range(3)]
            lo = [min(lo[k], p[k]) for k in range(3)]
            hi = [max(hi[k], p[k]) for k in range(3)]
    return [round((lo[k] + hi[k]) / 2, 4) for k in range(3)]


def offset_outline(poly: list[list[float]], d: float) -> list[list[float]]:
    """A polygon grown by `d` (mitred corners)."""
    area = sum(a[0] * b[1] - b[0] * a[1]
               for a, b in zip(poly, poly[1:] + poly[:1], strict=True)) / 2
    sign = 1 if area > 0 else -1  # outward normal of a ccw edge (dx, dy) is (dy, -dx)
    n = len(poly)
    lines = []
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dy)
        nx, ny = sign * dy / length, -sign * dx / length
        lines.append(((a[0] + nx * d, a[1] + ny * d), (dx, dy)))
    out = []
    for i in range(n):
        (p, r), (q, s) = lines[i - 1], lines[i]
        denom = r[0] * s[1] - r[1] * s[0]
        if abs(denom) < 1e-9:
            out.append([q[0], q[1]])
            continue
        t = ((q[0] - p[0]) * s[1] - (q[1] - p[1]) * s[0]) / denom
        out.append([round(p[0] + t * r[0], 4), round(p[1] + t * r[1], 4)])
    return out


def to_east_north(x: float, z: float, bearings: dict) -> list[float]:
    """Scene (x, z) as metres east and north of the scene origin."""
    bx, bmz = math.radians(bearings["plusX_endWall"]), math.radians(bearings["minusZ_balcony"])
    return [round(x * math.sin(bx) - z * math.sin(bmz), 4),
            round(x * math.cos(bx) - z * math.cos(bmz), 4)]


def main(project: str = "flat-02") -> None:
    root = REPO / "data" / "projects" / project
    site = json.loads((root / "input" / "site.json").read_text(encoding="utf-8"))
    dressing = json.loads((root / "input" / "dressing.json").read_text(encoding="utf-8"))
    gltf, binary = read_glb(root / "scene" / "flat.glb")

    names = {i: n.get("name") for i, n in enumerate(gltf["nodes"])}
    pins = {names[i]: node_centre(gltf, n) for i, n in enumerate(gltf["nodes"])
            if names[i] in PINS and "mesh" in n}
    gltf["scenes"][0]["nodes"] = [i for i in gltf["scenes"][0]["nodes"]
                                  if names[i] not in DROP_NODES]
    for i, node in enumerate(gltf["nodes"]):
        if names[i] in DROP_NODES:
            node.pop("mesh", None)
    for node in gltf["nodes"]:
        # A sliding door is built shut (the web viewer slides it): here it stands open.
        door = (node.get("extras") or {}).get("door", {})
        if "slide" in door:
            node["translation"] = [node["translation"][k] + door["slide"][k] * door["open"]
                                   for k in range(3)]
    for mesh in gltf["meshes"]:
        for prim in mesh["primitives"]:
            prim["attributes"] = {k: v for k, v in prim["attributes"].items()
                                  if not k.startswith("_LIGHT")}

    plan = dressing["plan"]
    clip = offset_outline(plan["outline"], plan["wallT"] + 0.15)
    bearings = site["bearings"]
    scene = {
        "_about": "Written by scripts/unreal/prepare_flat.py; read by scripts/unreal/in_editor.py.",
        "origin": site["sceneOrigin"],
        "bearings": bearings,
        "floorStorey": site["floor"]["storey"],
        "render": site["render"],
        "pins": pins,
        "outlineEastNorth": [to_east_north(x, z, bearings) for x, z in clip],
        "cameras": {
            # Scene metres (x, y up, z). The still stands just inside the living room, by
            # the passage from the hall, and looks along it to the west window, the
            # balcony glazing on its left.
            "still": {"eye": [1.25, 1.5, 1.0], "target": [5.8, 1.05, 0.75], "focalMm": 16},
            # The walk: seconds, where the eye is, what it looks at. From the entry down
            # the hall, into the living room, towards the west window and the dining
            # table, then round to the balcony door and the street beyond it. Kept clear
            # of the furniture's footprints (the web flat's manifest).
            "walk": [
                {"t": 0.0, "eye": [-4.5, 1.55, 0.35], "look": [0.6, 1.4, 0.8]},
                {"t": 4.5, "eye": [-2.2, 1.55, 0.62], "look": [0.6, 1.4, 0.8]},
                {"t": 8.5, "eye": [-0.2, 1.55, 0.75], "look": [3.0, 1.3, 0.9]},
                {"t": 11.0, "eye": [1.0, 1.55, 0.8], "look": [5.5, 1.2, 0.6]},
                {"t": 13.5, "eye": [2.1, 1.55, 0.95], "look": [5.9, 1.1, 1.6]},
                {"t": 15.5, "eye": [2.6, 1.55, 1.75], "look": [6.2, 1.1, 2.4]},
                {"t": 18.5, "eye": [3.9, 1.55, 1.8], "look": [6.2, 1.2, 3.2]},
                {"t": 21.5, "eye": [3.9, 1.55, 1.8], "look": [3.5, 1.0, -1.3]},
                {"t": 25.0, "eye": [2.65, 1.55, 1.3], "look": [2.75, 1.2, -3.0]},
                {"t": 30.0, "eye": [2.7, 1.55, -0.6], "look": [2.6, 1.0, -6.0]},
            ],
        },
    }
    out = root / "unreal"
    out.mkdir(exist_ok=True)
    write_glb(out / "flat.glb", gltf, binary)
    (out / "scene.json").write_text(json.dumps(scene, indent=1), encoding="utf-8")
    print(f"{out / 'flat.glb'}: {len(gltf['scenes'][0]['nodes'])} nodes; pins {sorted(pins)}")


if __name__ == "__main__":
    main(*sys.argv[1:2])
