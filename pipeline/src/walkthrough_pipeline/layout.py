"""
Rule-based automatic furniture layout (M7).

The assistant picks the items and the style; this solver decides where they
go. It is deterministic: the same room and the same item list always produce
the same layout, which makes it testable and makes the demo repeatable.

Rules from the brief:
  * bed headboard against a wall without the door
  * sofa against the longest free wall, facing the window or TV wall
  * tables keep at least 0.8 m clearance
  * walkways of 0.8-0.9 m and door swing areas stay free
  * if no valid layout exists, fall back to fewer items
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

Vec2 = tuple[float, float]

WALKWAY_M = 0.85
TABLE_CLEARANCE_M = 0.8
DOOR_SWING_M = 0.9


@dataclass(frozen=True)
class Item:
    """A piece of furniture to place."""

    id: str
    category: str
    #: footprint on the floor, (width, depth) in metres
    size: Vec2
    #: True when the item is designed to stand against a wall
    against_wall: bool = True


@dataclass
class Placement:
    item_id: str
    position: Vec2
    yaw: float

    def as_dict(self) -> dict:
        return {
            "itemId": self.item_id,
            "position": [round(self.position[0], 3), 0.0, round(self.position[1], 3)],
            "yaw": round(self.yaw, 4),
        }


@dataclass
class Wall:
    start: Vec2
    end: Vec2
    #: openings on this wall as (offset, width) pairs
    openings: list[tuple[float, float]] = field(default_factory=list)
    has_door: bool = False
    has_window: bool = False

    @property
    def length(self) -> float:
        return math.dist(self.start, self.end)

    @property
    def yaw(self) -> float:
        """Direction along the wall, degrees."""
        return math.degrees(math.atan2(self.end[1] - self.start[1], self.end[0] - self.start[0]))

    @property
    def midpoint(self) -> Vec2:
        return ((self.start[0] + self.end[0]) / 2, (self.start[1] + self.end[1]) / 2)

    def point_at(self, t: float) -> Vec2:
        """Point `t` metres along the wall from its start."""
        total = self.length
        if total < 1e-9:
            return self.start
        f = max(0.0, min(1.0, t / total))
        return (
            self.start[0] + (self.end[0] - self.start[0]) * f,
            self.start[1] + (self.end[1] - self.start[1]) * f,
        )

    def free_spans(self, margin: float = 0.0) -> list[tuple[float, float]]:
        """Stretches of wall not blocked by a door or window."""
        blocked = sorted(
            (max(0.0, o - w / 2 - margin), min(self.length, o + w / 2 + margin))
            for o, w in self.openings
        )
        spans: list[tuple[float, float]] = []
        cursor = 0.0
        for start, end in blocked:
            if start > cursor:
                spans.append((cursor, start))
            cursor = max(cursor, end)
        if cursor < self.length:
            spans.append((cursor, self.length))
        return [s for s in spans if s[1] - s[0] > 0.01]


# ---------------------------------------------------------------------------
# geometry helpers (mirrors apps/web/src/lib/geometry.ts)
# ---------------------------------------------------------------------------


def box_corners(center: Vec2, size: Vec2, yaw: float) -> list[Vec2]:
    hw, hd = size[0] / 2, size[1] / 2
    r = math.radians(yaw)
    cos, sin = math.cos(r), math.sin(r)
    local = [(-hw, -hd), (hw, -hd), (hw, hd), (-hw, hd)]
    return [(center[0] + x * cos - y * sin, center[1] + x * sin + y * cos) for x, y in local]


def _project(corners: list[Vec2], nx: float, ny: float) -> tuple[float, float]:
    ds = [x * nx + y * ny for x, y in corners]
    return min(ds), max(ds)


def boxes_overlap(
    a_center: Vec2, a_size: Vec2, a_yaw: float,
    b_center: Vec2, b_size: Vec2, b_yaw: float,
    clearance: float = 0.0,
) -> bool:
    ca = box_corners(a_center, (a_size[0] + clearance, a_size[1] + clearance), a_yaw)
    cb = box_corners(b_center, (b_size[0] + clearance, b_size[1] + clearance), b_yaw)
    for corners in (ca, cb):
        for i in range(4):
            x1, y1 = corners[i]
            x2, y2 = corners[(i + 1) % 4]
            axis = (-(y2 - y1), x2 - x1)
            length = math.hypot(*axis)
            if length < 1e-12:
                continue
            nx, ny = axis[0] / length, axis[1] / length
            amin, amax = _project(ca, nx, ny)
            bmin, bmax = _project(cb, nx, ny)
            if amax < bmin - 1e-9 or bmax < amin - 1e-9:
                return False
    return True


def distance_point_to_segment(p: Vec2, a: Vec2, b: Vec2) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    len_sq = dx * dx + dy * dy
    if len_sq < 1e-12:
        return math.dist(p, a)
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / len_sq))
    return math.dist(p, (a[0] + t * dx, a[1] + t * dy))


#: Boundary tolerance, shared with POLYGON_EDGE_TOLERANCE in geometry.ts.
EDGE_TOLERANCE = 1e-9


def point_in_polygon(p: Vec2, poly: list[Vec2], tolerance: float = EDGE_TOLERANCE) -> bool:
    """
    Ray casting, with points on an edge counted as inside.

    The boundary case is not academic: furniture placed flush against a wall
    has corners exactly on the polygon edge, and a strict test would reject
    every wall-hugging item. Mirrors apps/web/src/lib/geometry.ts.
    """
    for i in range(len(poly)):
        a = poly[i]
        b = poly[(i + 1) % len(poly)]
        if distance_point_to_segment(p, a, b) <= tolerance:
            return True

    x, y = p
    inside = False
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def box_inside_polygon(center: Vec2, size: Vec2, yaw: float, poly: list[Vec2]) -> bool:
    return all(point_in_polygon(c, poly) for c in box_corners(center, size, yaw))


def inward_normal(wall: Wall, poly: list[Vec2]) -> Vec2:
    """Unit normal of the wall pointing into the room."""
    dx, dy = wall.end[0] - wall.start[0], wall.end[1] - wall.start[1]
    length = math.hypot(dx, dy) or 1.0
    n = (-dy / length, dx / length)
    mid = wall.midpoint
    probe = (mid[0] + n[0] * 0.05, mid[1] + n[1] * 0.05)
    return n if point_in_polygon(probe, poly) else (-n[0], -n[1])


# ---------------------------------------------------------------------------
# solver
# ---------------------------------------------------------------------------


@dataclass
class Room:
    id: str
    type: str
    polygon: list[Vec2]
    walls: list[Wall]

    def door_walls(self) -> list[int]:
        return [i for i, w in enumerate(self.walls) if w.has_door]

    def window_walls(self) -> list[int]:
        return [i for i, w in enumerate(self.walls) if w.has_window]


@dataclass
class LayoutResult:
    placements: list[Placement]
    skipped: list[str]
    #: True when every requested item was placed
    complete: bool

    def as_dicts(self) -> list[dict]:
        return [p.as_dict() for p in self.placements]


#: Which wall each category prefers, and how much clearance it needs.
_RULES: dict[str, dict] = {
    "bed": {"wall": "no_door", "clearance": WALKWAY_M, "priority": 0},
    "sofa": {"wall": "longest_free", "clearance": WALKWAY_M, "priority": 1},
    "wardrobe": {"wall": "no_door", "clearance": 0.6, "priority": 2},
    "shelf": {"wall": "any_free", "clearance": 0.5, "priority": 3},
    "sideboard": {"wall": "any_free", "clearance": 0.5, "priority": 3},
    "desk": {"wall": "window", "clearance": 0.7, "priority": 4},
    "table": {"wall": "center", "clearance": TABLE_CLEARANCE_M, "priority": 5},
    "dining_table": {"wall": "center", "clearance": TABLE_CLEARANCE_M, "priority": 5},
    "coffee_table": {"wall": "front_of_sofa", "clearance": 0.4, "priority": 6},
    "rug": {"wall": "center", "clearance": 0.0, "priority": 7},
    "chair": {"wall": "any_free", "clearance": 0.4, "priority": 8},
    "lamp": {"wall": "corner", "clearance": 0.3, "priority": 9},
    "plant": {"wall": "corner", "clearance": 0.3, "priority": 10},
}

_DEFAULT_RULE = {"wall": "any_free", "clearance": 0.5, "priority": 20}


def rule_for(category: str) -> dict:
    return _RULES.get(category, _DEFAULT_RULE)


def _candidate_walls(room: Room, strategy: str) -> list[int]:
    doors = set(room.door_walls())
    windows = set(room.window_walls())
    order = list(range(len(room.walls)))
    if strategy == "no_door":
        prefer = [i for i in order if i not in doors]
        return prefer or order
    if strategy == "longest_free":
        prefer = [i for i in order if i not in doors]
        prefer = prefer or order
        return sorted(prefer, key=lambda i: -room.walls[i].length)
    if strategy == "window":
        return sorted(order, key=lambda i: (i not in windows, -room.walls[i].length))
    if strategy == "any_free":
        return sorted(order, key=lambda i: (i in doors, -room.walls[i].length))
    return order


def facing_yaw(wall: Wall, inward: Vec2) -> float:
    """
    Yaw that puts the item's back against the wall and its front into the room.

    In the 2D convention a yaw of `w` points the item's front along
    (-sin w, cos w). That equals the wall's left normal only when the room
    interior happens to lie on that side; for the other half of the cases —
    any clockwise-wound polygon, or a wall emitted in the reverse direction —
    it must be turned around, or beds and sofas face into the wall.
    """
    dx, dy = wall.end[0] - wall.start[0], wall.end[1] - wall.start[1]
    length = math.hypot(dx, dy) or 1.0
    left_normal = (-dy / length, dx / length)
    aligned = inward[0] * left_normal[0] + inward[1] * left_normal[1] > 0
    return wall.yaw if aligned else wall.yaw + 180.0


def _wall_positions(wall: Wall, item: Item, inward: Vec2) -> list[tuple[Vec2, float]]:
    """Candidate centre positions along a wall, centred in each free span."""
    out: list[tuple[Vec2, float]] = []
    half_depth = item.size[1] / 2
    yaw = facing_yaw(wall, inward)
    for start, end in wall.free_spans(margin=DOOR_SWING_M / 2):
        span = end - start
        if span < item.size[0]:
            continue
        # Try the middle of the span first, then nudge toward each end.
        for frac in (0.5, 0.3, 0.7):
            t = start + span * frac
            t = max(start + item.size[0] / 2, min(end - item.size[0] / 2, t))
            on_wall = wall.point_at(t)
            centre = (
                on_wall[0] + inward[0] * half_depth,
                on_wall[1] + inward[1] * half_depth,
            )
            out.append((centre, yaw))
    return out


def polygon_centroid(poly: list[Vec2]) -> Vec2:
    area = 0.0
    cx = cy = 0.0
    for i in range(len(poly)):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % len(poly)]
        cross = x1 * y2 - x2 * y1
        area += cross
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    area /= 2
    if abs(area) < 1e-9:
        n = len(poly)
        return (sum(p[0] for p in poly) / n, sum(p[1] for p in poly) / n)
    return (cx / (6 * area), cy / (6 * area))


def solve_layout(room: Room, items: list[Item]) -> LayoutResult:
    """
    Place `items` in `room`. Deterministic and collision-checked.

    Items are placed in rule priority order so the anchors (bed, sofa) claim
    the good walls before the accessories.
    """
    ordered = sorted(items, key=lambda it: (rule_for(it.category)["priority"], it.id))
    placed: list[tuple[Placement, Item]] = []
    skipped: list[str] = []
    centroid = polygon_centroid(room.polygon)

    for item in ordered:
        rule = rule_for(item.category)
        strategy = rule["wall"]
        clearance = rule["clearance"]
        candidates: list[tuple[Vec2, float]] = []

        if strategy == "center":
            candidates.append((centroid, 0.0))
            candidates.append((centroid, 90.0))
        elif strategy == "front_of_sofa":
            sofa = next((p for p, it in placed if it.category == "sofa"), None)
            if sofa is not None:
                r = math.radians(sofa.yaw)
                # 0.45 m in front of the sofa, along its facing direction.
                nx, ny = -math.sin(r), math.cos(r)
                offset = item.size[1] / 2 + 0.45
                candidates.append(
                    ((sofa.position[0] + nx * offset, sofa.position[1] + ny * offset), sofa.yaw)
                )
            candidates.append((centroid, 0.0))
        elif strategy == "corner":
            for vertex in room.polygon:
                inset = 0.35
                towards = (
                    vertex[0] + (centroid[0] - vertex[0]) * inset,
                    vertex[1] + (centroid[1] - vertex[1]) * inset,
                )
                candidates.append((towards, 0.0))
        else:
            for wall_index in _candidate_walls(room, strategy):
                wall = room.walls[wall_index]
                if wall.length < item.size[0]:
                    continue
                inward = inward_normal(wall, room.polygon)
                candidates.extend(_wall_positions(wall, item, inward))

        chosen: Placement | None = None
        for centre, yaw in candidates:
            if not box_inside_polygon(centre, item.size, yaw, room.polygon):
                continue
            conflict = any(
                boxes_overlap(
                    centre, item.size, yaw,
                    other.position, other_item.size, other.yaw,
                    clearance=min(clearance, rule_for(other_item.category)["clearance"]),
                )
                for other, other_item in placed
            )
            if conflict:
                continue
            chosen = Placement(item.id, centre, yaw)
            break

        if chosen is None:
            # Fall back to fewer items rather than producing an invalid layout.
            skipped.append(item.id)
        else:
            placed.append((chosen, item))

    return LayoutResult(
        placements=[p for p, _ in placed],
        skipped=skipped,
        complete=not skipped,
    )
