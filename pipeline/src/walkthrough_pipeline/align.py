"""
Gravity alignment and metric scale (M1 `pipeline align`), plus the floor
plane fitting shared with M3 `pipeline floor`.

All pure numpy — unit-testable without a GPU or a real reconstruction.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

Plane = tuple[float, float, float, float]  # ax + by + cz + d = 0, (a,b,c) unit


@dataclass
class PlaneFit:
    plane: Plane
    inliers: np.ndarray  # boolean mask over the input points
    iterations: int

    @property
    def inlier_ratio(self) -> float:
        return float(self.inliers.mean()) if self.inliers.size else 0.0


def fit_plane_ransac(
    points: np.ndarray,
    threshold: float = 0.02,
    iterations: int = 400,
    seed: int = 0,
    up_hint: np.ndarray | None = None,
    max_tilt_deg: float = 35.0,
) -> PlaneFit:
    """
    RANSAC plane fit.

    `up_hint` rejects candidate planes whose normal is far from the expected
    up direction — without it, a large wall wins over the floor in rooms with
    sparse floor coverage.
    """
    pts = np.asarray(points, dtype=np.float64)
    if pts.ndim != 2 or pts.shape[1] != 3:
        raise ValueError("points must be (N, 3)")
    n = len(pts)
    if n < 3:
        raise ValueError("need at least 3 points to fit a plane")

    rng = np.random.default_rng(seed)
    best_mask = np.zeros(n, dtype=bool)
    best_plane: Plane = (0.0, 1.0, 0.0, 0.0)
    cos_limit = np.cos(np.radians(max_tilt_deg))

    for _ in range(iterations):
        idx = rng.choice(n, size=3, replace=False)
        p0, p1, p2 = pts[idx]
        normal = np.cross(p1 - p0, p2 - p0)
        norm = np.linalg.norm(normal)
        if norm < 1e-9:
            continue
        normal = normal / norm
        if up_hint is not None and abs(float(np.dot(normal, up_hint))) < cos_limit:
            continue
        d = -float(np.dot(normal, p0))
        distances = np.abs(pts @ normal + d)
        mask = distances < threshold
        if mask.sum() > best_mask.sum():
            best_mask = mask
            best_plane = (float(normal[0]), float(normal[1]), float(normal[2]), d)

    # Refine on the inliers with a least-squares fit; RANSAC's 3-point plane
    # is noisy even when the inlier set is right.
    if best_mask.sum() >= 3:
        best_plane = _least_squares_plane(pts[best_mask])
        normal = np.array(best_plane[:3])
        distances = np.abs(pts @ normal + best_plane[3])
        best_mask = distances < threshold

    return PlaneFit(best_plane, best_mask, iterations)


def _least_squares_plane(points: np.ndarray) -> Plane:
    centroid = points.mean(axis=0)
    centred = points - centroid
    # The smallest singular vector is the plane normal.
    _, _, vh = np.linalg.svd(centred, full_matrices=False)
    normal = vh[-1]
    normal = normal / np.linalg.norm(normal)
    d = -float(np.dot(normal, centroid))
    return (float(normal[0]), float(normal[1]), float(normal[2]), d)


def rotation_between(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Rotation matrix taking unit vector `a` onto unit vector `b`."""
    a = a / np.linalg.norm(a)
    b = b / np.linalg.norm(b)
    v = np.cross(a, b)
    c = float(np.dot(a, b))
    if np.linalg.norm(v) < 1e-12:
        # Parallel or antiparallel.
        if c > 0:
            return np.eye(3)
        # 180°: rotate about any axis perpendicular to a.
        axis = np.array([1.0, 0.0, 0.0])
        if abs(a[0]) > 0.9:
            axis = np.array([0.0, 1.0, 0.0])
        axis = np.cross(a, axis)
        axis = axis / np.linalg.norm(axis)
        return _axis_angle(axis, np.pi)
    s = np.linalg.norm(v)
    kmat = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + kmat + kmat @ kmat * ((1 - c) / (s**2))


def _axis_angle(axis: np.ndarray, angle: float) -> np.ndarray:
    axis = axis / np.linalg.norm(axis)
    k = np.array(
        [[0, -axis[2], axis[1]], [axis[2], 0, -axis[0]], [-axis[1], axis[0], 0]]
    )
    return np.eye(3) + np.sin(angle) * k + (1 - np.cos(angle)) * (k @ k)


def gravity_alignment(
    floor_plane: Plane, points: np.ndarray | None = None
) -> np.ndarray:
    """
    4x4 transform putting the floor at y=0 with Y up.

    The plane normal is flipped to point "up" (towards the bulk of the
    points) before aligning, so the scene never ends up upside down.
    """
    normal = np.array(floor_plane[:3], dtype=np.float64)
    d = float(floor_plane[3])
    norm = np.linalg.norm(normal)
    normal, d = normal / norm, d / norm

    if points is not None and len(points):
        # Most of the scene should be above the floor.
        signed = np.asarray(points, dtype=np.float64) @ normal + d
        if np.median(signed) < 0:
            normal, d = -normal, -d

    rot = rotation_between(normal, np.array([0.0, 1.0, 0.0]))
    transform = np.eye(4)
    transform[:3, :3] = rot
    # After rotation the plane sits at y = -d; translate it onto y = 0.
    transform[1, 3] = d
    return transform


def apply_transform(points: np.ndarray, transform: np.ndarray) -> np.ndarray:
    pts = np.asarray(points, dtype=np.float64)
    homo = np.hstack([pts, np.ones((len(pts), 1))])
    return (homo @ transform.T)[:, :3]


def metric_scale(measured_distance: float, reference_distance_m: float) -> float:
    """
    Scale factor turning reconstruction units into metres.

    `measured_distance` is the distance between the two reference points in
    the reconstruction; `reference_distance_m` is what the user measured in
    the real room (a door width, say).
    """
    if measured_distance <= 0:
        raise ValueError("measured distance must be positive")
    if reference_distance_m <= 0:
        raise ValueError("reference distance must be positive")
    return reference_distance_m / measured_distance


def scale_transform(scale: float) -> np.ndarray:
    t = np.eye(4)
    t[0, 0] = t[1, 1] = t[2, 2] = scale
    return t


def compose(*transforms: np.ndarray) -> np.ndarray:
    """Compose 4x4 transforms left-to-right (first applied first)."""
    out = np.eye(4)
    for t in transforms:
        out = t @ out
    return out
