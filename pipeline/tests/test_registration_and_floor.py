"""
Regressions from the pipeline audit: plan registration that let metric
scale drift (1.12 from a 0.98-1.02 search), and an ARKit capture whose
measured gravity was overwritten by a floor fit allowed to tilt 35°.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest
from scipy.spatial.transform import Rotation
from walkthrough_pipeline.align import FloorRejected, find_floor
from walkthrough_pipeline.manifest import Manifest
from walkthrough_pipeline.paths import ProjectPaths
from walkthrough_pipeline.register import (
    _rot2,
    register_to_plan,
    solid_segments,
    solid_segments_by_wall,
    wall_lean,
)
from walkthrough_pipeline.scene import AlignmentFailed, align_scene, aligned_dir
from walkthrough_pipeline.splat import SplatCloud, write_ply

sys.path.insert(0, str(Path(__file__).parent))
import test_scene_pipeline as tsp  # noqa: E402


def _wall_samples(room, rng, per_metre=400, skip_wall=None):
    a, b, walls = solid_segments_by_wall(room)
    pts = []
    for i in range(len(a)):
        if walls[i] == skip_wall:
            continue
        n = int(np.linalg.norm(b[i] - a[i]) * per_metre)
        pts.append(a[i] + (b[i] - a[i]) * rng.uniform(0, 1, (n, 1)))
    return np.vstack(pts) + rng.normal(0, 0.004, (sum(len(p) for p in pts), 2))


# --------------------------------------------------------------------------
# C. Metric scale
# --------------------------------------------------------------------------


def test_bounded_scale_holds_through_icp():
    """The audit: a 0.98-1.02 search came back at 1.12 with a fine fit."""
    room = tsp._plan_room()
    rng = np.random.default_rng(0)
    splat = (_wall_samples(room, rng) / 1.12) @ _rot2(30).T + [2.0, -1.0]
    fit = register_to_plan(splat, solid_segments(room), 1.0, (0.98, 1.02))
    assert 0.98 <= fit.scale <= 1.02
    assert fit.scale_bounds == (0.98, 1.02)
    # Held to its bounds, the wrong-sized capture cannot fit well, and the
    # numbers say so (a right fit here is ~3 mm median, every point on a wall).
    assert fit.median_error_m > 0.01 or fit.inlier_ratio < 0.5


def test_free_scale_still_recovers_a_video_scale():
    room = tsp._plan_room()
    rng = np.random.default_rng(1)
    splat = (_wall_samples(room, rng) * 0.37) @ _rot2(-75).T + [5.0, 3.0]
    fit = register_to_plan(splat, solid_segments(room), 1 / 0.37, (0.85, 1.15))
    assert fit.scale == pytest.approx(1 / 0.37, rel=0.005)
    assert fit.median_error_m < 0.01 and fit.coverage > 0.9


def test_rigid_fit_keeps_scale_exactly():
    room = tsp._plan_room()
    rng = np.random.default_rng(2)
    splat = _wall_samples(room, rng) @ _rot2(110).T + [-1.0, 0.5]
    fit = register_to_plan(splat, solid_segments(room), 1.0, fixed_scale=True)
    assert fit.scale == 1.0 and fit.scale_fixed
    assert fit.median_error_m < 0.01
    assert fit.yaw_deg == pytest.approx(-110, abs=0.5)


def test_per_wall_diagnostics_show_a_wall_the_capture_missed():
    room = tsp._plan_room()
    rng = np.random.default_rng(3)
    a, b, walls = solid_segments_by_wall(room)
    # A long side: the door on wall 3 still tells the room from itself
    # turned round. (Without wall 1 it does not, and the per-wall numbers
    # show that too: the fit turns 180° and wall 3 comes out unseen.)
    longest = 0
    splat = _wall_samples(room, rng, skip_wall=longest)
    fit = register_to_plan(splat, (a, b), 1.0, fixed_scale=True, wall_of_segment=walls)
    by_wall = {w["wall"]: w for w in fit.walls}
    # Only the corners, where the neighbouring walls come within 5 cm.
    seen_points = min(w["points"] for k, w in by_wall.items() if k != longest)
    assert by_wall[longest]["coverage"] < 0.15
    assert by_wall[longest]["points"] < 0.15 * seen_points
    seen = [w for k, w in by_wall.items() if k != longest]
    assert min(w["coverage"] for w in seen) > 0.9
    assert all(w["median_mm"] < 10 for w in seen)
    assert fit.coverage < 0.85


def _metric_project(tmp_path, *, size=1.0, tilt_deg=0.0, extra=None, seed=4):
    """
    An ARKit-style project: splat in metres, Y up (optionally tilted, as
    if gravity were wrong), cameras, LiDAR seed points, a RoomPlan scan.
    `size` scales the splat against the scan.
    """
    rng = np.random.default_rng(seed)
    room = tsp._plan_room()
    pts, _ = tsp._plan_points(room, rng)
    if extra is not None:
        pts = np.vstack([pts, extra])
    world = Rotation.from_euler("y", 40, degrees=True).as_matrix()
    world = Rotation.from_euler("x", tilt_deg, degrees=True).as_matrix() @ world
    offset = np.array([0.5, -1.4, 2.0])

    def to_arkit(p):
        return size * p @ world.T + offset

    n = len(pts)
    q = np.zeros((n, 4), np.float32)
    q[:, 0] = 1
    paths = ProjectPaths(tmp_path / "p")
    paths.ensure()
    write_ply(SplatCloud(to_arkit(pts).astype(np.float32), np.full((n, 3), 0.6, np.float32),
                         np.full(n, 0.9, np.float32), np.full((n, 3), 0.004, np.float32), q),
              paths.work / "trained.ply")
    cams = []
    for r, t in tsp._cameras(room, rng):
        # tsp._cameras maps through its own R_TRUE/S_TRUE; rebuild in this frame.
        centre = -r.T @ t
        plan_centre = np.linalg.solve(tsp.R_TRUE * tsp.S_TRUE, centre - tsp.T_TRUE)
        r_plan = r @ tsp.R_TRUE
        r_world = r_plan @ world.T
        c_world = to_arkit(plan_centre[None])[0]
        cams.append((r_world, -r_world @ c_world))
    tsp._write_images_bin(paths.work / "undistorted" / "sparse" / "images.bin", cams)
    (paths.input / "roomplan").mkdir()
    (paths.input / "roomplan" / "scan.json").write_text(json.dumps(tsp._captured_room()))
    return paths


def test_arkit_capture_is_registered_rigidly(tmp_path):
    paths = _metric_project(tmp_path)
    report = align_scene(paths, "p", metric=True)
    assert report.roomplan["scale"] == 1.0 and report.roomplan["scale_fixed"]
    assert report.roomplan["median_error_mm"] < 10
    assert {"per_wall", "wall_coverage", "rms_error_mm", "p90_error_mm"} <= set(report.roomplan)
    assert report.roomplan["free_scale_probe"]["scale"] == pytest.approx(1.0, abs=0.01)


def test_arkit_capture_of_the_wrong_size_is_refused(tmp_path):
    paths = _metric_project(tmp_path, size=1 / 1.12)
    with pytest.raises(AlignmentFailed, match="disagree on size|does not fit"):
        align_scene(paths, "p", metric=True)
    assert not (aligned_dir(paths) / "scene.ply").exists()


def test_bounded_refinement_stays_within_its_tolerance(tmp_path):
    paths = _metric_project(tmp_path, size=1.006)
    report = align_scene(paths, "p", metric=True, scale_tolerance=0.01)
    assert report.roomplan["scale_bounds"] == [0.99, 1.01]
    assert report.roomplan["scale"] == pytest.approx(1 / 1.006, abs=0.002)


# --------------------------------------------------------------------------
# D. Gravity and the floor
# --------------------------------------------------------------------------


def test_arkit_gravity_is_kept_when_a_tilted_surface_outvotes_the_floor(tmp_path):
    """
    A big tilted surface below the cameras — a sloped bed, a ramp — has more
    points than the floor. The old fit (up to 35°) levelled the room to it.
    """
    rng = np.random.default_rng(7)
    xz = rng.uniform([1.0, 0.5], [3.4, 2.4], (60_000, 2))
    slope = np.column_stack([xz[:, 0], 0.3 + 0.25 * (xz[:, 0] - 1.0), xz[:, 1]])  # ~6° ramp
    paths = _metric_project(tmp_path, extra=slope)
    report = align_scene(paths, "p", metric=True)
    assert report.up_from == "ARKit gravity (kept)"
    t = np.array(Manifest.load(aligned_dir(paths) / "manifest.json").transform)
    up = t[:3, :3] @ [0.0, 1.0, 0.0]
    np.testing.assert_allclose(up / np.linalg.norm(up), [0, 1, 0], atol=1e-9)
    assert report.floor["from"] == "splats"
    assert abs(report.floor["tilt_deg"]) < 1.0


def _layer(rng, lo, hi, y, n):
    xz = rng.uniform(lo, hi, (n, 2))
    return np.column_stack([xz[:, 0], np.full(n, y) + rng.normal(0, 0.003, n), xz[:, 1]])


def test_a_small_dense_patch_under_the_floor_is_rejected_by_area():
    rng = np.random.default_rng(8)
    floor = _layer(rng, [0, 0], [4, 3], 0.0, 30_000)
    blob = _layer(rng, [1, 1], [1.5, 1.4], -0.3, 30_000)  # 0.2 m², denser than the floor
    walls = rng.uniform([0, 0, 0], [4, 2.5, 3], (20_000, 3))
    cams = rng.uniform(1.1, 1.6, 50)
    est = find_floor(np.vstack([floor, blob, walls]), cams, threshold=0.02, cell=0.1,
                     keep_gravity=True, min_area=1.0, clearance_range=(0.4, 2.2))
    assert est.height == pytest.approx(0.0, abs=0.005)
    assert est.rejected and "area" in est.rejected[0]["reason"]


def test_a_layer_with_cameras_under_it_is_not_the_floor():
    rng = np.random.default_rng(9)
    table = _layer(rng, [0, 0], [3, 3], 0.75, 40_000)  # the only broad layer below
    cams = np.r_[rng.uniform(0.3, 0.6, 30), rng.uniform(1.0, 1.6, 30)]
    with pytest.raises(FloorRejected, match="cameras would be under it"):
        find_floor(table, cams, threshold=0.02, cell=0.1, keep_gravity=True, min_area=1.0,
                   clearance_range=(0.4, 2.2))


def test_a_video_floor_may_not_tilt_the_scene_25_degrees():
    rng = np.random.default_rng(10)
    xz = rng.uniform([0, 0], [4, 3], (40_000, 2))
    plane = np.column_stack([xz[:, 0], np.tan(np.radians(25)) * xz[:, 0] - 2.0, xz[:, 1]])
    with pytest.raises(FloorRejected):
        find_floor(plane, rng.uniform(0.5, 1.0, 40), threshold=0.01, cell=0.1,
                   keep_gravity=False, max_tilt_deg=10.0)


def test_a_video_floor_within_a_few_degrees_refines_up():
    rng = np.random.default_rng(11)
    xz = rng.uniform([0, 0], [4, 3], (40_000, 2))
    plane = np.column_stack([xz[:, 0], np.tan(np.radians(4)) * xz[:, 0], xz[:, 1]])
    est = find_floor(plane, rng.uniform(1.0, 1.6, 40), threshold=0.01, cell=0.1,
                     keep_gravity=False, max_tilt_deg=10.0)
    assert est.tilt_deg == pytest.approx(4.0, abs=0.2)


def test_walls_lean_in_step_when_the_scene_is_not_level():
    room = tsp._plan_room()
    rng = np.random.default_rng(12)
    a, b, walls = solid_segments_by_wall(room)
    pts = []
    for i in range(len(a)):
        n = int(np.linalg.norm(b[i] - a[i]) * 3000)
        xz = a[i] + (b[i] - a[i]) * rng.uniform(0, 1, (n, 1))
        pts.append(np.column_stack([xz[:, 0], rng.uniform(0.05, tsp.H - 0.05, n), xz[:, 1]]))
    pts = np.vstack(pts)
    level = wall_lean(pts, (a, b), walls, tsp.H)
    assert max(abs(w["lean_deg"]) for w in level) < 0.3
    centre = pts.mean(axis=0)
    tilted = (pts - centre) @ Rotation.from_euler("z", 4, degrees=True).as_matrix().T + centre
    leans = [w["lean_deg"] for w in wall_lean(tilted, (a, b), walls, tsp.H)]
    assert max(abs(v) for v in leans) == pytest.approx(4.0, abs=0.6)
    assert min(leans) < -3 and max(leans) > 3  # opposite walls lean opposite ways


def test_the_published_scene_keeps_the_real_floor(tmp_path):
    """
    End to end, align then export: what the old room3 export got wrong. Its
    floor fit lay on a sloped surface, tilted the scene 7° and put the floor
    0.7 m too high, and export's crop (from 0.5 m under the floor) cut the
    real floor away. The published scene must hold the floor, level, under
    the whole room.
    """
    from walkthrough_pipeline.scene import export_scene
    from walkthrough_pipeline.splat import read_spz

    rng = np.random.default_rng(13)
    xz = rng.uniform([1.0, 0.5], [3.4, 2.4], (80_000, 2))
    slope = np.column_stack([xz[:, 0], 0.5 + 0.12 * (xz[:, 0] - 1.0), xz[:, 1]])
    paths = _metric_project(tmp_path, extra=slope)
    align_scene(paths, "p", metric=True)
    export_scene(paths)
    manifest = Manifest.load(paths.manifest)
    floor = read_spz(paths.scene / manifest.assets.floors["room"])
    scene = read_spz(paths.scene / "scene.spz")
    assert np.abs(floor.positions[:, 1]).max() < 0.04
    poly = np.array(manifest.rooms[0].floorPolygon)
    cells = {tuple(c) for c in np.floor(floor.positions[:, [0, 2]] / 0.25).astype(int)}
    lo, hi = poly.min(axis=0), poly.max(axis=0)
    grid = np.stack(np.meshgrid(np.arange(lo[0] + 0.125, hi[0], 0.25),
                                np.arange(lo[1] + 0.125, hi[1], 0.25)), -1).reshape(-1, 2)
    from walkthrough_pipeline.scene import points_in_polygon

    inside = grid[points_in_polygon(grid, poly)]
    covered = np.mean([tuple(c) in cells for c in np.floor(inside / 0.25).astype(int)])
    assert covered > 0.9
    # Nothing of the room hangs below the floor it stands on.
    assert np.percentile(scene.positions[:, 1], 0.5) > -0.05


def test_a_rug_is_reported_and_a_thick_floor_is_not(tmp_path):
    from walkthrough_pipeline.scene import export_scene

    paths = _metric_project(tmp_path)
    align_scene(paths, "p", metric=True)
    assert not any("rug" in w for w in export_scene(paths).warnings)

    from walkthrough_pipeline.splat import read_ply

    rug_path = aligned_dir(paths) / "scene.ply"
    cloud = read_ply(rug_path)
    rng = np.random.default_rng(14)
    n = 30_000
    rug = SplatCloud(
        np.column_stack([rng.uniform(1.2, 2.6, n), rng.normal(0.018, 0.002, n),
                         rng.uniform(0.8, 2.0, n)]).astype(np.float32),
        np.full((n, 3), 0.3, np.float32), np.full(n, 0.9, np.float32),
        np.full((n, 3), 0.004, np.float32), np.tile([1.0, 0, 0, 0], (n, 1)).astype(np.float32),
        sh_rest=np.zeros((n, 3, 3), np.float32) if cloud.sh_rest is not None else None,
    )
    merged = SplatCloud(*(np.concatenate([getattr(cloud, f), getattr(rug, f)])
                          for f in ("positions", "colours", "opacities", "scales", "rotations")),
                        sh_rest=None if cloud.sh_rest is None else np.concatenate(
                            [cloud.sh_rest, rug.sh_rest]))
    write_ply(merged, rug_path)
    assert any("rug" in w for w in export_scene(paths).warnings)
