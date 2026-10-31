import pytest
from walkthrough_pipeline import room_split
from walkthrough_pipeline.manifest import (
    ItemBox,
    Manifest,
    ManifestItem,
    Opening,
    Room,
    Wall,
    Waypoint,
)

# An L: a 4 x 4 living space with a 2 x 2 hall below its left half, one
# outline, as a floor-plan scan of an open plan comes out. A door in the
# hall's bottom wall, another 1 m down the left wall, in the living room.
SCANNED = Room(
    id="room",
    name="Room",
    waypoint=Waypoint(position=(1, 1.6, 1)),
    floorPolygon=[(0, -2), (2, -2), (2, 0), (4, 0), (4, 4), (0, 4)],
    walls=[
        Wall(start=(0, -2), end=(2, -2)),
        Wall(start=(2, -2), end=(2, 0)),
        Wall(start=(2, 0), end=(4, 0)),
        Wall(start=(4, 0), end=(4, 4)),
        Wall(start=(4, 4), end=(0, 4)),
        Wall(start=(0, 4), end=(0, -2)),
    ],
    openings=[Opening(type="door", wallIndex=0, offset=1.0, width=0.9, height=2.1),
              Opening(type="door", wallIndex=5, offset=1.0, width=0.8, height=2.1)],
)
ZONES = [
    {"id": "living", "name": "Living room", "type": "living",
     "polygon": [(0, 0), (4, 0), (4, 4), (0, 4)],
     "waypoint": {"eye": [2, 2], "look": [2, 4]},
     "slots": [{"id": "sofa-1", "category": "sofa", "at": [2, 3.5], "facing": [0, -1],
                "maxFootprint": [2.2, 1.0], "replaces": ["sofa-1"]}]},
    {"id": "hall", "name": "Hall", "type": "hallway",
     "polygon": [(0, 0), (0, -2), (2, -2), (2, 0)],
     "waypoint": {"eye": [1, -1.5], "look": [1, 2]}},
]


def test_each_part_keeps_only_the_walls_along_its_own_edges():
    living, hall = (room_split.part(SCANNED, z) for z in ZONES)
    spans = {(w.start, w.end) for w in hall.walls}
    assert ((0, -2), (2, -2)) in spans and ((2, -2), (2, 0)) in spans
    # The scanned left wall runs down both parts: each keeps its own stretch.
    assert ((0, 0), (0, -2)) in spans
    assert ((0, 4), (0, 0)) in {(w.start, w.end) for w in living.walls}
    # Between living room and hall there is no wall: walking goes on as before.
    shared = [w for w in living.walls + hall.walls if w.start[1] == 0 == w.end[1]]
    assert all(min(w.start[0], w.end[0]) >= 2 for w in shared)


def test_doors_follow_their_wall_into_the_part_they_are_in():
    living, hall = (room_split.part(SCANNED, z) for z in ZONES)
    bottom = next(i for i, w in enumerate(hall.walls) if (w.start, w.end) == ((0, -2), (2, -2)))
    assert [(o.wallIndex, o.offset) for o in hall.openings] == [(bottom, 1.0)]
    # The door 1 m down the left wall: in the living room's stretch of it.
    left = next(i for i, w in enumerate(living.walls) if w.start == (0, 4))
    assert [(o.wallIndex, o.offset) for o in living.openings] == [(left, pytest.approx(1.0))]


def test_parts_have_their_names_waypoints_area_and_slots():
    living = room_split.part(SCANNED, ZONES[0])
    assert (living.name, living.type, living.areaM2) == ("Living room", "living", 16.0)
    assert living.waypoint.position == (2, 1.6, 2)
    assert living.waypoint.yaw == pytest.approx(180.0)  # looking +z
    sofa = living.slots[0]
    assert sofa.yaw == pytest.approx(180.0)  # its front faces -z
    assert (sofa.position, sofa.replaces) == ((2, 0.0, 3.5), ["sofa-1"])


def test_items_move_into_the_part_they_stand_in():
    manifest = Manifest(id="m", name="m", items=[
        ManifestItem(id="sofa-1", label="sofa", roomId="room",
                     box=ItemBox(center=(2, 0.4, 3.5), size=(2, 0.8, 1))),
        ManifestItem(id="coat", label="storage", roomId="room",
                     box=ItemBox(center=(1, 1, -1), size=(0.5, 2, 0.5))),
    ])
    room_split.split(manifest, SCANNED, ZONES)
    assert [r.id for r in manifest.rooms] == ["living", "hall"]
    assert {i.id: i.roomId for i in manifest.items} == {"sofa-1": "living", "coat": "hall"}


def test_without_zones_the_scan_stays_one_room():
    manifest = Manifest(id="m", name="m")
    room_split.split(manifest, SCANNED, [])
    assert manifest.rooms == [SCANNED]
