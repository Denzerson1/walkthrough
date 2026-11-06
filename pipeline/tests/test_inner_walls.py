"""
A flat of rooms rather than an open plan: inner walls with their doors, a
balcony scanned with it and left out, and the walls and doors the scan
missed placed from the dressing.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from walkthrough_pipeline import rebuild, room_split, roomplan
from walkthrough_pipeline.flat_geometry import join_partitions
from walkthrough_pipeline.roomplan import Element, Scan

H = 2.5


def _surface(kind: str, a, b, height=H, bottom=0.0, name="") -> Element:
    """A wall, door or window from plan point a to b, as a Polycam export gives it."""
    (ax, az), (bx, bz) = a, b
    right = np.array([bx - ax, 0.0, bz - az])
    length = float(np.linalg.norm(right))
    right /= length
    up = np.array([0.0, 1.0, 0.0])
    t = np.eye(4)
    t[:3, 0], t[:3, 1], t[:3, 2] = right, up, np.cross(right, up)
    t[:3, 3] = [(ax + bx) / 2, bottom + height / 2, (az + bz) / 2]
    return Element(kind, kind[:-1], t, np.array([length, height, 0.0]), name)


def _floor(label: str, corners) -> Element:
    return Element("floors", "floor", np.eye(4), np.zeros(3), f"Floor_{label}",
                   corners=np.array([[x, 0.0, z] for x, z in corners]))


def _chair(x: float, z: float) -> Element:
    t = np.eye(4)
    t[:3, 3] = [x, 0.45, z]
    return Element("objects", "chair", t, np.array([0.5, 0.9, 0.5]))


# Two rooms side by side (x 0..2 and 2..4, z 0..3) with a door between
# them, and a balcony below (z -2..0) behind a glass door in the shared wall.
SCAN = Scan([
    _floor("Bedroom", [(0, 0), (2, 0), (2, 3), (0, 3)]),
    _floor("Office", [(2, 0), (4, 0), (4, 3), (2, 3)]),
    _floor("Balcony", [(0, -2), (4, -2), (4, 0), (0, 0)]),
    _surface("walls", (0, 3), (4, 3), name="Wall_0"),
    _surface("walls", (0, 0), (0, 3), name="Wall_1"),
    _surface("walls", (4, 0), (4, 3), name="Wall_2"),
    _surface("walls", (0, 0), (4, 0), name="Wall_3"),     # room / balcony
    _surface("walls", (2, 0), (2, 3), name="Wall_4"),     # between the rooms
    _surface("walls", (0, -2), (4, -2), name="Wall_5"),   # the balcony's
    _surface("walls", (0, -2), (0, 0), name="Wall_6"),
    _surface("walls", (4, -2), (4, 0), name="Wall_7"),
    _surface("doors", (2, 1.1), (2, 1.9), height=2.0, name="Door_0"),
    _surface("doors", (0.6, 0), (1.4, 0), height=2.2, name="Door_1"),
    _chair(1.0, 1.5),
    _chair(2.0, -1.0),  # on the balcony
])


def test_a_dropped_balcony_takes_its_walls_and_leaves_the_shared_one():
    flat = roomplan.without(SCAN, ["Balcony"])
    walls = {e.identifier for e in flat.of("walls")}
    assert walls == {"Wall_0", "Wall_1", "Wall_2", "Wall_3", "Wall_4"}
    assert [e.identifier for e in flat.of("floors")] == ["Floor_Bedroom", "Floor_Office"]
    # Furniture stays, so the pieces keep their numbers.
    assert len(flat.of("objects")) == 2
    room, _, _ = roomplan.to_rooms(flat)
    xs, zs = zip(*room.floorPolygon, strict=True)
    assert (min(xs), max(xs), min(zs), max(zs)) == pytest.approx((0, 4, 0, 3), abs=1e-6)
    with pytest.raises(roomplan.RoomPlanError, match="Kitchen"):
        roomplan.without(SCAN, ["Kitchen"])


def test_a_door_inside_the_flat_hangs_in_its_partition_not_on_the_outline():
    flat = roomplan.without(SCAN, ["Balcony"])
    room, _, floor_y = roomplan.to_rooms(flat)
    # The balcony door is on the outline now; the inner door is not on it.
    assert [o.width for o in room.openings] == pytest.approx([0.8])
    wall = room.walls[room.openings[0].wallIndex]
    assert wall.start[1] == pytest.approx(0) and wall.end[1] == pytest.approx(0)
    parts = roomplan.partitions(flat, np.array(room.floorPolygon), floor_y)
    assert len(parts) == 1
    (door,) = parts[0]["openings"]
    assert door["width"] == pytest.approx(0.8)
    assert door["height"] == pytest.approx(2.0)
    assert door["offset"] == pytest.approx(1.5)  # along x = 2 from z = 0


def test_the_dressing_adds_walls_and_doors_and_hangs_the_scanned_ones():
    flat = roomplan.without(SCAN, ["Balcony"])
    room, _, floor_y = roomplan.to_rooms(flat)
    scan = {"partitions": roomplan.partitions(flat, np.array(room.floorPolygon), floor_y)}
    rebuild.add_walls(room, scan, [{"start": [2, 2.2], "end": [4, 2.2]}])
    assert scan["partitions"][-1]["height"] == pytest.approx(H)
    rebuild.add_openings(room, scan, [
        # The scanned inner door: only how it is hung is taken.
        {"type": "door", "at": [2, 1.45], "width": 0.9, "height": 2.1,
         "hinge": "end", "swing": -1},
        {"type": "door", "at": [3, 2.2], "width": 0.8, "height": 2.0},
        {"type": "window", "at": [4, 1.5], "width": 1.2, "height": 1.4, "sill": 0.8},
    ])
    first, added = scan["partitions"][0]["openings"], scan["partitions"][1]["openings"]
    assert len(first) == 1
    assert first[0]["width"] == pytest.approx(0.8)
    assert (first[0]["hinge"], first[0]["swing"]) == ("end", -1)
    assert added[0]["offset"] == pytest.approx(1.0)
    window = room.openings[-1]
    assert window.type == "window" and window.sill == pytest.approx(0.8)
    # Given again to the floor, it replaces itself: a balcony door the scan
    # took for a window on a sill.
    before = len(room.openings)
    rebuild.add_openings(room, scan, [{"type": "window", "at": [4, 1.6], "width": 1.0,
                                       "height": 2.2, "sill": 0.0}])
    assert len(room.openings) == before
    assert room.openings[-1].sill == pytest.approx(0.0)
    with pytest.raises(ValueError, match="not on the outline"):
        rebuild.add_openings(room, scan, [{"type": "window", "at": [2, 1.5], "width": 1,
                                           "height": 1}])
    with pytest.raises(roomplan.RoomPlanError, match="No partition"):
        rebuild.add_openings(room, scan, [{"type": "door", "at": [1, 1.5], "width": 0.8,
                                           "height": 2}])


def test_inner_walls_stop_the_walk_but_for_their_doors_in_each_room():
    flat = roomplan.without(SCAN, ["Balcony"])
    room, _, floor_y = roomplan.to_rooms(flat)
    parts = roomplan.partitions(flat, np.array(room.floorPolygon), floor_y)
    whole = rebuild.with_partitions(room, parts)
    inner = len(room.walls)
    assert whole.walls[inner].start[0] == pytest.approx(2)
    assert whole.walls[inner].end[0] == pytest.approx(2)
    (door,) = [o for o in whole.openings if o.wallIndex == inner]
    assert door.sill == 0.0 and door.width == pytest.approx(0.8)
    zones = [
        {"id": "bedroom", "name": "Bedroom", "type": "bedroom",
         "polygon": [(0, 0), (2, 0), (2, 3), (0, 3)], "waypoint": {"eye": [1, 1], "look": [1, 2]}},
        {"id": "office", "name": "Office", "type": "office",
         "polygon": [(2, 0), (4, 0), (4, 3), (2, 3)], "waypoint": {"eye": [3, 1], "look": [3, 2]}},
    ]
    for zone in zones:
        part = room_split.part(whole, zone)
        on_line = [i for i, w in enumerate(part.walls)
                   if math.isclose(w.start[0], 2) and math.isclose(w.end[0], 2)]
        assert len(on_line) == 1  # each room has the wall between them, with its door
        assert [o.type for o in part.openings if o.wallIndex == on_line[0]] == ["door"]


def test_joining_partitions_keeps_their_doors_where_they_stood():
    # A wall whose start pokes 3 cm into a crossing one is moved onto its
    # centre line; its door must not move with it.
    parts = [{"start": [0.03, 0.0], "end": [3.0, 0.0], "height": H,
              "openings": [{"type": "door", "offset": 1.0, "width": 0.8, "height": 2.0,
                            "bottom": 0.0}]},
             {"start": [0.0, -1.0], "end": [0.0, 1.0], "height": H, "openings": []}]
    joined = join_partitions(parts, 0.1)
    assert joined[0]["start"] == pytest.approx([0.0, 0.0])
    assert joined[0]["openings"][0]["offset"] == pytest.approx(1.03)
    assert parts[0]["openings"][0]["offset"] == 1.0  # the input is left alone
