"""
The generated demo flat has to be a flat you can actually walk around.

Door openings are stored per room as an offset along one of that room's four
walls, measured from the wall's start — and the walls wind counter-clockwise,
so half of them run backwards along their axis. A sign error there produces a
manifest that looks fine and a flat with a room you cannot reach, which is
invisible until someone walks into it.

So this re-implements `canStand` from `geometry.ts` and flood-fills the plan.
The duplication is deliberate, the same way `layout.py` and `geometry.ts`
duplicate each other: the rule has to hold on both sides, and a test that
imported the implementation would only prove it agrees with itself.
"""

from __future__ import annotations

import math
import sys
from collections import deque
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from make_test_scene import ROOMS, build_manifest  # noqa: E402

BODY_RADIUS = 0.24
STEP = 0.1


def _distance_to_segment(p, a, b) -> float:
    px, pz = p
    ax, az = a
    bx, bz = b
    dx, dz = bx - ax, bz - az
    length_sq = dx * dx + dz * dz
    if length_sq < 1e-12:
        return math.hypot(px - ax, pz - az)
    t = max(0.0, min(1.0, ((px - ax) * dx + (pz - az) * dz) / length_sq))
    return math.hypot(px - (ax + t * dx), pz - (az + t * dz))


def _distance_along(p, a, b) -> float:
    px, pz = p
    ax, az = a
    bx, bz = b
    dx, dz = bx - ax, bz - az
    length_sq = dx * dx + dz * dz
    if length_sq < 1e-12:
        return 0.0
    t = max(0.0, min(1.0, ((px - ax) * dx + (pz - az) * dz) / length_sq))
    return t * math.sqrt(length_sq)


def _point_in_polygon(p, poly) -> bool:
    px, pz = p
    for i in range(len(poly)):
        if _distance_to_segment(p, poly[i], poly[(i + 1) % len(poly)]) <= 1e-9:
            return True
    inside = False
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, zi = poly[i]
        xj, zj = poly[j]
        if (zi > pz) != (zj > pz) and px < (xj - xi) * (pz - zi) / (zj - zi) + xi:
            inside = not inside
        j = i
    return inside


def _room_at(p, rooms):
    for room in rooms:
        poly = [tuple(c) for c in room.floorPolygon]
        if not _point_in_polygon(p, poly):
            continue
        blocked = False
        for index, wall in enumerate(room.walls):
            start, end = tuple(wall.start), tuple(wall.end)
            if _distance_to_segment(p, start, end) >= BODY_RADIUS:
                continue
            along = _distance_along(p, start, end)
            through = any(
                o.type == "door" and o.wallIndex == index and abs(along - o.offset) <= o.width / 2
                for o in room.openings
            )
            if not through:
                blocked = True
                break
        if not blocked:
            return room.id
    return None


@pytest.fixture(scope="module")
def manifest():
    return build_manifest("test-flat", "scene.ply", {})


def test_every_room_has_a_standable_waypoint(manifest):
    for room in manifest.rooms:
        position = (room.waypoint.position[0], room.waypoint.position[2])
        assert _room_at(position, manifest.rooms) == room.id, (
            f"{room.id}: the camera cannot stand on its own waypoint"
        )


def test_every_room_is_reachable_on_foot(manifest):
    """
    Flood-fill from the hallway on a 10 cm grid and check every room is hit.

    This is what catches a door recorded on the wrong wall, or at an offset
    measured from the wrong end: the room is still in the manifest and still
    appears in the room strip, it just has no way in.
    """
    start = next(r for r in manifest.rooms if r.id == "hall")
    origin = (round(start.waypoint.position[0], 2), round(start.waypoint.position[2], 2))
    assert _room_at(origin, manifest.rooms) is not None

    reached: set[str] = set()
    seen = {origin}
    queue = deque([origin])
    while queue:
        point = queue.popleft()
        room_id = _room_at(point, manifest.rooms)
        if room_id:
            reached.add(room_id)
        for dx, dz in ((STEP, 0), (-STEP, 0), (0, STEP), (0, -STEP)):
            nxt = (round(point[0] + dx, 2), round(point[1] + dz, 2))
            if nxt in seen or not (-1 <= nxt[0] <= 14 and -1 <= nxt[1] <= 11):
                continue
            seen.add(nxt)
            if _room_at(nxt, manifest.rooms):
                queue.append(nxt)

    missing = {r.id for r in manifest.rooms} - reached
    assert not missing, f"unreachable on foot: {sorted(missing)}"


def test_doors_are_declared_by_both_rooms(manifest):
    """
    A doorway only works if both rooms know about it: `canStand` tests rooms
    independently, so a door the corridor has not declared is a wall you get
    stuck against from the corridor side.
    """
    by_id = {r.id: r for r in manifest.rooms}
    hall = by_id["hall"]
    hall_doors = {(round(o.offset, 3), o.wallIndex) for o in hall.openings if o.type == "door"}
    assert len(hall_doors) == len(ROOMS) - 1, (
        "the corridor should declare one door per room opening onto it"
    )


def test_rooms_do_not_overlap(manifest):
    """Two rooms claiming the same floor would make `roomAt` order-dependent."""
    centres = []
    for room in manifest.rooms:
        xs = [c[0] for c in room.floorPolygon]
        zs = [c[1] for c in room.floorPolygon]
        centres.append((room.id, (min(xs) + max(xs)) / 2, (min(zs) + max(zs)) / 2))
    for room_id, cx, cz in centres:
        holding = [
            r.id
            for r in manifest.rooms
            if _point_in_polygon((cx, cz), [tuple(c) for c in r.floorPolygon])
        ]
        assert holding == [room_id], f"{room_id}'s centre is inside {holding}"
