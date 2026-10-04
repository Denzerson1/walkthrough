"""
Stray Scanner import, on a synthetic capture: a box room in ARKit's frame,
cameras with known poses, and LiDAR depth rendered from the geometry. If the
pose convention, the depth scaling or the COLMAP model were wrong, depth
points would miss the walls or land on the wrong pixels.
"""

from __future__ import annotations

import json
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest
from scipy.spatial.transform import Rotation
from walkthrough_pipeline import stray
from walkthrough_pipeline.colmap import read_images_bin
from walkthrough_pipeline.frames import extract_numbered, sharpest_per_window
from walkthrough_pipeline.paths import ProjectPaths
from walkthrough_pipeline.scene import align_scene
from walkthrough_pipeline.splat import SplatCloud, write_ply

sys.path.insert(0, str(Path(__file__).parent))
import test_scene_pipeline as tsp  # noqa: E402

# The room, in ARKit's world: the session starts with the phone 1.4 m up.
LO = np.array([-2.0, -1.4, -1.5])
HI = np.array([2.0, 1.1, 1.5])
VIDEO = (1920, 1440)
DEPTH = (256, 192)
K = (1450.0, 1450.0, 960.0, 720.0)  # fx, fy, cx, cy at video resolution


def _pose(position, yaw_deg, pitch_deg=-10.0):
    """Camera-to-world with OpenCV axes: x right, y down, z forward."""
    yaw, pitch = np.radians(yaw_deg), np.radians(pitch_deg)
    forward = np.array([np.sin(yaw) * np.cos(pitch), np.sin(pitch), -np.cos(yaw) * np.cos(pitch)])
    right = np.cross(forward, [0.0, 1.0, 0.0])
    right /= np.linalg.norm(right)
    down = np.cross(forward, right)
    pose = np.eye(4)
    pose[:3, :3] = np.column_stack([right, down, forward])
    pose[:3, 3] = position
    return pose


def _render_depth(pose):
    """Z-depth of the box room seen from `pose`, at LiDAR resolution."""
    w, h = DEPTH
    s = w / VIDEO[0]
    fx, fy, cx, cy = (v * s for v in K)
    v, u = np.mgrid[0:h, 0:w] + 0.5
    rays_cam = np.stack([(u - cx) / fx, (v - cy) / fy, np.ones_like(u)], axis=-1)
    rays = rays_cam @ pose[:3, :3].T
    origin = pose[:3, 3]
    with np.errstate(divide="ignore", invalid="ignore"):
        t = np.where(rays > 0, (HI - origin) / rays, (LO - origin) / rays)
    t = np.where(np.isfinite(t) & (t > 0), t, np.inf).min(axis=-1)
    return t  # rays have unit z, so the ray parameter is the z-depth


def _write_capture(root: Path, n: int = 40, columns: int = 13) -> list[np.ndarray]:
    (root / "depth").mkdir(parents=True)
    (root / "confidence").mkdir()
    (root / "rgb.mp4").write_bytes(b"")  # presence only; size is passed in
    poses, rows = [], []
    for i in range(n):
        pose = _pose([0.3 * np.cos(i / 6), 0.0, 0.3 * np.sin(i / 6)], yaw_deg=i * 9)
        poses.append(pose)
        depth_mm = np.clip(np.round(_render_depth(pose) * 1000), 0, 65535).astype(np.uint16)
        cv2.imwrite(str(root / "depth" / f"{i:06d}.png"), depth_mm)
        cv2.imwrite(str(root / "confidence" / f"{i:06d}.png"), np.full(DEPTH[::-1], 2, np.uint8))
        q = Rotation.from_matrix(pose[:3, :3]).as_quat()  # x, y, z, w
        rows.append([i / 60, i, *pose[:3, 3], *q, *K][:columns])
    header = "timestamp, frame, x, y, z, qx, qy, qz, qw, fx, fy, cx, cy"
    np.savetxt(root / "odometry.csv", rows, delimiter=",", header=header, comments="")
    np.savetxt(root / "camera_matrix.csv", [[K[0], 0, K[2]], [0, K[1], K[3]], [0, 0, 1]],
               delimiter=",")
    return poses


@pytest.fixture
def capture(tmp_path):
    poses = _write_capture(tmp_path / "cap")
    return stray.load(tmp_path / "cap", size=VIDEO), poses


def _distance_to_box(points):
    """Distance from each point to the nearest face of the room box."""
    return np.minimum(np.abs(points - LO), np.abs(points - HI)).min(axis=1)


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------


def test_poses_are_read_as_camera_to_world(capture):
    cap, poses = capture
    assert len(cap) == 40 and cap.size == VIDEO
    np.testing.assert_allclose(cap.cam_to_world, poses, atol=1e-6)
    np.testing.assert_allclose(cap.intrinsics[0], K)


def test_older_files_take_intrinsics_from_camera_matrix(tmp_path):
    _write_capture(tmp_path / "old", n=3, columns=9)
    cap = stray.load(tmp_path / "old", size=VIDEO)
    np.testing.assert_allclose(cap.intrinsics, np.tile(K, (3, 1)))


def test_find_capture_looks_inside_a_folder(tmp_path):
    _write_capture(tmp_path / "input" / "stray" / "8a3f1c2e", n=2)
    assert stray.find_capture(tmp_path / "input" / "stray").name == "8a3f1c2e"
    assert stray.find_capture(tmp_path / "nothing") is None


def test_depth_points_land_on_the_walls(capture):
    cap, _ = capture
    for index in (0, 13, 27):
        world, pixels = stray.depth_points(cap, index)
        assert len(world) > 5000
        assert np.percentile(_distance_to_box(world), 99) < 0.01  # mm rounding only
        assert pixels.min() >= 0 and pixels[:, 0].max() <= VIDEO[0]


def test_low_confidence_and_out_of_range_depth_is_dropped(capture, tmp_path):
    cap, _ = capture
    conf = np.full(DEPTH[::-1], 2, np.uint8)
    conf[:, : DEPTH[0] // 2] = 1
    cv2.imwrite(str(cap.confidence_path(0)), conf)
    world, pixels = stray.depth_points(cap, 0)
    assert pixels[:, 0].min() > VIDEO[0] / 2 - 10


# --------------------------------------------------------------------------
# Fusion and the trainer's dataset
# --------------------------------------------------------------------------


def test_fusion_keeps_one_point_per_voxel_and_bounded_tracks():
    rng = np.random.default_rng(0)
    base = rng.uniform(0, 1, (500, 3))
    world = [base + rng.normal(0, 0.001, base.shape) for _ in range(10)]  # same surface, 10x
    pixels = [rng.uniform(0, 100, (500, 2)) for _ in range(10)]
    colours = [np.full((500, 3), 200.0) for _ in range(10)]
    fused = stray.fuse(world, pixels, colours, voxel=0.02, max_track=6)
    # 5000 sightings of 500 surface points collapse to about 500; a point
    # within a millimetre of a voxel edge may straddle two.
    assert 500 <= len(fused.points) < 800
    counts = np.bincount(fused.obs_point)
    assert counts.max() <= 6
    np.testing.assert_allclose(fused.colours, 200.0)


def _read_cameras(path):
    data = path.read_bytes()
    (n,) = struct.unpack_from("<Q", data, 0)
    cams, off = {}, 8
    for _ in range(n):
        cid, model, w, h = struct.unpack_from("<iiQQ", data, off)
        off += 24
        cams[cid] = (model, w, h, struct.unpack_from("<4d", data, off))
        off += 32
    return cams


def _read_points(path):
    data = path.read_bytes()
    (n,) = struct.unpack_from("<Q", data, 0)
    points, off = {}, 8
    for _ in range(n):
        pid, x, y, z, _, _, _, _, length = struct.unpack_from("<Q3d3BdQ", data, off)
        off += struct.calcsize("<Q3d3BdQ")
        track = np.frombuffer(data, "<i4", 2 * length, off).reshape(-1, 2)
        off += 8 * length
        points[pid] = (np.array([x, y, z]), track)
    return points


def _read_observations(path):
    """image_id -> (K, 2) xy and (K,) point ids, from images.bin."""
    data = path.read_bytes()
    (n,) = struct.unpack_from("<Q", data, 0)
    out, off = {}, 8
    for _ in range(n):
        (image_id,) = struct.unpack_from("<i", data, off)
        off += struct.calcsize("<idddddddi")
        off = data.index(b"\0", off) + 1
        (k,) = struct.unpack_from("<Q", data, off)
        off += 8
        obs = np.frombuffer(data, [("xy", "<f8", 2), ("id", "<i8")], k, off)
        off += 24 * k
        out[image_id] = obs
    return out


def _frames(tmp_path, cap, width=640):
    out = tmp_path / "frames"
    out.mkdir()
    rng = np.random.default_rng(1)
    for f in cap.frames[::2]:  # the frames stage keeps a subset
        image = rng.integers(0, 255, (width * 3 // 4, width, 3), dtype=np.uint8)
        cv2.imwrite(str(out / f"frame_{f:06d}.png"), image)
    return sorted(out.glob("*.png"))


def test_dataset_poses_match_arkit(capture, tmp_path):
    cap, poses = capture
    report = stray.build_dataset(cap, _frames(tmp_path, cap), tmp_path / "ds")
    assert report.frames == 20 and (report.width, report.height) == (640, 480)
    cams = read_images_bin(tmp_path / "ds" / "sparse" / "images.bin")
    np.testing.assert_allclose([c.centre for c in cams], [p[:3, 3] for p in poses[::2]],
                               atol=1e-9)
    assert sorted(p.name for p in (tmp_path / "ds" / "images").iterdir()) == [
        c.name for c in cams
    ]


def test_every_depth_observation_projects_back_to_its_pixel(capture, tmp_path):
    """What the trainer's depth loss relies on: point, pose, intrinsics and pixel agree."""
    cap, _ = capture
    stray.build_dataset(cap, _frames(tmp_path, cap), tmp_path / "ds")
    sparse = tmp_path / "ds" / "sparse"
    cams = _read_cameras(sparse / "cameras.bin")
    images = read_images_bin(sparse / "images.bin")
    obs = _read_observations(sparse / "images.bin")
    points = _read_points(sparse / "points3D.bin")

    model, w, h, (fx, fy, cx, cy) = cams[1]
    assert (model, w, h) == (1, 640, 480)
    np.testing.assert_allclose((fx, fy, cx, cy), np.array(K) * 640 / 1920)

    errors = []
    for image_id, im in enumerate(images, start=1):
        o = obs[image_id]
        xyz = np.array([points[int(pid)][0] for pid in o["id"]])
        cam = xyz @ im.rotation.T + im.translation
        uv = np.column_stack([fx * cam[:, 0] / cam[:, 2] + cx, fy * cam[:, 1] / cam[:, 2] + cy])
        errors.append(np.linalg.norm(uv - o["xy"], axis=1))
    # Voxel averaging moves a point by up to half a 2 cm voxel.
    assert np.median(np.concatenate(errors)) < 2.0

    # Tracks and observations describe the same sightings.
    for pid, (_, track) in list(points.items())[:200]:
        for image_id, index in track:
            assert obs[int(image_id)]["id"][index] == pid


# --------------------------------------------------------------------------
# Frames and alignment
# --------------------------------------------------------------------------


def test_sharpest_frame_is_kept_from_each_window(tmp_path):
    scores = {tmp_path / f"frame_{i:06d}.png": s for i, s in enumerate([1, 9, 2, 3, 3, 8, 5])}
    kept = sharpest_per_window(scores, 3)
    assert [p.name for p in kept] == ["frame_000001.png", "frame_000005.png", "frame_000006.png"]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed here")
def test_extracted_frames_keep_their_video_frame_numbers(tmp_path):
    clip = tmp_path / "rgb.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc=size=320x240:rate=30", "-t", "1", str(clip)], check=True)
    frames = extract_numbered(clip, tmp_path / "out", step=7, long_edge=160)
    assert [p.name for p in frames] == [f"frame_{n:06d}.png" for n in (0, 7, 14, 21, 28)]
    assert cv2.imread(str(frames[0])).shape[:2] == (120, 160)


def test_metric_alignment_keeps_arkit_scale(tmp_path, monkeypatch):
    """An ARKit capture is already in metres: align must not rescale it."""
    monkeypatch.setattr(tsp, "S_TRUE", 1.0)
    monkeypatch.setattr(tsp, "R_TRUE", Rotation.from_euler("y", 40, degrees=True).as_matrix())
    monkeypatch.setattr(tsp, "T_TRUE", np.array([0.5, -1.4, 2.0]))
    rng = np.random.default_rng(4)
    room = tsp._plan_room()
    pts, _ = tsp._plan_points(room, rng)
    n = len(pts)
    q = np.zeros((n, 4))
    q[:, 0] = 1
    paths = ProjectPaths(tmp_path / "p")
    paths.ensure()
    write_ply(SplatCloud(tsp._to_colmap(pts).astype(np.float32), np.full((n, 3), 0.6, np.float32),
                         np.full(n, 0.9, np.float32), np.full((n, 3), 0.004, np.float32),
                         q.astype(np.float32)), paths.work / "trained.ply")
    tsp._write_images_bin(paths.work / "undistorted" / "sparse" / "images.bin",
                          tsp._cameras(room, rng))
    (paths.input / "roomplan").mkdir()
    (paths.input / "roomplan" / "scan.json").write_text(json.dumps(tsp._captured_room()))

    report = align_scene(paths, "p", metric=True)
    assert report.up_from == "ARKit gravity"
    assert report.scale == pytest.approx(1.0, abs=0.005)
    assert report.roomplan["inlier_ratio"] > 0.5
