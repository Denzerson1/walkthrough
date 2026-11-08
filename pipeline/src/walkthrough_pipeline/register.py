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
    #: Whether scale was solved for, and the bounds it was held to.
    scale_fixed: bool = False
    scale_bounds: tuple[float, float] = (1.0, 1.0)
    rms_error_m: float = math.inf  # among inliers
    p90_error_m: float = math.inf  # among inliers
    #: Share of the plan's solid wall length with a wall point within 5 cm.
    coverage: float = 0.0
    #: Per plan wall (by wall index): sightings, residuals and coverage.
    walls: list[dict] | None = None

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


def _umeyama_2d(
    src: np.ndarray, dst: np.ndarray, bounds: tuple[float, float] | None = None
) -> tuple[float, np.ndarray, np.ndarray]:
    """
    Similarity dst ~ s R src + t, with R a proper rotation. With `bounds`,
    s is the best scale within them: the rotation does not depend on the
    scale and the cost is a parabola in s, so the bounded optimum is the
    free one clamped. Bounds (1, 1) make it a rigid fit.
    """
    mu_s, mu_d = src.mean(0), dst.mean(0)
    xs, xd = src - mu_s, dst - mu_d
    u, sig, vt = np.linalg.svd(xd.T @ xs / len(src))
    d = np.eye(2)
    if np.linalg.det(u) * np.linalg.det(vt) < 0:
        d[1, 1] = -1
    r = u @ d @ vt
    var = (xs**2).sum() / len(src)
    s = float(np.trace(np.diag(sig) @ d) / var) if var > 1e-12 else 1.0
    if bounds is not None:
        s = min(max(s, bounds[0]), bounds[1])
    return s, r, mu_d - s * r @ mu_s


def solid_segments(room: Room) -> Segments:
    """
    The room's walls with every door and window cut out.

    Fitting to solid wall only is what tells a rectangular room from itself
    turned 180°: the splat has no wall where the doorway is, so the wrong way
    round puts splat wall across a plan doorway and plan wall across a splat
    doorway, and both cost.
    """
    starts, ends, _ = solid_segments_by_wall(room)
    return starts, ends


def solid_segments_by_wall(room: Room) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """`solid_segments`, plus the index of the wall each piece belongs to."""
    starts, ends, walls = [], [], []
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
                walls.append(index)
            pos = max(pos, hi)
    if len(starts) < 3:
        raise ValueError("The plan has too little solid wall to register against")
    return np.array(starts), np.array(ends), np.array(walls)


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
    *,
    fixed_scale: bool = False,
    wall_of_segment: np.ndarray | None = None,
) -> PlanFit:
    """
    Fit splat wall points onto plan walls (`solid_segments`): coarse search
    over yaw and scale, then ICP to the nearest wall.

    `scale_guess` comes from the ceiling height (tight) or the camera height
    (loose); `scale_range` says how far to trust it, and holds throughout —
    the coarse search, every ICP step and the result. An unbounded ICP once
    turned a 0.98-1.02 search into 1.12 with a fine-looking fit, pulling the
    walls onto the wrong plan walls. `fixed_scale` keeps `scale_guess`
    exactly: a rigid fit, for a capture already in metres (ARKit).
    `wall_of_segment` (from `solid_segments_by_wall`) labels the per-wall
    diagnostics with wall indices.
    """
    if len(points_xz) < 50:
        raise ValueError("Too few wall points to register against the plan")
    if fixed_scale:
        bounds = (scale_guess, scale_guess)
    else:
        if not 0 < scale_range[0] <= 1 <= scale_range[1]:
            raise ValueError(f"scale_range {scale_range} must bracket 1")
        bounds = (scale_guess * scale_range[0], scale_guess * scale_range[1])
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

    scales = [bounds[0]] if fixed_scale else np.geomspace(bounds[0], bounds[1], 7)
    coarse_costs = []  # (cost, yaw, scale)
    for s in scales:
        for yaw in np.arange(0.0, 360.0, 2.0):
            coarse_costs.append((cost(float(s), float(yaw)), float(yaw), float(s)))
    coarse_costs.sort()
    best_cost, yaw, s = coarse_costs[0]
    symmetry = best_cost / max(cost(s, yaw + 180.0), 1e-12)

    def icp(points: np.ndarray, s: float, r: np.ndarray, t: np.ndarray, steps: int = 25):
        # A shrinking gate: wide enough at first to pull the walls in, tight
        # at the end so furniture and the view through a window stop voting.
        # Each step solves the whole transform from the original points, so
        # the scale bound holds for the result, not per step.
        for gate in np.geomspace(0.4, 0.06, steps):
            q = s * points @ r.T + t
            d, feet = distance_to_segments(q, segments)
            keep = d < gate
            if keep.sum() < 30:
                break
            s, r, t = _umeyama_2d(points[keep], feet[keep], bounds)
        return s, r, t

    def score(s: float, r: np.ndarray, t: np.ndarray) -> float:
        # Splat on the walls, and walls with splat on them: both count.
        q = s * coarse @ r.T + t
        d, _ = distance_to_segments(q, segments)
        hit, _ = cKDTree(q).query(plan_samples, distance_upper_bound=INLIER_M)
        return float((d < INLIER_M).mean() + np.isfinite(hit).mean())

    # Start from the few best coarse yaws, each from its centred guess and
    # from guesses shifted around it: the centres line up only when the
    # capture saw the room evenly, and one wall hidden behind a wardrobe
    # moves the splat's centre by half a room.
    starts, picked = [], []
    for _cost, yw, sc in coarse_costs:
        if all(abs((yw - p + 180) % 360 - 180) >= 15 for p in picked):
            picked.append(yw)
            starts.append((sc, yw))
        if len(starts) == 8:
            break
    shifts = (-1.0, -0.5, 0.0, 0.5, 1.0)  # plan metres
    offsets = [np.array([dx, dz]) for dx in shifts for dz in shifts]
    candidates = []
    for sc, yw in starts:
        r0 = _rot2(yw)
        for off in offsets:
            fit_s, fit_r, fit_t = icp(coarse, sc, r0, plan_centre - sc * r0 @ splat_centre + off,
                                      steps=15)
            candidates.append((score(fit_s, fit_r, fit_t), fit_s, fit_r, fit_t))
    _, s, r, t = max(candidates, key=lambda c: c[0])
    s, r, t = icp(points_xz, s, r, t)

    if not bounds[0] - 1e-9 <= s <= bounds[1] + 1e-9 or np.linalg.det(r) <= 0:
        raise AssertionError(f"Registration left its bounds: scale {s}, det {np.linalg.det(r)}")
    q = s * points_xz @ r.T + t
    d, _ = distance_to_segments(q, segments)
    inliers = d < INLIER_M
    walls, coverage = _wall_diagnostics(q, segments, wall_of_segment)
    di = d[inliers]
    return PlanFit(
        scale=float(s),
        yaw_deg=math.degrees(math.atan2(r[1, 0], r[0, 0])),
        translation=t,
        inlier_ratio=float(inliers.mean()),
        median_error_m=float(np.median(di)) if inliers.any() else math.inf,
        symmetry_ratio=float(symmetry),
        scale_fixed=fixed_scale,
        scale_bounds=bounds,
        rms_error_m=float(np.sqrt(np.mean(di**2))) if inliers.any() else math.inf,
        p90_error_m=float(np.percentile(di, 90)) if inliers.any() else math.inf,
        coverage=coverage,
        walls=walls,
    )


#: A wall point within this of a plan wall is on it.
INLIER_M = 0.05


def wall_lean(
    points: np.ndarray, segments: Segments, wall_of_segment: np.ndarray, ceiling: float
) -> list[dict]:
    """
    How far each plan wall's points lean from vertical, in degrees: the
    median offset from the wall in an upper band against a lower one. Real
    walls stand within a fraction of a degree, so a scene levelled by the
    wrong up direction shows here as walls leaning in step — one side in,
    the other out. `points` are (N, 3) in the plan frame, floor at y = 0.
    """
    a, b = segments
    ab = b - a
    lengths = np.linalg.norm(ab, axis=1)
    normals = np.column_stack([-ab[:, 1], ab[:, 0]]) / np.maximum(lengths, 1e-12)[:, None]
    xz, y = points[:, [0, 2]], points[:, 1]
    keep = (y > 0.1 * ceiling) & (y < 0.95 * ceiling)
    xz, y = xz[keep], y[keep]
    ap = xz[:, None, :] - a[None]
    t = np.clip(np.einsum("nsk,sk->ns", ap, ab) / np.maximum(lengths**2, 1e-12), 0, 1)
    dist = np.linalg.norm(xz[:, None, :] - (a[None] + t[..., None] * ab[None]), axis=2)
    nearest = np.argmin(dist, axis=1)
    near = dist[np.arange(len(xz)), nearest] < 2 * INLIER_M
    signed = np.einsum("nk,nk->n", xz - a[nearest], normals[nearest])
    low_band, high_band = y < 0.4 * ceiling, y > 0.65 * ceiling

    out = []
    for wall in np.unique(wall_of_segment):
        mine = near & np.isin(nearest, np.nonzero(wall_of_segment == wall)[0])
        lo, hi = mine & low_band, mine & high_band
        if lo.sum() < 50 or hi.sum() < 50:
            continue
        rise = float(np.median(y[hi]) - np.median(y[lo]))
        run = float(np.median(signed[hi]) - np.median(signed[lo]))
        out.append({"wall": int(wall), "lean_deg": round(math.degrees(math.atan2(run, rise)), 2),
                    "points": int(mine.sum())})
    return out


def _wall_diagnostics(
    q: np.ndarray, segments: Segments, wall_of_segment: np.ndarray | None
) -> tuple[list[dict], float]:
    """
    Per plan wall: how many registered points lie on it, how far off they
    sit, and how much of its solid length has a point within INLIER_M. A
    wall the capture never saw fits nothing, and must not pass as fitted.
    Returns the walls and the share of all solid wall length covered.
    """
    a, b = segments
    labels = np.arange(len(a)) if wall_of_segment is None else np.asarray(wall_of_segment)
    tree = cKDTree(q)
    ab = b - a
    ap = q[:, None, :] - a[None]
    t = np.clip(np.einsum("nsk,sk->ns", ap, ab) / np.maximum((ab**2).sum(1), 1e-12), 0, 1)
    dist = np.linalg.norm(q[:, None, :] - (a[None] + t[..., None] * ab[None]), axis=2)
    nearest = np.argmin(dist, axis=1)
    d = dist[np.arange(len(q)), nearest]

    walls, covered_total, length_total = [], 0.0, 0.0
    for wall in np.unique(labels):
        segs = np.nonzero(labels == wall)[0]
        length = float(sum(np.linalg.norm(ab[k]) for k in segs))
        hit, _ = tree.query(_samples((a[segs], b[segs])), distance_upper_bound=INLIER_M)
        covered = float(np.isfinite(hit).mean())
        mine = np.isin(nearest, segs) & (d < INLIER_M)
        walls.append({
            "wall": int(wall),
            "length_m": round(length, 2),
            "points": int(mine.sum()),
            "median_mm": round(float(np.median(d[mine])) * 1000, 1) if mine.any() else None,
            "p90_mm": round(float(np.percentile(d[mine], 90)) * 1000, 1) if mine.any() else None,
            "coverage": round(covered, 3),
        })
        covered_total += covered * length
        length_total += length
    return walls, covered_total / max(length_total, 1e-9)
