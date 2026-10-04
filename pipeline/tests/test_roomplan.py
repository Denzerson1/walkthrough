"""RoomPlan import, and registering a splat onto the scan."""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest
from walkthrough_pipeline import roomplan
from walkthrough_pipeline.register import (
    ceiling_height,
    register_to_plan,
    solid_segments,
    wall_points,
)

# A 4.2 x 3.6 m room, turned 30° in ARKit's frame, with the phone's starting
# point (the origin) 1.45 m above the floor — as a real scan would be.
W, D, H = 4.2, 3.6, 2.5
FLOOR_Y = -1.45
TURN = 30.0


def _transform(centre_xz, height_y, facing_deg):
    """Column-major 16 floats: right vector along `facing_deg` in (x, z)."""
    a = math.radians(facing_deg)
    right = [math.cos(a), 0.0, math.sin(a)]
    up = [0.0, 1.0, 0.0]
    normal = np.cross(right, up).tolist()
    return [*right, 0.0, *up, 0.0, *normal, 0.0, centre_xz[0], height_y, centre_xz[1], 1.0]


def _world(x, z):
    a = math.radians(TURN)
    return (x * math.cos(a) - z * math.sin(a) + 0.7, x * math.sin(a) + z * math.cos(a) - 2.1)


def _captured_room(nested_transforms=False):
    corners = [(0, 0), (W, 0), (W, D), (0, D)]
    walls = []
    for i in range(4):
        (x0, z0), (x1, z1) = corners[i], corners[(i + 1) % 4]
        facing = math.degrees(math.atan2(z1 - z0, x1 - x0)) + TURN
        walls.append(
            {
                "identifier": f"W{i}",
                "category": {"wall": {}},
                "dimensions": [math.hypot(x1 - x0, z1 - z0), H, 0.0],
                "transform": _transform(_world((x0 + x1) / 2, (z0 + z1) / 2), FLOOR_Y + H / 2,
                                        facing),
            }
        )

    def surface(kind, category, along_wall0_x, width, height, bottom):
        return {
            "category": {category: {"isOpen": False}} if category == "door" else {category: {}},
            "dimensions": [width, height, 0.0],
            "transform": _transform(_world(along_wall0_x, 0.0), FLOOR_Y + bottom + height / 2,
                                    TURN),
            "parentIdentifier": "W0",
        }

    data = {
        "walls": walls,
        "doors": [surface("doors", "door", 0.8, 0.9, 2.0, 0.0)],
        "windows": [surface("windows", "window", 3.0, 1.2, 1.2, 0.9)],
        "openings": [],
        "objects": [
            {
                "category": {"sofa": {}},
                "dimensions": [2.0, 0.85, 0.9],
                "transform": _transform(_world(2.1, 3.0), FLOOR_Y + 0.425, TURN + 180),
            }
        ],
        "sections": [{"label": {"livingRoom": {}}, "center": [0, 0, 0]}],
    }
    if nested_transforms:
        for group in data.values():
            for e in group:
                if "transform" in e:
                    t = e["transform"]
                    e["transform"] = [t[0:4], t[4:8], t[8:12], t[12:16]]
    return data


def _area(poly):
    x, z = np.array(poly).T
    return 0.5 * float(np.sum(x * np.roll(z, -1) - np.roll(x, -1) * z))


# --------------------------------------------------------------------------
# Import
# --------------------------------------------------------------------------


@pytest.mark.parametrize("nested", [False, True])
def test_room_outline_floor_and_type_come_from_the_scan(nested):
    scan = roomplan.load_json(_captured_room(nested_transforms=nested))
    room, items, floor_y = roomplan.to_rooms(scan, room_id="living")

    assert floor_y == pytest.approx(FLOOR_Y, abs=1e-6)
    assert room.type == "living"
    assert _area(room.floorPolygon) == pytest.approx(W * D, rel=1e-4)  # and positive: CCW
    assert len(room.walls) == 4
    assert all(w.height == pytest.approx(H) for w in room.walls)


def test_openings_land_on_the_right_wall_at_the_right_offset():
    room, _, _ = roomplan.to_rooms(roomplan.load_json(_captured_room()))
    door = next(o for o in room.openings if o.type == "door")
    window = next(o for o in room.openings if o.type == "window")
    assert door.wallIndex == window.wallIndex

    wall = room.walls[door.wallIndex]
    start = np.array(wall.start)
    door_world = np.array(_world(0.8, 0.0))
    assert door.offset == pytest.approx(float(np.linalg.norm(door_world - start)), abs=1e-3)
    assert (door.width, door.height) == (pytest.approx(0.9), pytest.approx(2.0))
    assert window.width == pytest.approx(1.2)


def test_furniture_boxes_sit_on_the_floor_with_solver_categories():
    _, items, _ = roomplan.to_rooms(roomplan.load_json(_captured_room()), room_id="living")
    (sofa,) = items
    assert sofa.label == "sofa" and sofa.source == "roomplan" and sofa.roomId == "living"
    assert sofa.box.center[1] == pytest.approx(0.425, abs=1e-3)  # half its height
    assert sofa.box.yaw == pytest.approx((TURN + 180) % 360, abs=0.1)


def test_waypoint_is_inside_the_room_at_eye_height():
    room, _, _ = roomplan.to_rooms(roomplan.load_json(_captured_room()))
    x, y, z = room.waypoint.position
    centre = np.mean(room.floorPolygon, axis=0)
    assert y == pytest.approx(1.6)
    assert math.hypot(x - centre[0], z - centre[1]) < 1e-3


def test_floor_polygon_is_used_when_the_scan_has_one():
    data = _captured_room()
    data["floors"] = [
        {
            "category": {"floor": {}},
            "dimensions": [3.0, 2.0, 0.0],
            "transform": _transform((0.0, 0.0), FLOOR_Y, 0.0),
            "polygonCorners": [[0, 0, 0], [3, 0, 0], [3, 0, 2], [0, 0, 2]],
        }
    ]
    room, _, _ = roomplan.to_rooms(roomplan.load_json(data))
    assert _area(room.floorPolygon) == pytest.approx(6.0)


def test_find_export_prefers_json(tmp_path):
    (tmp_path / "scan.usdz").write_bytes(b"")
    (tmp_path / "scan.json").write_text(json.dumps(_captured_room()))
    assert roomplan.find_export(tmp_path).name == "scan.json"


def test_rejects_json_that_is_not_a_scan():
    with pytest.raises(roomplan.RoomPlanError):
        roomplan.load_json({"hello": "world"})


# --------------------------------------------------------------------------
# Registration
# --------------------------------------------------------------------------


def _synthetic_splat(room, scale, yaw_deg, shift, seed=0):
    """
    Points on the room's solid walls, floor and ceiling plus clutter, then
    moved into an arbitrary 'COLMAP' frame: plan = s R splat + t inverted.
    """
    rng = np.random.default_rng(seed)
    a, b = solid_segments(room)
    pts = []
    for start, end in zip(a, b, strict=True):
        n = int(np.linalg.norm(end - start) * 800)
        t = rng.uniform(0, 1, n)
        xz = start + (end - start) * t[:, None] + rng.normal(0, 0.005, (n, 2))
        pts.append(np.column_stack([xz[:, 0], rng.uniform(0, H, n), xz[:, 1]]))
    poly = np.array(room.floorPolygon)
    lo, hi = poly.min(0), poly.max(0)
    for y in (0.0, H):  # floor and ceiling
        xz = rng.uniform(lo, hi, (8000, 2))
        pts.append(np.column_stack([xz[:, 0], np.full(8000, y), xz[:, 1]]))
    # A wardrobe-height clutter blob, and floaters seen through the window.
    blob = rng.normal(0, 0.3, (1500, 3)) + [(lo[0] + hi[0]) / 2, 1.4, (lo[1] + hi[1]) / 2]
    floaters = rng.uniform([lo[0] - 3, 1.2, lo[1] - 3], [hi[0] + 3, 2.0, hi[1] + 3], (800, 3))
    plan_pts = np.vstack([*pts, blob, floaters])

    c, s_ = math.cos(math.radians(yaw_deg)), math.sin(math.radians(yaw_deg))
    r = np.array([[c, -s_], [s_, c]])
    xz = (plan_pts[:, [0, 2]] - shift) @ r / scale  # invert plan = s R p + t
    positions = np.column_stack([xz[:, 0], plan_pts[:, 1] / scale, xz[:, 1]])
    return positions, np.ones(len(positions))


@pytest.mark.parametrize(
    ("scale", "yaw", "shift"),
    [(3.2, 37.0, (1.5, -0.8)), (0.41, 200.0, (-4.0, 2.0)), (1.0, 91.0, (0.0, 0.0))],
)
def test_registration_recovers_scale_yaw_and_position(scale, yaw, shift):
    room, _, _ = roomplan.to_rooms(roomplan.load_json(_captured_room()))
    positions, opacities = _synthetic_splat(room, scale, yaw, np.array(shift))

    ceiling = ceiling_height(positions[:, 1])
    assert ceiling == pytest.approx(H / scale, rel=0.02)
    fit = register_to_plan(
        wall_points(positions, opacities, ceiling), solid_segments(room), H / ceiling
    )

    assert fit.scale == pytest.approx(scale, rel=0.003)
    assert (fit.yaw_deg - yaw + 180) % 360 - 180 == pytest.approx(0, abs=0.3)
    probe = positions[:5, [0, 2]]
    expected = scale * probe @ np.array(
        [[math.cos(math.radians(yaw)), -math.sin(math.radians(yaw))],
         [math.sin(math.radians(yaw)), math.cos(math.radians(yaw))]]
    ).T + shift
    np.testing.assert_allclose(fit.apply(probe), expected, atol=0.01)
    assert fit.inlier_ratio > 0.5
    assert fit.median_error_m < 0.02


def test_doors_and_windows_tell_a_rectangle_from_itself_turned_round():
    """Without the openings the 180° pose would fit the walls equally well."""
    room, _, _ = roomplan.to_rooms(roomplan.load_json(_captured_room()))
    positions, opacities = _synthetic_splat(room, 1.0, 180.0, np.zeros(2), seed=3)
    fit = register_to_plan(wall_points(positions, opacities, H), solid_segments(room), 1.0)
    assert abs((fit.yaw_deg - 180 + 180) % 360 - 180) < 0.5
    assert fit.symmetry_ratio < 0.9


def test_matrix_maps_points_like_apply():
    room, _, _ = roomplan.to_rooms(roomplan.load_json(_captured_room()))
    positions, opacities = _synthetic_splat(room, 2.0, 60.0, np.array([1.0, 2.0]))
    fit = register_to_plan(
        wall_points(positions, opacities, H / 2), solid_segments(room), 2.0
    )
    homo = np.column_stack([positions[:10], np.ones(10)])
    moved = (homo @ fit.matrix().T)[:, :3]
    np.testing.assert_allclose(moved[:, [0, 2]], fit.apply(positions[:10, [0, 2]]), atol=1e-9)
    np.testing.assert_allclose(moved[:, 1], positions[:10, 1] * fit.scale, atol=1e-9)
    assert np.linalg.det(fit.matrix()[:3, :3]) > 0  # never mirrored


def test_no_ceiling_is_reported_as_unknown():
    rng = np.random.default_rng(0)
    assert ceiling_height(rng.uniform(0, 2.5, 50_000)) is None


# --------------------------------------------------------------------------
# Polycam Floorplan glTF — a real export (128 KB, committed as a fixture)
# --------------------------------------------------------------------------

POLYCAM = Path(__file__).parent / "fixtures" / "polycam-bedroom.glb"


def test_polycam_glb_reads_as_a_room():
    room, items, floor_y = roomplan.to_rooms(roomplan.load(POLYCAM))
    assert room.type == "bedroom"
    assert floor_y == pytest.approx(0.0, abs=0.01)
    assert _area(room.floorPolygon) == pytest.approx(2.636 * 4.726, rel=0.01)
    # Walls sink 10 cm below the floor in this export; the ceiling is at 2.53.
    assert room.walls[0].height == pytest.approx(2.53, abs=0.01)
    assert sorted(o.type for o in room.openings) == ["door", "door"]
    assert sorted(i.label for i in items) == [
        "bed", "chair", "chair", "chair", "storage", "storage", "storage", "table"
    ]
    bed = next(i for i in items if i.label == "bed")
    assert bed.box.size[0] == pytest.approx(2.17, abs=0.02)  # long side first


def test_find_export_finds_a_glb(tmp_path):
    (tmp_path / "scan.glb").write_bytes(POLYCAM.read_bytes())
    assert roomplan.find_export(tmp_path).name == "scan.glb"
