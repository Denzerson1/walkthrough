"""The 1:1 rebuild: wall geometry, light-atlas layout and the Blender spec."""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest
from walkthrough_pipeline import flat_geometry as fg
from walkthrough_pipeline import roomplan
from walkthrough_pipeline.manifest import (
    DEFAULT_SILL_M,
    ItemBox,
    Manifest,
    ManifestItem,
    Opening,
)
from walkthrough_pipeline.paths import ProjectPaths
from walkthrough_pipeline.rebuild import facing, scene_spec, stand_in

sys.path.insert(0, str(Path(__file__).parent))
from test_roomplan import _captured_room  # noqa: E402

SQUARE = [[0.0, 0.0], [4.0, 0.0], [4.0, 3.0], [0.0, 3.0]]


def test_grow_pushes_every_corner_out_by_the_same_distance():
    grown = fg.grow(SQUARE, 0.1)
    assert grown[0] == pytest.approx([-0.1, -0.1])
    assert grown[2] == pytest.approx([4.1, 3.1])


def test_wall_frame_normal_points_out_of_a_counter_clockwise_room():
    _, outward, length = fg.wall_frame(SQUARE[0], SQUARE[1])
    assert length == 4.0 and outward == pytest.approx((0.0, -1.0))  # the room is at +z


def test_wall_cells_leave_openings_empty_and_cover_the_rest():
    door = (0.5, 1.4, 0.0, 2.0)
    window = (2.0, 3.2, 0.9, 2.1)
    cells = fg.wall_cells(4.0, 2.5, [door, window])
    area = sum((u1 - u0) * (y1 - y0) for u0, u1, y0, y1 in cells)
    assert area == pytest.approx(4.0 * 2.5 - 0.9 * 2.0 - 1.2 * 1.2)
    for u0, u1, y0, y1 in cells:
        um, ym = (u0 + u1) / 2, (y0 + y1) / 2
        assert not (0.5 < um < 1.4 and ym < 2.0)
        assert not (2.0 < um < 3.2 and 0.9 < ym < 2.1)


def test_no_wall_cell_runs_past_an_opening_edge():
    """The bright-line fix: no cell spans across an opening's top or bottom height."""
    cells = fg.wall_cells(4.0, 2.5, [(1.0, 2.0, 0.0, 2.0)])
    for _, _, y0, y1 in cells:
        assert not (y0 < 2.0 < y1)


def test_air_is_the_room_and_the_opening_slots():
    room = {"polygon": SQUARE, "height": 2.5, "walls": [
        {"start": SQUARE[0], "end": SQUARE[1],
         "openings": [{"offset": 2.0, "width": 1.0, "height": 2.0, "bottom": 0.0}]},
    ]}
    assert fg.is_air(2.0, 1.0, 1.5, [room])
    assert fg.is_air(2.0, 1.0, -0.05, [room])  # inside the doorway slot
    assert not fg.is_air(0.5, 1.0, -0.05, [room])  # inside solid wall
    assert not fg.is_air(2.0, 2.6, 1.5, [room])  # above the ceiling


def test_atlas_cells_never_overlap_and_fit():
    areas = [30.0, 12.0, 12.0, 4.0, 1.0, 0.2, 0.2]
    corners, sides = fg.atlas_cells(areas, margin=0.006)
    boxes = [(x, y, x + s, y + s) for (x, y), s in zip(corners, sides, strict=True)]
    for x0, y0, x1, y1 in boxes:
        assert 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1
    for i, a in enumerate(boxes):
        for b in boxes[i + 1 :]:
            assert a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1]
    # Texel density roughly even: side tracks the root of area.
    assert sides[0] / sides[1] == pytest.approx((30 / 12) ** 0.5)


def test_window_sill_defaults_and_scan_sills():
    assert Opening(type="window", wallIndex=0, offset=1, width=1, height=1).bottom == DEFAULT_SILL_M
    assert Opening(type="door", wallIndex=0, offset=1, width=1, height=2).bottom == 0.0
    room, _, _ = roomplan.to_rooms(roomplan.load_json(_captured_room()))
    window = next(o for o in room.openings if o.type == "window")
    assert window.sill == pytest.approx(0.9, abs=0.01)  # the fixture's window sits at 0.9 m


def _item(label, size, center=(1.0, 0.4, 1.0), yaw=0.0):
    return ManifestItem(id=f"{label}-1", label=label, roomId="room",
                        box=ItemBox(center=center, size=size, yaw=yaw))


def test_a_piece_faces_away_from_the_wall_it_stands_against():
    room, _, _ = roomplan.to_rooms(roomplan.load_json(_captured_room()))
    poly = room.floorPolygon
    # A sofa pushed against the first wall, its depth axis across that wall.
    (ax, az), (bx, bz) = poly[0], poly[1]
    _, (nx, nz), _ = fg.wall_frame(poly[0], poly[1])
    yaw = math.degrees(math.atan2(bz - az, bx - ax))
    cx, cz = (ax + bx) / 2 - nx * 0.5, (az + bz) / 2 - nz * 0.5
    fx, fz = facing(_item("sofa", (2.0, 0.8, 0.9), (cx, 0.4, cz), yaw), room)
    assert fx * -nx + fz * -nz > 0.99  # facing into the room


def test_stand_in_picks_the_closest_proportions_of_an_allowed_kind(tmp_path, monkeypatch):
    monkeypatch.setattr("walkthrough_pipeline.rebuild.asset_path", lambda url: url)
    catalog = [
        {"category": "dining_table", "dimensionsM": [1.6, 0.75, 0.9], "glb": "/files/big"},
        {"category": "dining_table", "dimensionsM": [0.8, 0.75, 0.8], "glb": "/files/small"},
        {"category": "sofa", "dimensionsM": [0.8, 0.75, 0.8], "glb": "/files/sofa"},
    ]
    assert stand_in(_item("table", (0.85, 0.74, 0.8)), catalog) == "/files/small"
    assert stand_in(_item("bed", (1.6, 0.5, 2.0)), catalog) is None  # drawn as a block


def test_spec_carries_rooms_openings_items_and_outputs(tmp_path):
    room, items, _ = roomplan.to_rooms(roomplan.load_json(_captured_room()), room_id="room")
    manifest = Manifest(id="p", name="p", rooms=[room], items=items)
    paths = ProjectPaths(tmp_path / "p")
    paths.ensure()
    floor = {"id": "oak", "tileSizeM": [0.6, 0.6], "maps": {}, "baseColor": "#c49a63"}
    spec = scene_spec(paths, manifest, floor, [], "#eeeae3", 1024, 64)
    json.dumps(spec)  # plain JSON, for Blender
    (r,) = spec["rooms"]
    assert len(r["walls"]) == 4 and r["height"] == pytest.approx(2.5)
    kinds = sorted(o["type"] for w in r["walls"] for o in w["openings"])
    assert kinds == ["door", "window"]
    assert all("bottom" in o for w in r["walls"] for o in w["openings"])
    (sofa,) = spec["items"]
    assert sofa["model"] is None and sofa["own"] is False
    assert spec["out"]["glb"].endswith("flat.glb")
