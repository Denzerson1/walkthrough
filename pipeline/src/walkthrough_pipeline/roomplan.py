"""
Apple RoomPlan import: walls, doors, windows and furniture boxes from a LiDAR
scan, turned into manifest rooms.

Two export formats are read:

* **JSON** — `CapturedRoom` encoded with `JSONEncoder`, which is what most
  RoomPlan apps offer as "JSON" and what Apple's sample app writes. Every
  element has `dimensions` and a column-major 4x4 `transform` whose first
  column is the element's right vector, second its up vector and fourth its
  centre.
* **USDZ** — `CapturedRoom.export(to:)`. Needs `usd-core`; elements are found
  by prim name (Wall0, Door1, Table0 ...) and measured from their bounds.

Both are reduced to the same `Element` list. RoomPlan's world frame is ARKit's:
metres, Y up, right-handed — the scene convention already — except that the
floor sits wherever the phone started, so `to_rooms` drops it to y = 0.
"""

from __future__ import annotations

import json
import math
import re
import struct
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .manifest import ItemBox, ManifestItem, Opening, Room, Wall, Waypoint

SURFACE_KINDS = ("walls", "doors", "windows", "openings", "floors")

#: RoomPlan object categories (CapturedRoom.Object.Category) to the layout
#: solver's vocabulary, so recolour and layout read them correctly.
OBJECT_LABELS = {
    "sofa": "sofa",
    "bed": "bed",
    "table": "table",
    "chair": "chair",
    "storage": "storage",
    "television": "tv",
    "refrigerator": "appliance",
    "stove": "appliance",
    "oven": "appliance",
    "dishwasher": "appliance",
    "washerDryer": "appliance",
    "sink": "fixture",
    "toilet": "fixture",
    "bathtub": "fixture",
    "fireplace": "fixture",
    "stairs": "fixture",
}

#: RoomPlan section labels (iOS 17+) to manifest room types.
SECTION_TYPES = {
    "bedroom": "bedroom",
    "livingRoom": "living",
    "kitchen": "kitchen",
    "diningRoom": "dining",
    "bathroom": "bathroom",
}

EYE_HEIGHT = 1.6


class RoomPlanError(ValueError):
    pass


@dataclass
class Element:
    kind: str  # walls | doors | windows | openings | floors | objects
    category: str
    transform: np.ndarray  # (4, 4), column-vector convention, world frame
    dimensions: np.ndarray  # (3,) metres: width, height, depth
    identifier: str = ""
    parent: str | None = None
    corners: np.ndarray | None = None  # polygon corners in local frame (floors)

    @property
    def centre(self) -> np.ndarray:
        return self.transform[:3, 3]

    @property
    def right(self) -> np.ndarray:
        r = self.transform[:3, 0]
        return r / max(float(np.linalg.norm(r)), 1e-12)


@dataclass
class Scan:
    elements: list[Element]
    room_type: str | None = None

    def of(self, kind: str) -> list[Element]:
        return [e for e in self.elements if e.kind == kind]


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------


def find_export(directory: Path) -> Path | None:
    """The RoomPlan file in an ingested `input/roomplan/` folder, JSON first."""
    if not directory.is_dir():
        return None
    for pattern in ("*.json", "*.usdz", "*.usda", "*.usdc", "*.usd", "*.glb", "*.gltf"):
        found = sorted(directory.rglob(pattern))
        if found:
            return found[0]
    return None


def load(path: Path) -> Scan:
    suffix = path.suffix.lower()
    if suffix == ".json":
        return load_json(json.loads(path.read_text(encoding="utf-8")))
    if suffix in (".usdz", ".usda", ".usdc", ".usd"):
        return load_usd(path)
    if suffix == ".glb":
        return load_glb(path.read_bytes())
    raise RoomPlanError(
        f"Not a RoomPlan export: {path.name} (expected .json, .usdz or .glb)"
    )


def load_json(data: dict) -> Scan:
    # A CapturedStructure (multi-room, iOS 17) carries the merged elements at
    # the top level as well, so the same keys work for both.
    if not any(k in data for k in (*SURFACE_KINDS, "objects")):
        raise RoomPlanError("JSON has none of walls/doors/windows/objects — not a CapturedRoom")

    elements: list[Element] = []
    for kind in (*SURFACE_KINDS, "objects"):
        for raw in data.get(kind) or []:
            corners = raw.get("polygonCorners")
            elements.append(
                Element(
                    kind=kind,
                    category=_category(raw.get("category"), kind),
                    transform=_matrix(raw["transform"]),
                    dimensions=np.asarray(_floats(raw["dimensions"])[:3], dtype=np.float64),
                    identifier=str(raw.get("identifier", "")),
                    parent=raw.get("parentIdentifier"),
                    corners=(
                        np.asarray([_floats(c)[:3] for c in corners], dtype=np.float64)
                        if corners
                        else None
                    ),
                )
            )
    if not elements:
        raise RoomPlanError("The RoomPlan export is empty")
    return Scan(elements, room_type=_room_type(data.get("sections") or []))


def _floats(value) -> list[float]:
    """A SIMD vector as Swift encodes it: a flat array, or keyed x/y/z/w."""
    if isinstance(value, dict):
        return [float(value[k]) for k in ("x", "y", "z", "w") if k in value]
    return [float(v) for v in value]


def _matrix(value) -> np.ndarray:
    """
    simd_float4x4 as encoded by Swift: 16 floats or 4 columns of 4, both
    column-major. Returned in the usual column-vector form, M @ [x y z 1].
    """
    if isinstance(value, list) and value and isinstance(value[0], (list, dict)):
        flat = [f for col in value for f in _floats(col)]
    else:
        flat = _floats(value)
    if len(flat) != 16:
        raise RoomPlanError(f"transform must have 16 values, got {len(flat)}")
    return np.asarray(flat, dtype=np.float64).reshape(4, 4).T


def _category(value, kind: str) -> str:
    """Swift enums encode as {"door": {"isOpen": true}} or a bare string."""
    if isinstance(value, dict) and value:
        return str(next(iter(value)))
    if isinstance(value, str):
        return value
    return kind.rstrip("s")


def _room_type(sections: list) -> str | None:
    for section in sections:
        label = _category(section.get("label"), "")
        if label in SECTION_TYPES:
            return SECTION_TYPES[label]
    return None


def load_usd(path: Path) -> Scan:
    """
    RoomPlan USDZ. Prims are named after their category and index (Wall0,
    Door2, Window1, Opening0, Floor0, Storage3 ...); each is measured from its
    untransformed bounds so the box keeps the element's own orientation.
    """
    try:
        from pxr import Usd, UsdGeom  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RoomPlanError(
            "Reading a USDZ needs usd-core (`uv pip install usd-core`). Exporting the "
            "scan as JSON instead avoids it."
        ) from exc

    stage = Usd.Stage.Open(str(path))
    if stage is None:
        raise RoomPlanError(f"Could not open {path.name}")
    metres = float(UsdGeom.GetStageMetersPerUnit(stage) or 1.0)
    z_up = UsdGeom.GetStageUpAxis(stage) == UsdGeom.Tokens.z
    cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_])
    xcache = UsdGeom.XformCache()

    surface = {"wall": "walls", "door": "doors", "window": "windows", "opening": "openings",
               "floor": "floors"}
    objects = {name.lower(): name for name in OBJECT_LABELS}
    elements: list[Element] = []
    for prim in stage.Traverse():
        match = re.fullmatch(r"([A-Za-z]+?)_?(\d+)", prim.GetName())
        if not match or not prim.IsA(UsdGeom.Xformable):
            continue
        name = match.group(1).lower()
        if name in surface:
            kind, category = surface[name], name
        elif name in objects:
            kind, category = "objects", objects[name]
        else:
            continue
        box = cache.ComputeUntransformedBound(prim).ComputeAlignedRange()
        if box.IsEmpty():
            continue
        lo, hi = np.array(box.GetMin()), np.array(box.GetMax())
        # USD is row-vector: p' = p @ M. Transpose to the column form.
        world = np.array(xcache.GetLocalToWorldTransform(prim)).T
        local_centre = np.eye(4)
        local_centre[:3, 3] = (lo + hi) / 2
        transform = world @ local_centre
        transform[:3, 3] *= metres
        dims = (hi - lo) * metres * _axis_scales(world)
        if z_up:
            transform = _Z_UP_TO_Y_UP @ transform
        elements.append(Element(kind, category, transform, dims, prim.GetName()))
    if not elements:
        raise RoomPlanError(f"No RoomPlan walls or objects found in {path.name}")
    # Without the JSON's parentIdentifier, openings are matched to the nearest
    # wall geometrically, which is what to_rooms does anyway.
    return Scan(elements)


_Z_UP_TO_Y_UP = np.array([[1, 0, 0, 0], [0, 0, 1, 0], [0, -1, 0, 0], [0, 0, 0, 1]], dtype=float)


def _axis_scales(world: np.ndarray) -> np.ndarray:
    return np.linalg.norm(world[:3, :3], axis=0)


#: glTF node-name prefixes (Polycam's room export) to element kinds.
_GLB_SURFACES = {"wall": "walls", "door": "doors", "window": "windows", "opening": "openings"}


def load_glb(data: bytes) -> Scan:
    """
    A RoomPlan room exported as glTF binary — Polycam's Floorplan mode.

    Nodes are named Wall_0, Door_1, Window_0, Floor_<Room>, and furniture
    after its category (bed_0, chair_swivel_..._0, storage_shelf_0). The
    geometry is baked into world space with no node transforms, so each
    element's box is measured from its vertices: Y up, and the horizontal
    axes from the smallest rectangle around its footprint. Joints (wall
    corner fillers) and ceilings are ignored.
    """
    import cv2

    if data[:4] != b"glTF":
        raise RoomPlanError("Not a binary glTF file")
    (json_len,) = struct.unpack_from("<I", data, 12)
    doc = json.loads(data[20 : 20 + json_len])
    binary = 20 + json_len + 8  # skip the BIN chunk header

    def vertices(mesh_index: int) -> np.ndarray:
        points = []
        for prim in doc["meshes"][mesh_index]["primitives"]:
            acc = doc["accessors"][prim["attributes"]["POSITION"]]
            view = doc["bufferViews"][acc["bufferView"]]
            start = binary + view.get("byteOffset", 0) + acc.get("byteOffset", 0)
            stride = view.get("byteStride", 12)
            raw = np.frombuffer(data, np.uint8, acc["count"] * stride, start)
            points.append(raw.reshape(-1, stride)[:, :12].copy().view(np.float32))
        return np.vstack(points).astype(np.float64)

    elements: list[Element] = []
    room_type = None
    for node in doc.get("nodes", []):
        if "mesh" not in node:
            continue
        name = node.get("name", "")
        head = name.split("_")[0].lower()
        if head in ("joint", "ceiling"):
            continue
        if head == "floor":
            label = name.split("_", 1)[-1]  # Floor_Bedroom, Floor_LivingRoom
            room_type = SECTION_TYPES.get(label[:1].lower() + label[1:], room_type)
            kind, category = "floors", "floor"
        elif head in _GLB_SURFACES:
            kind, category = _GLB_SURFACES[head], head
        else:
            kind, category = "objects", head
        pts = vertices(node["mesh"])
        (cx, cz), (w, d), angle = cv2.minAreaRect(pts[:, [0, 2]].astype(np.float32))
        if d > w:  # right axis along the long side
            w, d, angle = d, w, angle + 90
        a = np.radians(angle)
        right = np.array([np.cos(a), 0.0, np.sin(a)])
        up = np.array([0.0, 1.0, 0.0])
        lo, hi = pts[:, 1].min(), pts[:, 1].max()
        transform = np.eye(4)
        transform[:3, 0], transform[:3, 1], transform[:3, 2] = right, up, np.cross(right, up)
        transform[:3, 3] = [cx, (lo + hi) / 2, cz]
        if kind == "floors":
            transform[1, 3] = hi  # the floor's top surface is the floor
        elements.append(Element(kind, category, transform, np.array([w, hi - lo, d]), name))
    if not any(e.kind == "walls" for e in elements):
        raise RoomPlanError("No Wall_ nodes in this glTF — is it a Floorplan/RoomPlan export?")
    return Scan(elements, room_type=room_type)


# --------------------------------------------------------------------------
# Scan -> manifest rooms
# --------------------------------------------------------------------------


def floor_height(scan: Scan) -> float:
    """
    Height of the floor in the scan's own frame. A wall's centre is half its
    height above the floor; floor elements, when present, are the floor.
    """
    floors = scan.of("floors")
    if floors:
        return float(np.median([f.centre[1] for f in floors]))
    walls = scan.of("walls")
    if not walls:
        raise RoomPlanError("The scan has no walls, so the floor height is unknown")
    return float(np.median([w.centre[1] - w.dimensions[1] / 2 for w in walls]))


def wall_segments(scan: Scan) -> list[tuple[np.ndarray, np.ndarray]]:
    """Each wall as a floor-plan segment (x, z) start -> end."""
    segments = []
    for w in scan.of("walls"):
        half = w.right[[0, 2]] * w.dimensions[0] / 2
        c = w.centre[[0, 2]]
        segments.append((c - half, c + half))
    return segments


def floor_polygon(scan: Scan) -> np.ndarray:
    """
    The room outline (x, z), counter-clockwise in the manifest's sense
    (positive shoelace area).

    Prefers a floor element's polygon (iOS 17); otherwise chains the walls
    end to end and takes each corner where neighbouring walls' lines meet,
    because RoomPlan wall ends overlap or fall short by a few centimetres.
    """
    floors = [f for f in scan.of("floors") if f.corners is not None and len(f.corners) >= 3]
    if floors:
        f = max(floors, key=lambda e: float(e.dimensions[0] * e.dimensions[2]))
        homo = np.hstack([f.corners, np.ones((len(f.corners), 1))])
        poly = (homo @ f.transform.T)[:, [0, 2]]
    else:
        poly = _chain_walls(wall_segments(scan))
    if _signed_area(poly) < 0:
        poly = poly[::-1]
    return poly


def _chain_walls(segments: list[tuple[np.ndarray, np.ndarray]]) -> np.ndarray:
    if len(segments) < 3:
        raise RoomPlanError(f"Need at least 3 walls to outline a room, found {len(segments)}")
    remaining = list(range(1, len(segments)))
    order = [segments[0]]
    while remaining:
        end = order[-1][1]
        best, flip, best_d = remaining[0], False, math.inf
        for i in remaining:
            a, b = segments[i]
            for candidate_flip, point in ((False, a), (True, b)):
                d = float(np.linalg.norm(point - end))
                if d < best_d:
                    best, flip, best_d = i, candidate_flip, d
        a, b = segments[best]
        order.append((b, a) if flip else (a, b))
        remaining.remove(best)

    corners = []
    for i, (a, b) in enumerate(order):
        c, d = order[(i + 1) % len(order)]
        corners.append(_line_intersection(a, b, c, d, fallback=(b + c) / 2))
    return np.array(corners)


def _line_intersection(a, b, c, d, fallback) -> np.ndarray:
    r, s = b - a, d - c
    denom = r[0] * s[1] - r[1] * s[0]
    if abs(denom) < 1e-6:  # parallel: the walls continue each other
        return fallback
    t = ((c[0] - a[0]) * s[1] - (c[1] - a[1]) * s[0]) / denom
    point = a + t * r
    # A corner far from both walls means the chain jumped across the room.
    if np.linalg.norm(point - fallback) > 1.0:
        return fallback
    return point


def _signed_area(poly: np.ndarray) -> float:
    x, z = poly[:, 0], poly[:, 1]
    return 0.5 * float(np.sum(x * np.roll(z, -1) - np.roll(x, -1) * z))


def to_rooms(
    scan: Scan,
    room_id: str = "room",
    room_name: str = "Room",
    room_type: str | None = None,
) -> tuple[Room, list[ManifestItem], float]:
    """
    One manifest room (plus its furniture boxes) from a single-room scan, in
    the scan's frame with the floor dropped to y = 0.

    Returns the room, the items and the floor height that was removed.
    """
    floor_y = floor_height(scan)
    poly = floor_polygon(scan)
    walls_in = scan.of("walls")
    # Floor to the top of the wall, not the wall's own height: some exports
    # (Polycam) sink walls a few centimetres below the floor.
    height = (
        float(np.median([w.centre[1] + w.dimensions[1] / 2 for w in walls_in])) - floor_y
        if walls_in
        else 2.5
    )

    corners = [(float(x), float(z)) for x, z in poly]
    walls = [
        Wall(start=corners[i], end=corners[(i + 1) % len(corners)], height=round(height, 3))
        for i in range(len(corners))
    ]

    openings: list[Opening] = []
    for e in (*scan.of("doors"), *scan.of("windows"), *scan.of("openings")):
        index, offset = _nearest_wall(walls, e.centre[[0, 2]])
        openings.append(
            Opening(
                # An open doorway (RoomPlan "opening") is walkable, so it is a door.
                type="window" if e.kind == "windows" else "door",
                wallIndex=index,
                offset=round(offset, 3),
                width=round(float(e.dimensions[0]), 3),
                height=round(float(e.dimensions[1]), 3),
            )
        )

    centroid = poly.mean(axis=0)
    far = poly[int(np.argmax(np.linalg.norm(poly - centroid, axis=1)))] - centroid
    # yaw 0 looks down -Z (lib/camera.ts): face the far corner, the view that
    # shows most of the room.
    yaw = math.degrees(math.atan2(-far[0], -far[1])) % 360

    room = Room(
        id=room_id,
        name=room_name,
        type=room_type or scan.room_type or "living",  # type: ignore[arg-type]
        waypoint=Waypoint(
            position=(round(float(centroid[0]), 3), EYE_HEIGHT, round(float(centroid[1]), 3)),
            yaw=round(yaw, 1),
        ),
        floorPolygon=[(round(x, 4), round(z, 4)) for x, z in corners],
        walls=walls,
        openings=openings,
    )

    items = []
    counts: dict[str, int] = {}
    for e in scan.of("objects"):
        label = OBJECT_LABELS.get(e.category, e.category)
        counts[label] = counts.get(label, 0) + 1
        r = e.right
        items.append(
            ManifestItem(
                id=f"{label}-{counts[label]}",
                label=label,
                roomId=room_id,
                box=ItemBox(
                    center=(
                        round(float(e.centre[0]), 3),
                        round(float(e.centre[1] - floor_y), 3),
                        round(float(e.centre[2]), 3),
                    ),
                    size=tuple(round(float(v), 3) for v in e.dimensions),  # type: ignore[arg-type]
                    # Same convention as box_corners in layout.py: local +x
                    # lands on (cos yaw, sin yaw) in the (x, z) plane.
                    yaw=round(math.degrees(math.atan2(r[2], r[0])) % 360, 1),
                ),
                source="roomplan",
            )
        )

    return room, items, floor_y


def _nearest_wall(walls: list[Wall], point: np.ndarray) -> tuple[int, float]:
    """Index of the wall nearest `point`, and the distance along it from its start."""
    best, best_d, best_offset = 0, math.inf, 0.0
    for i, w in enumerate(walls):
        a, b = np.array(w.start), np.array(w.end)
        ab = b - a
        length = float(np.linalg.norm(ab))
        if length < 1e-9:
            continue
        t = float(np.clip(np.dot(point - a, ab) / length**2, 0.0, 1.0))
        d = float(np.linalg.norm(point - (a + t * ab)))
        if d < best_d:
            best, best_d, best_offset = i, d, t * length
    return best, best_offset

