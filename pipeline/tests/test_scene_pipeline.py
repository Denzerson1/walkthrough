"""
`align` and `export` end to end on a synthetic capture: a splat in a tilted,
scaled, rotated 'COLMAP' frame, camera poses in COLMAP's binary format, and a
RoomPlan scan. The scene must come out level, metric and on the plan.
"""

from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

import numpy as np
import pytest
from walkthrough_pipeline import roomplan
from walkthrough_pipeline.align import _axis_angle
from walkthrough_pipeline.colmap import read_images_bin
from walkthrough_pipeline.manifest import Manifest
from walkthrough_pipeline.paths import ProjectPaths
from walkthrough_pipeline.register import distance_to_segments, solid_segments
from walkthrough_pipeline.scene import (
    AlignmentFailed,
    align_scene,
    export_scene,
    floor_outline,
    points_in_polygon,
)
from walkthrough_pipeline.splat import SplatCloud, read_ply, write_ply

sys.path.insert(0, str(Path(__file__).parent))
from test_roomplan import H, _captured_room  # noqa: E402

# plan -> COLMAP: X' = S R X + T, deliberately not level.
S_TRUE = 0.37
R_TRUE = _axis_angle(np.array([0.4, 0.2, 1.0]), 2.3)
T_TRUE = np.array([3.0, -1.5, 7.0])


def _to_colmap(points):
    return S_TRUE * points @ R_TRUE.T + T_TRUE


def _plan_room():
    return roomplan.to_rooms(roomplan.load_json(_captured_room()), room_id="room")[0]


def _plan_points(room, rng):
    """Splat centres in the plan frame: solid walls, floor, ceiling, clutter."""
    a, b = solid_segments(room)
    pts = []
    for start, end in zip(a, b, strict=True):
        n = int(np.linalg.norm(end - start) * 1500)
        t = rng.uniform(0, 1, n)
        xz = start + (end - start) * t[:, None]
        pts.append(np.column_stack([xz[:, 0], rng.uniform(0.05, H - 0.05, n), xz[:, 1]]))
    poly = np.array(room.floorPolygon)
    lo, hi = poly.min(0), poly.max(0)
    floor_xz = rng.uniform(lo, hi, (40_000, 2))
    floor_xz = floor_xz[points_in_polygon(floor_xz, poly)]
    floor = np.column_stack([floor_xz[:, 0], rng.normal(0, 0.003, len(floor_xz)), floor_xz[:, 1]])
    ceil_xz = rng.uniform(lo, hi, (20_000, 2))
    ceiling = np.column_stack([ceil_xz[:, 0], np.full(len(ceil_xz), H), ceil_xz[:, 1]])
    sofa = rng.uniform([1.2, 0.0, 2.6], [3.0, 0.8, 3.4], (3000, 3))  # rough box, inside
    return np.vstack([*pts, floor, ceiling, sofa]), len(floor)


def _write_images_bin(path: Path, cameras):
    out = struct.pack("<Q", len(cameras))
    for i, (rot, t) in enumerate(cameras, start=1):
        w = np.sqrt(max(0.0, 1 + np.trace(rot))) / 2
        q = np.array([w, (rot[2, 1] - rot[1, 2]) / (4 * w), (rot[0, 2] - rot[2, 0]) / (4 * w),
                      (rot[1, 0] - rot[0, 1]) / (4 * w)])
        out += struct.pack("<idddddddi", i, *q, *t, 1)
        out += f"frame_{i:05d}.png".encode() + b"\0"
        out += struct.pack("<Q", 0)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(out)


def _cameras(room, rng, n=60):
    """Handheld poses: upright, looking about the room, at knee-to-eye height."""
    centre = np.mean(room.floorPolygon, axis=0)
    cams = []
    for _ in range(n):
        yaw = rng.uniform(0, 2 * np.pi)
        c_plan = np.array([centre[0], rng.choice([0.5, 1.3, 1.6]), centre[1]])
        c_plan[[0, 2]] += rng.normal(0, 0.4, 2)
        forward = np.array([np.cos(yaw), rng.normal(0, 0.1), np.sin(yaw)])
        forward /= np.linalg.norm(forward)
        down = np.array([0.0, -1.0, 0.0])
        down -= forward * forward.dot(down)
        down /= np.linalg.norm(down)
        right = np.cross(down, forward)
        r_plan = np.stack([right, down, forward])  # world -> camera, OpenCV axes
        r = r_plan @ R_TRUE.T
        c = _to_colmap(c_plan[None])[0]
        cams.append((r, -r @ c))
    return cams


@pytest.fixture
def capture(tmp_path):
    rng = np.random.default_rng(1)
    room = _plan_room()
    plan_pts, n_floor = _plan_points(room, rng)
    n = len(plan_pts)
    q = np.zeros((n, 4))
    q[:, 0] = 1
    cloud = SplatCloud(
        positions=_to_colmap(plan_pts).astype(np.float32),
        colours=np.full((n, 3), 0.6, dtype=np.float32),
        opacities=np.full(n, 0.95, dtype=np.float32),
        scales=np.full((n, 3), 0.004, dtype=np.float32),
        rotations=q.astype(np.float32),
        sh_rest=rng.normal(0, 0.05, (n, 3, 3)).astype(np.float32),
    )
    paths = ProjectPaths(tmp_path / "proj")
    paths.ensure()
    write_ply(cloud, paths.work / "trained.ply")
    _write_images_bin(paths.work / "undistorted" / "sparse" / "images.bin", _cameras(room, rng))
    (paths.input / "roomplan").mkdir()
    (paths.input / "roomplan" / "scan.json").write_text(json.dumps(_captured_room()))
    return paths, room, n_floor


def test_images_bin_round_trip(capture):
    paths, _, _ = capture
    cams = read_images_bin(paths.work / "undistorted" / "sparse" / "images.bin")
    assert len(cams) == 60
    # Every camera was upright in the plan, give or take a little pitch, so
    # its up maps back to near plan +Y.
    ups = np.array([c.up for c in cams]) @ R_TRUE
    assert ups[:, 1].min() > 0.95
    assert np.linalg.norm(ups.mean(axis=0) / np.linalg.norm(ups.mean(axis=0)) - [0, 1, 0]) < 0.01


def test_align_puts_the_splat_on_the_plan_in_metres(capture):
    paths, room, _ = capture
    report = align_scene(paths, "proj")

    assert report.up_from == "cameras"
    assert report.scale_from == "roomplan"
    assert report.scale == pytest.approx(1 / S_TRUE, rel=0.005)
    assert report.roomplan["inlier_ratio"] > 0.5

    scene = read_ply(paths.scene / "scene.ply")
    y = scene.positions[:, 1]
    # Floor at y = 0 and ceiling at the scan's wall height.
    assert np.median(np.abs(y[np.abs(y) < 0.05])) < 0.005
    assert np.sum(np.abs(y - H) < 0.02) > 15_000

    wall_band = (y > 0.5) & (y < 2.0)
    d, _ = distance_to_segments(
        scene.positions[wall_band][:, [0, 2]].astype(np.float64), solid_segments(room)
    )
    assert np.median(d) < 0.01

    manifest = Manifest.load(paths.manifest)
    assert manifest.rooms[0].floorPolygon == room.floorPolygon
    assert [i.label for i in manifest.items] == ["sofa"]
    assert manifest.assets.scene == "scene.ply"


def test_align_never_touches_the_trainer_output(capture):
    paths, _, _ = capture
    before = (paths.work / "trained.ply").read_bytes()
    align_scene(paths, "proj")
    align_scene(paths, "proj")  # rerunnable: same input, same answer
    assert (paths.work / "trained.ply").read_bytes() == before


def test_align_refuses_a_scan_of_another_room(capture):
    paths, _, _ = capture
    other = _captured_room()
    for wall in other["walls"]:
        wall["dimensions"][0] *= 2.3  # a much bigger room
        t = wall["transform"]
        t[12] *= 2.3
        t[14] *= 2.3
    (paths.input / "roomplan" / "scan.json").write_text(json.dumps(other))
    with pytest.raises(AlignmentFailed, match="does not fit"):
        align_scene(paths, "proj")
    assert not (paths.scene / "scene.ply").exists()


def test_export_splits_the_floor_and_writes_spz(capture):
    paths, _, n_floor = capture
    align_scene(paths, "proj")
    report = export_scene(paths)

    assert report.sh_degree == 1
    # Every generated floor point, and essentially nothing else.
    assert report.floor_splats == pytest.approx(n_floor, rel=0.02)
    manifest = Manifest.load(paths.manifest)
    assert manifest.assets.sceneNoFloor == "scene_nofloor.spz"
    assert manifest.assets.floor == "floor.spz"
    assert manifest.rooms[0].originalFloorColor == "#999999"
    for name in ("scene.spz", "scene_nofloor.spz", "floor.spz"):
        assert (paths.scene / name).stat().st_size > 1000


def test_export_crops_what_lies_beyond_the_walls(capture):
    paths, _, _ = capture
    align_scene(paths, "proj")
    scene = read_ply(paths.scene / "scene.ply")
    floater = SplatCloud(
        positions=np.array([[50.0, 1.0, 50.0]], dtype=np.float32),
        colours=np.ones((1, 3), dtype=np.float32),
        opacities=np.ones(1, dtype=np.float32),
        scales=np.full((1, 3), 0.01, dtype=np.float32),
        rotations=np.array([[1.0, 0, 0, 0]], dtype=np.float32),
        sh_rest=np.zeros((1, 3, 3), dtype=np.float32),
    )
    merged = SplatCloud(
        *(np.concatenate([getattr(scene, f), getattr(floater, f)])
          for f in ("positions", "colours", "opacities", "scales", "rotations", "sh_rest"))
    )
    write_ply(merged, paths.scene / "scene.ply")
    assert export_scene(paths).cropped_out == 1
    assert export_scene(paths, crop_margin=None).cropped_out == 0


def test_points_in_polygon_matches_the_even_odd_rule():
    poly = np.array([[0, 0], [4, 0], [4, 2], [2, 2], [2, 4], [0, 4]], dtype=float)  # L-shape
    pts = np.array([[1, 1], [3, 1], [3, 3], [1, 3], [5, 1]], dtype=float)
    assert points_in_polygon(pts, poly).tolist() == [True, True, False, True, False]


def test_floor_is_found_when_most_splats_are_faint(capture):
    """
    gsplat's MCMC leaves most splats nearly transparent (median opacity
    ~0.035 on the first real room). A fixed opacity cut kept 4% of them and
    the floor fit tilted 19°; the relative cut must still find a level floor.
    """
    paths, _, _ = capture
    cloud = read_ply(paths.work / "trained.ply")
    rng = np.random.default_rng(5)
    cloud.opacities = np.where(
        rng.uniform(size=len(cloud)) < 0.8, rng.uniform(0.001, 0.05, len(cloud)),
        rng.uniform(0.06, 0.25, len(cloud)),
    ).astype(np.float32)
    write_ply(cloud, paths.work / "trained.ply")
    report = align_scene(paths, "proj")
    assert report.scale == pytest.approx(1 / S_TRUE, rel=0.01)


def test_gravity_ignores_pitch_when_every_camera_looks_down_one_way():
    from walkthrough_pipeline.colmap import Camera, gravity_from_cameras

    cams = []
    for yaw in np.radians(np.linspace(-40, 40, 9)):  # a half-orbit, all pitched down 30°
        pitch = np.radians(-30)
        cp = np.cos(pitch)
        forward = np.array([cp * np.sin(yaw), np.sin(pitch), cp * np.cos(yaw)])
        right = np.cross(forward, [0.0, 1.0, 0.0])
        right /= np.linalg.norm(right)
        down = np.cross(forward, right)
        rot = np.stack([right, down, forward])
        rot = rot @ R_TRUE.T  # into the arbitrary COLMAP frame
        cams.append(Camera("x", rot, np.zeros(3)))
    up = gravity_from_cameras(cams) @ R_TRUE
    np.testing.assert_allclose(up, [0, 1, 0], atol=1e-6)
    biased = np.mean([c.up for c in cams], axis=0) @ R_TRUE
    assert np.degrees(np.arccos(biased[1] / np.linalg.norm(biased))) > 20  # what it replaced


def _area(poly):
    x, z = np.asarray(poly).T
    return abs(0.5 * float(np.sum(x * np.roll(z, -1) - np.roll(x, -1) * z)))


def test_floor_outline_ignores_strays_far_from_the_room():
    """The first real capture outlined 740 m² for one room from a point hull."""
    rng = np.random.default_rng(0)
    room = rng.uniform([0, 0], [5, 4], (60_000, 2))
    strays = rng.uniform([-30, -30], [30, 30], (3000, 2))  # sparse, everywhere
    poly = floor_outline(np.vstack([room, strays]))
    assert _area(poly) == pytest.approx(20.0, rel=0.08)


def test_floor_outline_keeps_an_l_shape():
    rng = np.random.default_rng(1)
    pts = rng.uniform([0, 0], [6, 6], (120_000, 2))
    pts = pts[~((pts[:, 0] > 3) & (pts[:, 1] > 3))]  # remove a quarter: an L, 27 m²
    poly = floor_outline(pts)
    assert _area(poly) == pytest.approx(27.0, rel=0.08)
    assert not points_in_polygon(np.array([[4.5, 4.5]]), poly)[0]


def test_without_a_scan_the_viewer_starts_where_the_video_was_shot(capture):
    paths, _, _ = capture
    (paths.input / "roomplan" / "scan.json").unlink()
    report = align_scene(paths, "proj")
    assert report.scale_from == "camera height (estimate)"
    room = Manifest.load(paths.manifest).rooms[0]
    cams = read_images_bin(paths.work / "undistorted" / "sparse" / "images.bin")
    t = np.array(Manifest.load(paths.manifest).transform)
    centres = np.array([c.centre for c in cams]) @ t[:3, :3].T + t[:3, 3]
    x, _, z = room.waypoint.position
    np.testing.assert_allclose([x, z], np.median(centres[:, [0, 2]], axis=0), atol=1e-3)
    assert _area(room.floorPolygon) == pytest.approx(
        _area(_plan_room().floorPolygon) * (report.scale * S_TRUE) ** 2, rel=0.1
    )


def test_the_viewer_starts_facing_what_was_filmed(capture):
    """Cameras that all look one way: the waypoint yaw must look that way too."""
    paths, room, _ = capture
    rng = np.random.default_rng(2)
    centre = np.mean(room.floorPolygon, axis=0)
    cams = []
    for _ in range(40):  # all looking roughly toward plan +X
        yaw = rng.normal(0, 0.3)
        forward = np.array([np.cos(yaw), 0.0, np.sin(yaw)])
        down = np.array([0.0, -1.0, 0.0])
        right = np.cross(down, forward)
        r = np.stack([right, down, forward]) @ R_TRUE.T
        c = _to_colmap(np.array([[centre[0], 1.3, centre[1]]]))[0]
        cams.append((r, -r @ c))
    _write_images_bin(paths.work / "undistorted" / "sparse" / "images.bin", cams)
    align_scene(paths, "proj")
    wp = Manifest.load(paths.manifest).rooms[0].waypoint
    # lib/camera.ts: yaw 0 looks down -Z, direction (-sin yaw, -cos yaw).
    look = np.array([-np.sin(np.radians(wp.yaw)), -np.cos(np.radians(wp.yaw))])
    np.testing.assert_allclose(look, [1.0, 0.0], atol=0.1)
