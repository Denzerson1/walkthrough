"""Reading COLMAP sparse models: camera poses for gravity and scale estimates."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass
class Camera:
    name: str
    rotation: np.ndarray  # (3, 3) world -> camera
    translation: np.ndarray  # (3,)
    camera_id: int = 1  # its intrinsics in cameras.bin

    @property
    def centre(self) -> np.ndarray:
        return -self.rotation.T @ self.translation

    @property
    def up(self) -> np.ndarray:
        """World-space up of the image. COLMAP cameras look down +Z with +Y down."""
        return -self.rotation[1]


def quaternion_to_matrix(q: np.ndarray) -> np.ndarray:
    w, x, y, z = q / np.linalg.norm(q)
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
            [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
            [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
        ]
    )


def read_images_bin(path: Path) -> list[Camera]:
    """Registered images from COLMAP's binary `images.bin`."""
    data = Path(path).read_bytes()
    (count,) = struct.unpack_from("<Q", data, 0)
    off = 8
    cameras = []
    for _ in range(count):
        _, qw, qx, qy, qz, tx, ty, tz, camera_id = struct.unpack_from("<idddddddi", data, off)
        off += struct.calcsize("<idddddddi")
        end = data.index(b"\0", off)
        name = data[off:end].decode("utf-8")
        off = end + 1
        (n_points,) = struct.unpack_from("<Q", data, off)
        off += 8 + n_points * 24  # x, y (double) and point3D id (int64) each
        cameras.append(
            Camera(
                name=name,
                rotation=quaternion_to_matrix(np.array([qw, qx, qy, qz])),
                translation=np.array([tx, ty, tz]),
                camera_id=camera_id,
            )
        )
    return cameras


def write_images_bin(source: Path, out_dir: Path, cam_to_world: dict[str, np.ndarray]) -> None:
    """
    A copy of the model whose images.bin is `source`, with the camera-to-
    world poses of the images named in `cam_to_world` replaced (the rest,
    and every observation, unchanged). cameras.bin and points3D.bin are
    copied from beside `source`.
    """
    import shutil

    from .splat import matrix_to_quaternion

    data = Path(source).read_bytes()
    (count,) = struct.unpack_from("<Q", data, 0)
    off, chunks = 8, [data[:8]]
    head = struct.calcsize("<idddddddi")
    for _ in range(count):
        image_id, qw, qx, qy, qz, tx, ty, tz, camera_id = struct.unpack_from(
            "<idddddddi", data, off)
        end = data.index(b"\0", off + head)
        name = data[off + head:end].decode("utf-8")
        (n_points,) = struct.unpack_from("<Q", data, end + 1)
        tail = data[end + 1: end + 9 + n_points * 24]
        if name in cam_to_world:
            c2w = np.asarray(cam_to_world[name], dtype=np.float64)
            rotation = c2w[:3, :3].T
            qw, qx, qy, qz = matrix_to_quaternion(rotation)
            tx, ty, tz = -rotation @ c2w[:3, 3]
        chunks.append(struct.pack("<idddddddi", image_id, qw, qx, qy, qz, tx, ty, tz, camera_id))
        chunks.append(name.encode("utf-8") + b"\0" + tail)
        off = end + 9 + n_points * 24
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "images.bin").write_bytes(b"".join(chunks))
    for other in ("cameras.bin", "points3D.bin"):
        if (Path(source).parent / other).is_file():
            shutil.copy2(Path(source).parent / other, out_dir / other)


def read_points3d_bin(path: Path) -> np.ndarray:
    """(N, 3) positions from COLMAP's binary `points3D.bin`."""
    data = Path(path).read_bytes()
    (count,) = struct.unpack_from("<Q", data, 0)
    off, points = 8, np.empty((count, 3))
    record = struct.calcsize("<Q3d3Bd")
    for i in range(count):
        points[i] = struct.unpack_from("<3d", data, off + 8)
        (track,) = struct.unpack_from("<Q", data, off + record)
        off += record + 8 + 8 * track
    return points


#: COLMAP's id for the PINHOLE camera model (fx, fy, cx, cy).
PINHOLE = 1


@dataclass
class PosedImage:
    name: str
    rotation: np.ndarray  # (3, 3) world -> camera, OpenCV axes
    translation: np.ndarray  # (3,)
    intrinsics: tuple[float, float, float, float]  # fx, fy, cx, cy in pixels
    #: (K, 2) pixel positions of the 3D points this image observes, and
    #: (K,) the ids of those points — what tells a trainer which pixels a
    #: point's depth supervises.
    points2d: np.ndarray
    point_ids: np.ndarray


def write_model(
    out_dir: Path,
    width: int,
    height: int,
    images: list[PosedImage],
    points: np.ndarray,
    colours: np.ndarray,
) -> None:
    """
    Write a COLMAP binary model (cameras.bin, images.bin, points3D.bin) for
    poses that come from somewhere other than COLMAP. Each image gets its own
    PINHOLE camera, because phone intrinsics move with autofocus. Point ids
    are their row index + 1; each point's track is rebuilt from the images'
    observations so the two always agree.
    """
    from .splat import matrix_to_quaternion

    out_dir.mkdir(parents=True, exist_ok=True)
    cams = [struct.pack("<Q", len(images))]
    for image_id, im in enumerate(images, start=1):
        cams.append(struct.pack("<iiQQ4d", image_id, PINHOLE, width, height, *im.intrinsics))
    (out_dir / "cameras.bin").write_bytes(b"".join(cams))

    tracks: list[list[tuple[int, int]]] = [[] for _ in range(len(points))]
    chunks = [struct.pack("<Q", len(images))]
    for image_id, im in enumerate(images, start=1):
        q = matrix_to_quaternion(im.rotation)
        chunks.append(struct.pack("<i4d3di", image_id, *q, *im.translation, image_id))
        chunks.append(im.name.encode("utf-8") + b"\0")
        obs = np.zeros(len(im.point_ids), dtype=[("xy", "<f8", 2), ("id", "<i8")])
        obs["xy"], obs["id"] = im.points2d, im.point_ids
        chunks.append(struct.pack("<Q", len(obs)) + obs.tobytes())
        for index, point_id in enumerate(im.point_ids):
            tracks[point_id - 1].append((image_id, index))
    (out_dir / "images.bin").write_bytes(b"".join(chunks))

    rgb = np.clip(np.round(colours), 0, 255).astype(np.uint8)
    chunks = [struct.pack("<Q", len(points))]
    for i, (xyz, track) in enumerate(zip(points, tracks, strict=True)):
        chunks.append(struct.pack("<Q3d3BdQ", i + 1, *xyz, *rgb[i], 0.0, len(track)))
        chunks.append(np.asarray(track, dtype="<i4").tobytes())
    (out_dir / "points3D.bin").write_bytes(b"".join(chunks))


def find_model(sparse_dir: Path) -> Path | None:
    """The largest reconstruction under `sparse/` (COLMAP may write several)."""
    candidates = sorted(Path(sparse_dir).rglob("images.bin"), key=lambda p: -p.stat().st_size)
    return candidates[0].parent if candidates else None


def gravity_from_cameras(cameras: list[Camera]) -> np.ndarray:
    """
    Up direction from how the phone was held.

    People film without rolling the phone, so every image's right vector is
    level, whatever the pitch: up is the direction perpendicular to all of
    them, the smallest eigenvector of their scatter. Averaging the images'
    up vectors instead is biased by pitch — a capture that mostly looks down
    tilts it toward the subject. The up vectors only choose the sign.

    Gravity matters because a plane fit alone cannot tell a floor from a wall
    or a ceiling in a reconstruction with no fixed orientation.
    """
    rights = np.array([c.rotation[0] for c in cameras])
    _, vectors = np.linalg.eigh(rights.T @ rights)
    up = vectors[:, 0]
    if np.mean([c.up for c in cameras], axis=0) @ up < 0:
        up = -up
    return up / np.linalg.norm(up)
