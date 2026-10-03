"""
Registering a gravity-aligned splat to a RoomPlan scan.

After `align` puts the floor at y = 0, the splat still has an unknown yaw,
an unknown position on the floor and — because COLMAP has no notion of
metres — an unknown scale. RoomPlan's walls are metric. Fitting the splat's
wall points onto those walls fixes all three at once, which is what makes a
LiDAR scan worth taking: the scene comes out in real metres with the plan
lying exactly on it.

All numpy, tested on synthetic rooms with a known transform.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.spatial import cKDTree

from .manifest import Room

Segments = tuple[np.ndarray, np.ndarray]  # (S, 2) starts, (S, 2) ends


@dataclass
class PlanFit:
    scale: float
    yaw_deg: float  # rotation of the splat's (x, z) plane, see `matrix`
    translation: np.ndarray  # (2,) in plan metres
    inlier_ratio: float  # share of wall points within 5 cm of a plan wall
    median_error_m: float  # among those inliers
    #: Coarse cost of the best yaw over the cost turned 180°. Near 1 means a
    #: symmetric room whose doors and windows did not settle which way round.
    symmetry_ratio: float = 0.0

    def apply(self, points_xz: np.ndarray) -> np.ndarray:
        return self.scale * points_xz @ _rot2(self.yaw_deg).T + self.translation

    def matrix(self) -> np.ndarray:
        """The 4x4 similarity in scene space: rotation about Y, uniform scale."""
        c, s = math.cos(math.radians(self.yaw_deg)), math.sin(math.radians(self.yaw_deg))
        m = np.eye(4)
        # (x, z) -> (c x - s z, s x + c z), with y left alone: det = +1, so
        # never a mirror image.
        m[0, 0], m[0, 2], m[2, 0], m[2, 2] = c, -s, s, c
        m[:3, :3] *= self.scale
        m[0, 3], m[2, 3] = self.translation
        return m


def _rot2(deg: float) -> np.ndarray:
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return np.array([[c, -s], [s, c]])


def ceiling_height(heights: np.ndarray) -> float | None:
    """
    The ceiling's height above the floor, from the heights of opaque splats.

    The ceiling is the densest horizontal layer in the top half of the scene.
    Returns None when there is no clear one — a room filmed without looking
    up — rather than guessing.
    """
    h = heights[heights > 0]
    if len(h) < 1000:
        return None
    top = np.percentile(h, 99.5)
    counts, edges = np.histogram(h, bins=200, range=(0.0, top))
    upper = counts[100:]
    peak = int(np.argmax(upper)) + 100
    if counts[peak] < 4 * np.median(counts[counts > 0]):
        return None
    return float((edges[peak] + edges[peak + 1]) / 2)


def solid(opacities: np.ndarray) -> np.ndarray:
    """
    The more opaque 40% of splats — the ones that are surface rather than
    haze. Relative, not a fixed threshold: gsplat's MCMC leaves most splats
    faint (median opacity ~0.035 on a real room), so a fixed 0.3 kept 4% of
    them and the floor fit landed on a tilted plane.
    """
    return opacities >= np.percentile(opacities, 60)


def wall_points(
    positions: np.ndarray, opacities: np.ndarray, ceiling: float, max_points: int = 20_000
) -> np.ndarray:
    """
    (x, z) of solid splats in the band between furniture and ceiling — where
    almost everything is wall, window or a tall cupboard against a wall.
    """
    y = positions[:, 1]
    band = solid(opacities) & (y > 0.45 * ceiling) & (y < 0.85 * ceiling)
    pts = positions[band][:, [0, 2]].astype(np.float64)
    if len(pts) > max_points:
        pts = pts[np.random.default_rng(0).choice(len(pts), max_points, replace=False)]
    return pts


def distance_to_segments(points: np.ndarray, segments: Segments) -> tuple[np.ndarray, np.ndarray]:
    """Per point: distance to the nearest segment, and the nearest point on it."""
    a, b = segments
    ab = b - a  # (S, 2)
    ap = points[:, None, :] - a[None, :, :]  # (N, S, 2)
    t = np.clip(np.einsum("nsk,sk->ns", ap, ab) / np.maximum((ab**2).sum(1), 1e-12), 0, 1)
    feet = a[None] + t[..., None] * ab[None]
    d = np.linalg.norm(points[:, None, :] - feet, axis=2)
    best = np.argmin(d, axis=1)
    rows = np.arange(len(points))
    return d[rows, best], feet[rows, best]


def _umeyama_2d(src: np.ndarray, dst: np.ndarray) -> tuple[float, np.ndarray, np.ndarray]:
    """Similarity dst ~ s R src + t, with R a proper rotation."""
    mu_s, mu_d = src.mean(0), dst.mean(0)
    xs, xd = src - mu_s, dst - mu_d
    u, sig, vt = np.linalg.svd(xd.T @ xs / len(src))
    d = np.eye(2)
    if np.linalg.det(u) * np.linalg.det(vt) < 0:
        d[1, 1] = -1
    r = u @ d @ vt
    var = (xs**2).sum() / len(src)
    s = float(np.trace(np.diag(sig) @ d) / var) if var > 1e-12 else 1.0
    return s, r, mu_d - s * r @ mu_s


def solid_segments(room: Room) -> Segments:
    """
    The room's walls with every door and window cut out.

    Fitting to solid wall only is what tells a rectangular room from itself
    turned 180°: the splat has no wall where the doorway is, so the wrong way
    round puts splat wall across a plan doorway and plan wall across a splat
    doorway, and both cost.
    """
    starts, ends = [], []
    for index, wall in enumerate(room.walls):
        a, b = np.array(wall.start, dtype=float), np.array(wall.end, dtype=float)
        length = float(np.linalg.norm(b - a))
        if length < 1e-6:
            continue
        cuts = sorted(
            (o.offset - o.width / 2, o.offset + o.width / 2)
            for o in room.openings
            if o.wallIndex == index
        )
        pos = 0.0
        for lo, hi in [*cuts, (length, length)]:
            if lo > pos + 0.05:
                starts.append(a + (b - a) * pos / length)
                ends.append(a + (b - a) * min(lo, length) / length)
            pos = max(pos, hi)
    if len(starts) < 3:
        raise ValueError("The plan has too little solid wall to register against")
    return np.array(starts), np.array(ends)


def _samples(segments: Segments, spacing: float = 0.05) -> np.ndarray:
    pts = []
    for a, b in zip(*segments, strict=True):
        n = max(2, int(np.linalg.norm(b - a) / spacing))
        pts.append(a + (b - a) * np.linspace(0, 1, n)[:, None])
    return np.vstack(pts)


def register_to_plan(
    points_xz: np.ndarray,
    segments: Segments,
    scale_guess: float,
    scale_range: tuple[float, float] = (0.85, 1.15),
) -> PlanFit:
    """
    Fit splat wall points onto plan walls (`solid_segments`): coarse search
    over yaw and scale, then ICP to the nearest wall. `scale_guess` comes
    from the ceiling height (tight) or the camera height (loose);
    `scale_range` says how far to trust it.
    """
    if len(points_xz) < 50:
        raise ValueError("Too few wall points to register against the plan")
    a, b = segments
    plan_pts = np.vstack([a, b])
    plan_centre = (plan_pts.min(0) + plan_pts.max(0)) / 2
    plan_samples = _samples(segments)

    rng = np.random.default_rng(0)
    coarse = points_xz[rng.choice(len(points_xz), min(3000, len(points_xz)), replace=False)]
    lo_p, hi_p = np.percentile(coarse, 10, axis=0), np.percentile(coarse, 90, axis=0)
    splat_centre = (lo_p + hi_p) / 2
    tree = cKDTree(coarse)

    def cost(s: float, yaw: float) -> float:
        r = _rot2(yaw)
        # Splat -> plan: wall points should lie on solid wall.
        q = s * (coarse - splat_centre) @ r.T + plan_centre
        forward, _ = distance_to_segments(q, segments)
        # Plan -> splat: solid wall should have splat behind it. Done in the
        # splat's frame so the tree is built once.
        back = (plan_samples - plan_centre) @ r / s + splat_centre
        reverse, _ = tree.query(back, distance_upper_bound=0.3 / s)
        reverse = np.minimum(np.nan_to_num(reverse * s, posinf=0.3), 0.3)
        return float(np.minimum(forward, 0.3).mean() + reverse.mean())

    best = (math.inf, 0.0, scale_guess)
    for s in scale_guess * np.geomspace(scale_range[0], scale_range[1], 7):
        for yaw in np.arange(0.0, 360.0, 2.0):
            c = cost(float(s), float(yaw))
            if c < best[0]:
                best = (c, float(yaw), float(s))

    best_cost, yaw, s = best
    symmetry = best_cost / max(cost(s, yaw + 180.0), 1e-12)
    r = _rot2(yaw)
    t = plan_centre - s * r @ splat_centre

    # ICP with a shrinking gate: wide enough at first to pull the walls in
    # from the coarse fit, tight at the end so furniture and the view through
    # a window stop voting.
    for gate in np.geomspace(0.4, 0.06, 25):
        q = s * points_xz @ r.T + t
        d, feet = distance_to_segments(q, segments)
        keep = d < gate
        if keep.sum() < 30:
            break
        ds, dr, dt = _umeyama_2d(q[keep], feet[keep])
        s, r, t = ds * s, dr @ r, ds * dr @ t + dt

    q = s * points_xz @ r.T + t
    d, _ = distance_to_segments(q, segments)
    inliers = d < 0.05
    return PlanFit(
        scale=float(s),
        yaw_deg=math.degrees(math.atan2(r[1, 0], r[0, 0])),
        translation=t,
        inlier_ratio=float(inliers.mean()),
        median_error_m=float(np.median(d[inliers])) if inliers.any() else math.inf,
        symmetry_ratio=float(symmetry),
    )
