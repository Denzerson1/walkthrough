"""
Stray Scanner captures (github.com/strayrobots/scanner, MIT): video with the
phone's own camera poses and LiDAR depth.

This replaces the two weakest steps of a plain video capture. Poses come
from ARKit's tracking instead of COLMAP guessing them from texture — which
fails on plain walls — and LiDAR depth both seeds the splat and supervises
it, so a white wall trains as a wall rather than fog.

Format (docs/format.md in the app's repo):

* `rgb.mp4` — the camera stream, in the sensor's landscape orientation.
* `odometry.csv` — per frame: timestamp, frame, x, y, z, qx, qy, qz, qw and,
  in newer versions, fx, fy, cx, cy. The pose is camera-to-world with
  OpenCV camera axes (the app turns ARKit's camera 180° about X before
  writing) in ARKit's world frame: metres, Y up along gravity.
* `camera_matrix.csv` — 3x3 intrinsics, for files without per-frame ones.
* `depth/000000.png` — 16-bit depth in millimetres, 256 x 192.
* `confidence/000000.png` — 0, 1 or 2 per depth pixel.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

#: Depth pixels used: highest ARKit confidence, and within LiDAR's range.
MIN_CONFIDENCE = 2
DEPTH_RANGE_M = (0.15, 5.0)
#: Fused points: one per voxel, each kept with at most this many sightings.
VOXEL_M = 0.02
MAX_TRACK = 6
#: A pose that moves further than this between consecutive frames (1/60 s)
#: is ARKit relocalising, not the phone moving; frames around it are dropped.
JUMP_M = 0.05
JUMP_DEG = 5.0


class StrayError(ValueError):
    pass


@dataclass
class StrayCapture:
    root: Path
    frames: np.ndarray  # (N,) frame numbers, matching the depth/ file names
    cam_to_world: np.ndarray  # (N, 4, 4), OpenCV camera axes
    intrinsics: np.ndarray  # (N, 4) fx, fy, cx, cy at the video's resolution
    size: tuple[int, int]  # video width, height

    def __len__(self) -> int:
        return len(self.frames)

    @property
    def video(self) -> Path:
        return self.root / "rgb.mp4"

    def depth_path(self, frame: int) -> Path:
        return self.root / "depth" / f"{frame:06d}.png"

    def confidence_path(self, frame: int) -> Path:
        return self.root / "confidence" / f"{frame:06d}.png"


def is_capture(path: Path) -> bool:
    return (path / "odometry.csv").is_file() and (path / "rgb.mp4").is_file()


def find_capture(directory: Path) -> Path | None:
    """The Stray dataset folder inside `directory` (or `directory` itself)."""
    if not directory.is_dir():
        return None
    if is_capture(directory):
        return directory
    found = (p.parent for p in sorted(directory.rglob("odometry.csv")) if is_capture(p.parent))
    return next(found, None)


def load(root: Path, size: tuple[int, int] | None = None) -> StrayCapture:
    """Read a dataset. `size` is the video's (width, height); probed if not given."""
    if not is_capture(root):
        raise StrayError(f"{root} is not a Stray Scanner dataset (needs rgb.mp4 and odometry.csv)")
    rows = np.loadtxt(root / "odometry.csv", delimiter=",", skiprows=1, ndmin=2,
                      usecols=range(9) if _columns(root) < 13 else range(13))
    if len(rows) == 0:
        raise StrayError("odometry.csv has no frames")

    from scipy.spatial.transform import Rotation

    poses = np.tile(np.eye(4), (len(rows), 1, 1))
    poses[:, :3, :3] = Rotation.from_quat(rows[:, 5:9]).as_matrix()  # x, y, z, w
    poses[:, :3, 3] = rows[:, 2:5]

    if rows.shape[1] >= 13:
        intrinsics = rows[:, 9:13]
    else:
        k = np.loadtxt(root / "camera_matrix.csv", delimiter=",")
        intrinsics = np.tile([k[0, 0], k[1, 1], k[0, 2], k[1, 2]], (len(rows), 1))
    if size is None:
        from .frames import probe

        stream = probe(root / "rgb.mp4")["streams"][0]
        size = (int(stream["width"]), int(stream["height"]))
    return StrayCapture(root, rows[:, 1].astype(int), poses, intrinsics.astype(np.float64), size)


def _columns(root: Path) -> int:
    """Fields per row, counted on the first data row: the header is not reliable."""
    with (root / "odometry.csv").open(encoding="utf-8") as fh:
        fh.readline()
        return len(fh.readline().split(","))


def depth_points(
    capture: StrayCapture, index: int, stride: int = 2
) -> tuple[np.ndarray, np.ndarray]:
    """
    World-space points from one frame's LiDAR depth, and the pixel each came
    from in video coordinates. Depth is z-depth along the camera axis, as the
    app's own reference tools read it.
    """
    frame = int(capture.frames[index])
    depth = cv2.imread(str(capture.depth_path(frame)), cv2.IMREAD_UNCHANGED)
    confidence = cv2.imread(str(capture.confidence_path(frame)), cv2.IMREAD_UNCHANGED)
    if depth is None or confidence is None:
        raise StrayError(f"Missing depth or confidence for frame {frame:06d}")

    h, w = depth.shape
    v, u = np.mgrid[0:h:stride, 0:w:stride]
    z = depth[v, u].astype(np.float64) / 1000.0
    keep = (confidence[v, u] >= MIN_CONFIDENCE) & (z > DEPTH_RANGE_M[0]) & (z < DEPTH_RANGE_M[1])
    u, v, z = u[keep] + 0.5, v[keep] + 0.5, z[keep]

    # Depth maps cover the same field of view as the video at lower
    # resolution, so the intrinsics scale with the image.
    fx, fy, cx, cy = capture.intrinsics[index]
    s = w / capture.size[0]
    x = (u - cx * s) / (fx * s) * z
    y = (v - cy * s) / (fy * s) * z
    cam = np.column_stack([x, y, z])
    pose = capture.cam_to_world[index]
    world = cam @ pose[:3, :3].T + pose[:3, 3]
    return world, np.column_stack([u, v]) / s


@dataclass
class FusedPoints:
    points: np.ndarray  # (P, 3) world
    colours: np.ndarray  # (P, 3) 0-255
    #: Observations: which point (row in `points`) was seen in which image at
    #: which pixel. At most MAX_TRACK per point.
    obs_point: np.ndarray  # (K,)
    obs_image: np.ndarray  # (K,)
    obs_xy: np.ndarray  # (K, 2)


def fuse(
    world: list[np.ndarray], pixels: list[np.ndarray], colours: list[np.ndarray],
    voxel: float = VOXEL_M, max_track: int = MAX_TRACK,
) -> FusedPoints:
    """
    One point per occupied voxel across all frames, at the mean of what fell
    in it, remembering up to `max_track` frames that saw it. The sightings
    are what the trainer's depth loss supervises; the voxel keeps a few
    hundred frames of LiDAR from becoming millions of duplicate points.
    """
    xyz = np.vstack(world)
    rgb = np.vstack(colours).astype(np.float64)
    xy = np.vstack(pixels)
    image = np.concatenate([np.full(len(w), i) for i, w in enumerate(world)])

    keys = np.floor(xyz / voxel).astype(np.int64)
    _, voxel_of, counts = np.unique(keys, axis=0, return_inverse=True, return_counts=True)
    voxel_of = voxel_of.ravel()
    n = len(counts)
    sums = np.zeros((n, 3))
    np.add.at(sums, voxel_of, xyz)
    rgb_sums = np.zeros((n, 3))
    np.add.at(rgb_sums, voxel_of, rgb)

    order = np.argsort(voxel_of, kind="stable")
    starts = np.r_[0, np.cumsum(counts)[:-1]]
    rank = np.arange(len(order)) - np.repeat(starts, counts)
    kept = order[rank < max_track]
    return FusedPoints(
        points=sums / counts[:, None],
        colours=rgb_sums / counts[:, None],
        obs_point=voxel_of[kept],
        obs_image=image[kept],
        obs_xy=xy[kept],
    )


def tracking_jumps(capture: StrayCapture, margin: int = 3) -> set[int]:
    """Rows within `margin` frames of a pose jump (see JUMP_M, JUMP_DEG)."""
    poses = capture.cam_to_world
    step = np.linalg.norm(np.diff(poses[:, :3, 3], axis=0), axis=1)
    relative = np.einsum("nji,njk->nik", poses[:-1, :3, :3], poses[1:, :3, :3])
    angle = np.degrees(np.arccos(np.clip((np.trace(relative, axis1=1, axis2=2) - 1) / 2, -1, 1)))
    rows: set[int] = set()
    for jump in np.nonzero((step > JUMP_M) | (angle > JUMP_DEG))[0]:
        rows.update(range(max(0, jump - margin + 1), min(len(poses), jump + margin + 1)))
    return rows


def edge_agreement(depth_mm: np.ndarray, gray: np.ndarray) -> float:
    """
    How well a depth map's edges line up with an image's edges: the
    correlation of their gradient magnitudes. Depth steps (a door frame, a
    shelf edge) are also visible edges, so the matching frame scores highest.
    """
    def edges(image: np.ndarray) -> np.ndarray:
        image = image.astype(np.float32)
        magnitude = np.hypot(cv2.Sobel(image, cv2.CV_32F, 1, 0), cv2.Sobel(image, cv2.CV_32F, 0, 1))
        magnitude = cv2.GaussianBlur(magnitude, (0, 0), 1.5)
        return (magnitude - magnitude.mean()) / max(float(magnitude.std()), 1e-6)

    depth = np.log(np.maximum(depth_mm.astype(np.float32), 100.0))
    return float((edges(depth) * edges(gray)).mean())


def _turning(capture: StrayCapture) -> np.ndarray:
    """Degrees turned between consecutive poses, smoothed over 15 frames."""
    poses = capture.cam_to_world
    relative = np.einsum("nji,njk->nik", poses[:-1, :3, :3], poses[1:, :3, :3])
    angle = np.degrees(np.arccos(np.clip((np.trace(relative, axis1=1, axis2=2) - 1) / 2, -1, 1)))
    return np.convolve(angle, np.ones(15) / 15, mode="same")


def frame_offset(capture: StrayCapture, samples: int = 8, search: int = 4) -> tuple[int, float]:
    """
    Video frame number minus pose row for the same instant, measured.

    The first real capture's video ran one frame behind its poses: depth row
    n matched video frame n - 1. One frame while turning is a degree, ~20 px
    at training size, and it smeared the whole scene. The depth map shares
    the pose's instant, so the video frame whose edges best match it says
    where the video stands. Measured where the phone turns fastest, where an
    offset shows most. Returns (offset, share of samples that agreed).
    """
    from .frames import ffmpeg_path

    turning = _turning(capture)
    picks: list[int] = []
    for row in np.argsort(-turning):
        if search < row < len(capture) - search and all(abs(row - q) > 200 for q in picks):
            picks.append(int(row))
        if len(picks) == samples:
            break

    h, w = cv2.imread(str(capture.depth_path(int(capture.frames[0]))), cv2.IMREAD_UNCHANGED).shape
    votes = []
    for row in picks:
        n = int(capture.frames[row])
        raw = subprocess.run(
            [ffmpeg_path(), "-v", "error", "-noautorotate", "-i", str(capture.video), "-vf",
             f"select=between(n\\,{n - search}\\,{n + search}),scale={w}:{h}", "-fps_mode", "vfr",
             "-f", "rawvideo", "-pix_fmt", "gray", "-"],
            check=True, capture_output=True,
        ).stdout
        window = np.frombuffer(raw, np.uint8).reshape(-1, h, w)
        if len(window) != 2 * search + 1:
            continue
        depth = cv2.imread(str(capture.depth_path(n)), cv2.IMREAD_UNCHANGED)
        scores = [edge_agreement(depth, frame) for frame in window]
        votes.append(int(np.argmax(scores)) - search)
    if not votes:
        return 0, 0.0
    values, counts = np.unique(votes, return_counts=True)
    best = int(values[np.argmax(counts)])
    return best, float(counts.max() / len(votes))


@dataclass
class DatasetReport:
    frames: int
    points: int
    observations: int
    width: int
    height: int


def build_dataset(
    capture: StrayCapture, frames: list[Path], out_dir: Path, offset: int = 0
) -> DatasetReport:
    """
    The trainer's input — `images/` and a COLMAP `sparse/` model — from
    ARKit's poses and LiDAR depth instead of structure from motion.

    `frames` are the chosen video frames, named frame_<number>.png, already
    scaled; the intrinsics are scaled to match.
    """
    import shutil

    from .colmap import PosedImage, write_model

    row_of = {int(f): i for i, f in enumerate(capture.frames)}
    unstable = tracking_jumps(capture)
    # A video frame shows the instant of pose row (frame number - offset).
    chosen = [(path, row_of[n]) for path in frames
              if (n := int(path.stem.split("_")[1]) - offset) in row_of
              and row_of[n] not in unstable]
    if len(chosen) < 20:
        raise StrayError(f"Only {len(chosen)} frames have a pose; the capture is too short.")

    first = cv2.imread(str(chosen[0][0]))
    height, width = first.shape[:2]
    s = width / capture.size[0]

    images_dir = out_dir / "images"
    shutil.rmtree(out_dir, ignore_errors=True)
    images_dir.mkdir(parents=True)

    world, pixels, colours = [], [], []
    for path, row in chosen:
        shutil.copy2(path, images_dir / path.name)
        pts, xy = depth_points(capture, row)
        xy = xy * s
        image = cv2.imread(str(path))
        ix = np.clip(xy[:, 0].astype(int), 0, width - 1)
        iy = np.clip(xy[:, 1].astype(int), 0, height - 1)
        world.append(pts)
        pixels.append(xy)
        colours.append(image[iy, ix, ::-1])  # BGR -> RGB
    fused = fuse(world, pixels, colours)

    posed = []
    for i, (path, row) in enumerate(chosen):
        mine = fused.obs_image == i
        pose = capture.cam_to_world[row]
        rotation = pose[:3, :3].T
        posed.append(
            PosedImage(
                name=path.name,
                rotation=rotation,
                translation=-rotation @ pose[:3, 3],
                intrinsics=tuple(capture.intrinsics[row] * s),
                points2d=fused.obs_xy[mine],
                point_ids=fused.obs_point[mine] + 1,
            )
        )
    write_model(out_dir / "sparse", width, height, posed, fused.points, fused.colours)
    return DatasetReport(len(chosen), len(fused.points), len(fused.obs_point), width, height)
