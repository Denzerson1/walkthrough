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
    spec = scene_spec(paths, manifest, {"main": floor}, [], "#eeeae3", 1024, 64)
    json.dumps(spec)  # plain JSON, for Blender
    (r,) = spec["rooms"]
    assert len(r["walls"]) == 4 and r["height"] == pytest.approx(2.5)
    kinds = sorted(o["type"] for w in r["walls"] for o in w["openings"])
    assert kinds == ["door", "window"]
    assert all("bottom" in o for w in r["walls"] for o in w["openings"])
    (sofa,) = spec["items"]
    assert sofa["model"] is None and sofa["own"] is False
    assert spec["out"]["glb"].endswith("flat.glb")


# The scanned attic flat's shape: a flat ceiling at 2.57 m that slopes down
# to a 0.81 m knee wall along z = 3.71.
FLAT_CEILING = [[-3.0, 2.57, -1.3], [3.0, 2.57, -1.3], [3.0, 2.57, 2.21], [-3.0, 2.57, 2.21]]
SLOPE = [[-3.0, 2.57, 2.21], [3.0, 2.57, 2.21], [3.0, 0.81, 3.71], [-3.0, 0.81, 3.71]]


def test_union_of_floor_zones_is_their_outline():
    entry = [[-0.78, -3.71], [1.08, -3.71], [1.08, -1.23], [0.18, -1.23], [0.18, -1.73],
             [-0.78, -1.73]]
    living = [[-2.99, -1.27], [-0.78, -1.27], [-0.78, -1.73], [0.18, -1.73], [0.18, -1.23],
              [2.99, -1.23], [2.99, 3.71], [-2.99, 3.71]]
    outline = fg.polygon_union([entry, living])
    assert fg.signed_area(outline) == pytest.approx(
        fg.signed_area(entry) + fg.signed_area(living), abs=1e-6)
    assert len(outline) == 8  # the shared zig-zag between the zones is gone
    assert [-0.78, -1.73] not in outline


def test_ceiling_height_follows_the_slope():
    ceilings = [FLAT_CEILING, SLOPE]
    assert fg.ceiling_height(0.0, 0.0, ceilings, 2.5) == pytest.approx(2.57)
    assert fg.ceiling_height(0.0, 3.71 - 0.75, ceilings, 2.5) == pytest.approx(1.69, abs=0.01)
    assert fg.ceiling_height(9.0, 9.0, ceilings, 2.5) == 2.5  # nothing overhead


def test_gable_wall_profile_and_pieces():
    # The side wall x = 3 runs from the back (z = -1.3) to the knee wall.
    profile = fg.wall_profile([3.0, -1.3], [3.0, 3.71], [FLAT_CEILING, SLOPE], 2.5)
    assert [round(u, 2) for u, _ in profile] == [0.0, 3.51, 5.01]
    assert profile[0][1] == pytest.approx(2.57) and profile[-1][1] == pytest.approx(0.82, abs=0.02)
    pieces = fg.wall_pieces(5.01, profile, [])
    area = sum((u1 - u0) * ((t0 + t1) / 2 - y0) for u0, u1, y0, t0, t1 in pieces)
    assert area == pytest.approx(3.51 * 2.57 + 1.5 * (2.57 + 0.82) / 2, abs=0.05)


def test_wall_pieces_never_bend_and_keep_openings_empty():
    profile = [(0.0, 2.5), (2.0, 2.5), (4.0, 1.0)]
    door = (0.5, 1.4, 0.0, 2.0)
    pieces = fg.wall_pieces(4.0, profile, [door])
    for u0, u1, y0, t0, t1 in pieces:
        assert t0 >= y0 and t1 >= y0
        assert not (0.5 < (u0 + u1) / 2 < 1.4 and y0 < 2.0)
    # No piece's top bends: at its middle, the real top (the profile, capped
    # at the piece's row, 0-2.0 m under the door head, 2.0-2.5 m above) is
    # the straight line between its ends.
    def real_top(u):
        return 2.5 if u <= 2.0 else 2.5 - 1.5 * (u - 2.0) / 2.0

    for u0, u1, y0, t0, t1 in pieces:
        row_top = 2.0 if y0 < 2.0 else 2.5
        assert min(row_top, real_top((u0 + u1) / 2)) == pytest.approx((t0 + t1) / 2)


def test_roof_window_lining_flares_plumb_below_and_level_above():
    frame = fg.slope_frame(SLOPE)
    assert math.degrees(frame["angle"]) == pytest.approx(49.6, abs=0.2)
    assert frame["n"][1] > 0  # out of the room is up
    w = fg.roof_window(frame, at=(1.6, 3.0), width=0.78, length=1.18, depth=0.22, bottom=0.0)
    u0, u1, v0, v1 = w["hole"]
    assert u1 - u0 == pytest.approx(0.78)
    assert v1 - v0 == pytest.approx(0.22 * math.tan(frame["angle"]) + 1.18
                                    + 0.22 / math.tan(frame["angle"]))
    bottom = w["lining"][2]  # plumb: its inner and outer edges line up in plan
    assert bottom[0][0] == pytest.approx(bottom[3][0])
    assert bottom[0][2] == pytest.approx(bottom[3][2])
    top = w["lining"][3]  # level
    assert top[0][1] == pytest.approx(top[3][1])
    centre_x = w["centre"][0]
    assert centre_x == pytest.approx(1.6, abs=1e-6)


def test_sun_position_at_vienna_noon_on_the_equinox():
    # Solar noon in Vienna on CET is about 12:02 (longitude 16.37 E, equation of time).
    bearing, elevation = fg.sun_position(2026, 3, 20, 12 + 2 / 60, 1, 48.21, 16.37)
    assert bearing == pytest.approx(180, abs=3)
    assert elevation == pytest.approx(90 - 48.21, abs=1.5)


def test_compass_to_scene_turns_clockwise():
    facing = (0.0, 1.0)  # windows face +z, bearing 147
    assert fg.compass_to_scene(147, 147, facing) == pytest.approx((0.0, 1.0))
    assert fg.compass_to_scene(237, 147, facing) == pytest.approx((-1.0, 0.0))


def test_union_snaps_corners_that_round_apart():
    # Two pieces whose shared corner differs by 0.2 mm across a rounding boundary.
    a = [[0.0, 0.0], [1.0, 0.0], [1.0, 1.1849], [0.0, 1.1849]]
    b = [[0.0, 1.1851], [1.0, 1.1851], [1.0, 2.0], [0.0, 2.0]]
    outline = fg.polygon_union([a, b])
    assert len(outline) == 4
    assert fg.signed_area(outline) == pytest.approx(2.0, abs=1e-3)


def test_coplanar_ceiling_pieces_merge():
    left = [[-3.0, 2.57, 2.21], [0.18, 2.57, 2.21], [0.18, 0.81, 3.71], [-3.0, 0.81, 3.71]]
    right = [[0.18, 2.57, 2.21], [3.0, 2.57, 2.21], [3.0, 0.81, 3.71], [0.18, 0.81, 3.71]]
    flat_a = [[-3.0, 2.57, -1.3], [0.18, 2.57, -1.3], [0.18, 2.57, 2.21], [-3.0, 2.57, 2.21]]
    flat_b = [[0.18, 2.57, -1.3], [3.0, 2.57, -1.3], [3.0, 2.57, 2.21], [0.18, 2.57, 2.21]]
    merged = fg.merge_ceilings([left, right, flat_a, flat_b])
    assert len(merged) == 2
    slope = next(c for c in merged if not fg.is_flat(c))
    assert len(slope) == 4
    assert {round(p[0], 2) for p in slope} == {-3.0, 3.0}


def test_gable_bends_where_the_ceiling_stops_just_short_of_the_wall():
    # The wall line from the floor outline is 1.3 mm outside the scanned ceiling's edge.
    flat = [[-2.99, 2.57, -1.27], [2.99, 2.57, -1.27], [2.99, 2.57, 2.21], [-2.99, 2.57, 2.21]]
    slope = [[-2.99, 2.57, 2.21], [2.99, 2.57, 2.21], [2.99, 0.81, 3.71], [-2.99, 0.81, 3.71]]
    profile = fg.wall_profile([-2.9913, 3.7137], [-2.9913, -1.2725], [flat, slope], 2.5)
    assert len(profile) == 3
    assert profile[1][1] == pytest.approx(2.57, abs=0.02)


def test_mitres_reach_out_at_convex_corners_and_in_at_inside_ones():
    t = 0.15
    square = fg.mitres(SQUARE, t)
    # Each wall's outer face runs one thickness past its end and before its start.
    assert all(start == pytest.approx(-t) and end == pytest.approx(t) for start, end in square)
    # An L: the corner at (2, 1) turns inwards, so the walls meeting there stop short.
    ell = [[0.0, 0.0], [4.0, 0.0], [4.0, 3.0], [2.0, 3.0], [2.0, 1.0], [0.0, 1.0]]
    reach = fg.mitres(ell, t)
    assert reach[3][1] == pytest.approx(-t)  # (2, 3) -> (2, 1) ends short of (2, 1)
    assert reach[4][0] == pytest.approx(t)  # (2, 1) -> (0, 1) starts past it


def test_convex_hull_of_a_footprint():
    pts = [[0, 0], [2, 0], [2, 1], [0, 1], [1, 0.5], [1, 0], [2, 0.5]]
    hull = fg.convex_hull(pts)
    assert fg.signed_area(hull) == pytest.approx(2.0)
    assert len(hull) == 4


def test_a_bed_facing_along_its_box_is_sized_length_deep():
    from walkthrough_pipeline.rebuild import sized_for

    # Scanned as (2.09 long, 0.69, 1.69 wide) with its depth along x (yaw 270).
    assert sized_for((0, -1), [2.09, 0.69, 1.69], 270) == [1.69, 0.69, 2.09]
    assert sized_for((1, 0), [2.09, 0.69, 1.69], 270) == [2.09, 0.69, 1.69]
