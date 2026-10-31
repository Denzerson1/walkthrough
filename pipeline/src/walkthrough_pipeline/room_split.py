"""
An open-plan scan as the rooms people name in it.

A floor-plan scan of a studio comes out as one room. The dressing's `rooms`
cut it into the parts a buyer would name (hall, living room, bedroom), each
with its own outline, name, type, waypoint and furniture slots, for the room
labels, the room strip and the style kits. Between the parts there is no wall,
so walking is as before: each part keeps only the scanned walls along its own
edges (with their doors and windows), and canStand lets a person stand in
whichever part a point lies in.

Dressing:

    "rooms": [
      {"id": "living", "name": "Living room", "type": "living",
       "polygon": [[x, z], ...],             # inside the scanned outline
       "waypoint": {"eye": [x, z], "look": [x, z]},
       "slots": [{"id": "sofa-1", "category": "sofa", "at": [x, z],
                  "facing": [dx, dz], "maxFootprint": [w, d],
                  "on": "nightstand-1",       # optional: stands on that slot's piece
                  "replaces": ["sofa-1"]}]},  # optional: the flat's own pieces it stands in for
      ...]
"""

from __future__ import annotations

import math

from .layout import facing_yaw, point_in_polygon, polygon_centroid
from .manifest import Manifest, Opening, Room, Slot, Wall, Waypoint
from .roomplan import EYE_HEIGHT

#: How far a scanned wall may lie off a part's edge and still be that edge's wall.
ON_EDGE_M = 0.03


Point = tuple[float, float]


def _along(a: Point, b: Point, p: Point) -> Point:
    """`p` relative to the line a -> b: (distance along from a, distance off it)."""
    dx, dz = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dz)
    ux, uz = dx / length, dz / length
    px, pz = p[0] - a[0], p[1] - a[1]
    return px * ux + pz * uz, abs(px * uz - pz * ux)


def _clip(wall: Wall, polygon: list[Point]) -> list[Point]:
    """The spans of `wall` (distances from its start) that lie along an edge of `polygon`."""
    length = math.hypot(wall.end[0] - wall.start[0], wall.end[1] - wall.start[1])
    spans = []
    for i, a in enumerate(polygon):
        b = polygon[(i + 1) % len(polygon)]
        (ta, oa), (tb, ob) = _along(wall.start, wall.end, a), _along(wall.start, wall.end, b)
        if oa > ON_EDGE_M or ob > ON_EDGE_M:
            continue
        lo, hi = max(min(ta, tb), 0.0), min(max(ta, tb), length)
        if hi - lo > ON_EDGE_M:
            spans.append((lo, hi))
    return spans


def _point(wall: Wall, t: float) -> Point:
    length = math.hypot(wall.end[0] - wall.start[0], wall.end[1] - wall.start[1])
    f = t / length
    return (round(wall.start[0] + f * (wall.end[0] - wall.start[0]), 4),
            round(wall.start[1] + f * (wall.end[1] - wall.start[1]), 4))


def part(room: Room, zone: dict) -> Room:
    """The part of `room` that `zone` (a dressing `rooms` entry) outlines."""
    polygon = [tuple(p) for p in zone["polygon"]]
    walls: list[Wall] = []
    openings: list[Opening] = []
    for index, wall in enumerate(room.walls):
        for lo, hi in _clip(wall, polygon):
            walls.append(Wall(start=_point(wall, lo), end=_point(wall, hi), height=wall.height))
            for o in room.openings:
                if o.wallIndex == index and lo <= o.offset <= hi:
                    openings.append(o.model_copy(update={"wallIndex": len(walls) - 1,
                                                         "offset": round(o.offset - lo, 4)}))
    (ex, ez), (lx, lz) = zone["waypoint"]["eye"], zone["waypoint"]["look"]
    return Room(
        id=zone["id"], name=zone["name"], type=zone["type"],
        waypoint=Waypoint(position=(ex, EYE_HEIGHT, ez),
                          yaw=round(math.degrees(math.atan2(-(lx - ex), -(lz - ez))) % 360, 1)),
        floorPolygon=polygon, walls=walls, openings=openings,
        originalFloorColor=room.originalFloorColor, ceilings=room.ceilings,
        obstacles=[o for o in room.obstacles
                   if point_in_polygon(polygon_centroid(o.footprint), polygon)],
        slots=[Slot(id=s["id"], category=s["category"], position=(s["at"][0], 0.0, s["at"][1]),
                    yaw=round(facing_yaw(tuple(s["facing"])) % 360, 1),
                    maxFootprint=tuple(s["maxFootprint"]), on=s.get("on"),
                    replaces=s.get("replaces", []))
               for s in zone.get("slots", [])],
    )


def split(manifest: Manifest, room: Room, zones: list[dict]) -> None:
    """
    The manifest's rooms: `room` (the scan's one) as the parts the dressing's
    `rooms` name, or as it is when it names none; each item in the part its
    centre stands in.
    """
    manifest.rooms = [part(room, z) for z in zones] if zones else [room]
    for item in manifest.items:
        centre = (item.box.center[0], item.box.center[2])
        home = next((r for r in manifest.rooms if point_in_polygon(centre, r.floorPolygon)),
                    manifest.rooms[0])
        item.roomId = home.id

