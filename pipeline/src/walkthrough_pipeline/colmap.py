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
        _, qw, qx, qy, qz, tx, ty, tz, _ = struct.unpack_from("<idddddddi", data, off)
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
            )
        )
    return cameras


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
