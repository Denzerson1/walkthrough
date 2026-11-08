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


class FloorRejected(ValueError):
    """No floor candidate passed the checks; the message lists why each failed."""


@dataclass
class FloorEstimate:
    """The floor in a levelled frame (Y up), and the evidence for it."""

    height: float
    #: The floor plane n.p + d = 0: unit normal, (0, 1, 0) when gravity is kept.
    normal: np.ndarray
    plane_d: float
    #: Angle between the plane fitted to the floor's own points and +Y: how
    #: far the floor disagrees with the up direction used. Measured, never
    #: applied when gravity is kept.
    tilt_deg: float
    inliers: int
    #: Floor area (occupied cells) in scene units², and its share of the area
    #: under the cameras that has any points at all.
    area: float
    coverage: float
    #: Camera heights above it: 5th percentile, median, 95th; share below it.
    clearance: dict
    #: Candidates turned down, lowest first, with the reason.
    rejected: list[dict]


def _clearance(height: float, camera_heights: np.ndarray | None) -> dict:
    if camera_heights is None or len(camera_heights) == 0:
        return {}
    c = camera_heights - height
    return {"p5": float(np.percentile(c, 5)), "median": float(np.median(c)),
            "p95": float(np.percentile(c, 95)), "below": float((c < 0).mean())}


def _occupied(xz: np.ndarray, cell: float) -> set[tuple[int, int]]:
    return set(map(tuple, np.floor(xz / cell).astype(np.int64)))


def find_floor(
    points: np.ndarray,
    camera_heights: np.ndarray | None,
    *,
    threshold: float,
    cell: float,
    keep_gravity: bool,
    min_area: float = 0.0,
    min_coverage: float = 0.15,
    clearance_range: tuple[float, float] | None = None,
    max_tilt_deg: float = 10.0,
) -> FloorEstimate:
    """
    The floor of a scene already levelled by the best up direction known.

    `keep_gravity` (ARKit): the up direction is measured and stays; only the
    floor's height is found — the lowest horizontal layer that is broad
    enough (area, coverage) and that the cameras stand above at a plausible
    height (`clearance_range` on the median, in scene units). A plane fit
    that may tilt the scene is not used: it once overrode ARKit's gravity by
    up to 35° to fit a bed.

    Otherwise (a video, up from how the phone was held): RANSAC planes
    within `max_tilt_deg` of up, each checked the same way, the best kept;
    its normal refines up.

    Raises FloorRejected when nothing passes.
    """
    pts = np.asarray(points, dtype=np.float64)
    ref = np.median(camera_heights) if camera_heights is not None and len(camera_heights) else \
        np.percentile(pts[:, 1], 35)
    below = pts[pts[:, 1] < ref]
    if len(below) < 100:
        raise FloorRejected("Fewer than 100 points below the cameras: the floor was not filmed.")
    if len(below) > 300_000:
        below = below[np.random.default_rng(0).choice(len(below), 300_000, replace=False)]
    region = len(_occupied(below[:, [0, 2]], cell))
    rejected: list[dict] = []

    def judge(inlier_pts: np.ndarray, height: float) -> str | None:
        area = len(_occupied(inlier_pts[:, [0, 2]], cell)) * cell * cell
        coverage = area / max(region * cell * cell, 1e-12)
        clear = _clearance(height, camera_heights)
        if area < min_area:
            return f"area {area:.2f} under the minimum {min_area:.2f}"
        if coverage < min_coverage:
            return f"covers {coverage:.0%} of the area under the cameras"
        if clear:
            if clear["below"] > 0.02:
                return f"{clear['below']:.0%} of the cameras would be under it"
            if clearance_range and not (clearance_range[0] <= clear["median"]
                                        <= clearance_range[1]):
                return (f"cameras would be {clear['median']:.2f} above it, outside "
                        f"{clearance_range[0]}-{clearance_range[1]}")
        return None

    def estimate(inlier_pts: np.ndarray, normal: np.ndarray, d: float,
                 height: float) -> FloorEstimate:
        fitted = np.array(_least_squares_plane(inlier_pts)[:3])
        tilt = float(np.degrees(np.arccos(min(1.0, abs(fitted[1])))))
        area = len(_occupied(inlier_pts[:, [0, 2]], cell)) * cell * cell
        return FloorEstimate(
            height=float(height), normal=normal, plane_d=float(d), tilt_deg=tilt,
            inliers=len(inlier_pts), area=area, coverage=area / max(region * cell * cell, 1e-12),
            clearance=_clearance(height, camera_heights), rejected=rejected,
        )

    if keep_gravity:
        y = below[:, 1]
        edges = np.arange(y.min(), y.max() + threshold, threshold)
        if len(edges) < 2:
            edges = np.array([y.min() - threshold, y.max() + threshold])
        counts, edges = np.histogram(y, bins=edges)
        strongest = counts.max()
        last = len(counts) - 1
        peaks = [i for i in range(len(counts))
                 if counts[i] >= max(100, 0.2 * strongest)
                 and counts[i] >= counts[max(i - 1, 0)] and counts[i] >= counts[min(i + 1, last)]]
        for i in peaks:  # lowest first
            centre = (edges[i] + edges[i + 1]) / 2
            near = below[np.abs(y - centre) < threshold]
            height = float(np.median(near[:, 1]))
            near = below[np.abs(y - height) < threshold]
            reason = judge(near, height)
            if reason is None:
                return estimate(near, np.array([0.0, 1.0, 0.0]), -height, height)
            rejected.append({"height": round(height, 4), "points": len(near), "reason": reason})
        raise FloorRejected(_why(rejected))

    remaining = below
    for attempt in range(4):
        if len(remaining) < 100:
            break
        fit = fit_plane_ransac(remaining, threshold=threshold, up_hint=np.array([0.0, 1.0, 0.0]),
                               max_tilt_deg=max_tilt_deg, seed=attempt)
        if fit.inliers.sum() < 3:
            break
        normal = np.array(fit.plane[:3])
        d = fit.plane[3]
        if normal[1] < 0:
            normal, d = -normal, -d
        inlier_pts = remaining[fit.inliers]
        # Height of the plane under the cameras' median position (x, z ~ centre).
        centre = inlier_pts.mean(axis=0)
        height = float(-(d + normal[0] * centre[0] + normal[2] * centre[2]) / normal[1])
        reason = judge(inlier_pts, height)
        if reason is None:
            return estimate(inlier_pts, normal, d, height)
        rejected.append({"height": round(height, 4), "points": int(fit.inliers.sum()),
                         "reason": reason})
        remaining = remaining[~fit.inliers]
    raise FloorRejected(_why(rejected))


def _why(rejected: list[dict]) -> str:
    if not rejected:
        return "No level layer below the cameras stood out at all."
    lines = "; ".join(f"at {r['height']:+.3f}: {r['reason']}" for r in rejected)
    return f"No floor candidate passed the checks ({lines})."


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
