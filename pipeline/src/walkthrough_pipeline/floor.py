"""
Floor detection and lighting transfer (M3 `pipeline floor`).

The semantic-segmentation step needs a model whose licence we have cleared,
so it sits behind an interface (`FloorSegmenter`). Everything else — the
projection voting, the plane refinement, the concave hull and the shading
map — is pure numpy and fully tested.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import cv2
import numpy as np

from .align import Plane, fit_plane_ransac


class FloorSegmenter(Protocol):
    """
    Returns a boolean floor mask per frame, same height and width as the image.

    Implementations are added in M3 once a licence-cleared model is approved
    (see docs/LICENSES.md). Keeping it a Protocol means the voting logic below
    is testable with a stub.
    """

    def segment(self, image: np.ndarray) -> np.ndarray: ...


@dataclass
class CameraPose:
    """World-to-camera extrinsics plus pinhole intrinsics."""

    rotation: np.ndarray  # (3, 3)
    translation: np.ndarray  # (3,)
    fx: float
    fy: float
    cx: float
    cy: float
    width: int
    height: int

    def project(self, points_world: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """
        Project world points into pixels.

        Returns (pixels (N,2) float, valid mask (N,) bool). Points behind the
        camera or outside the image are marked invalid.
        """
        pts = np.asarray(points_world, dtype=np.float64)
        cam = (self.rotation @ pts.T).T + self.translation
        z = cam[:, 2]
        valid = z > 1e-6
        # Avoid divide-by-zero; invalid entries are masked out anyway.
        safe_z = np.where(valid, z, 1.0)
        u = self.fx * cam[:, 0] / safe_z + self.cx
        v = self.fy * cam[:, 1] / safe_z + self.cy
        inside = (u >= 0) & (u < self.width) & (v >= 0) & (v < self.height)
        return np.stack([u, v], axis=1), valid & inside


def vote_floor(
    centres: np.ndarray,
    poses: list[CameraPose],
    masks: list[np.ndarray],
    min_votes: int = 3,
    min_ratio: float = 0.6,
) -> np.ndarray:
    """
    Project every Gaussian centre into every frame and vote floor / not floor.

    A Gaussian is floor when it was seen at least `min_votes` times and at
    least `min_ratio` of those observations landed on a floor pixel.
    Requiring a minimum number of observations suppresses the noise from
    Gaussians visible in only one or two frames.
    """
    if len(poses) != len(masks):
        raise ValueError("poses and masks must be the same length")
    centres = np.asarray(centres, dtype=np.float64)
    n = len(centres)
    seen = np.zeros(n, dtype=np.int32)
    floor_hits = np.zeros(n, dtype=np.int32)

    for pose, mask in zip(poses, masks, strict=True):
        pixels, valid = pose.project(centres)
        if not valid.any():
            continue
        idx = np.nonzero(valid)[0]
        u = pixels[idx, 0].astype(np.int32)
        v = pixels[idx, 1].astype(np.int32)
        u = np.clip(u, 0, mask.shape[1] - 1)
        v = np.clip(v, 0, mask.shape[0] - 1)
        seen[idx] += 1
        floor_hits[idx] += mask[v, u].astype(np.int32)

    with np.errstate(invalid="ignore", divide="ignore"):
        ratio = np.where(seen > 0, floor_hits / np.maximum(seen, 1), 0.0)
    return (seen >= min_votes) & (ratio >= min_ratio)


def refine_floor(
    centres: np.ndarray,
    votes: np.ndarray,
    height_tolerance: float = 0.05,
    seed: int = 0,
) -> tuple[Plane, np.ndarray]:
    """
    Fit the floor plane to the voted points, then widen the selection to every
    Gaussian within `height_tolerance` of that plane.

    The widening matters: segmentation misses floor under furniture edges, but
    those Gaussians are geometrically unambiguous once the plane is known.
    """
    centres = np.asarray(centres, dtype=np.float64)
    voted = centres[votes]
    if len(voted) < 3:
        raise ValueError("not enough floor votes to fit a plane")

    fit = fit_plane_ransac(voted, threshold=0.02, seed=seed, up_hint=np.array([0.0, 1.0, 0.0]))
    normal = np.array(fit.plane[:3])
    distances = np.abs(centres @ normal + fit.plane[3])
    refined = distances < height_tolerance
    return fit.plane, refined


def concave_hull_2d(points: np.ndarray, alpha: float = 0.6) -> np.ndarray:
    """
    Room outline from floor points (used when no RoomPlan export is present).

    Alpha-shape-like approach built on the convex hull plus edge subdivision:
    a full Delaunay alpha shape is overkill for rectangular rooms and brittle
    on sparse point sets. Returns an (M, 2) polygon.
    """
    pts = np.asarray(points, dtype=np.float64)
    if len(pts) < 3:
        raise ValueError("need at least 3 points for a hull")

    hull_idx = cv2.convexHull(pts.astype(np.float32), returnPoints=False).ravel()
    hull = pts[np.sort(hull_idx).astype(np.intp)]

    if alpha >= 1.0 or len(hull) < 4:
        return hull

    # Pull each hull edge inward toward the nearest actual point when the edge
    # spans a long empty gap — this carves out concavities like an L-shape.
    out: list[np.ndarray] = []
    for i in range(len(hull)):
        a = hull[i]
        b = hull[(i + 1) % len(hull)]
        out.append(a)
        edge_len = float(np.linalg.norm(b - a))
        if edge_len < 1e-9:
            continue
        mid = (a + b) / 2
        d = np.linalg.norm(pts - mid, axis=1)
        nearest = pts[int(np.argmin(d))]
        if float(np.min(d)) > alpha * edge_len / 2:
            out.append(nearest)
    return np.array(out)


def simplify_polygon(poly: np.ndarray, tolerance: float = 0.05) -> np.ndarray:
    """Douglas-Peucker, so editor-facing polygons have few, meaningful points."""
    pts = np.asarray(poly, dtype=np.float32).reshape(-1, 1, 2)
    simplified = cv2.approxPolyDP(pts, tolerance, True)
    return simplified.reshape(-1, 2).astype(np.float64)


def render_floor_topdown(
    centres: np.ndarray,
    colours: np.ndarray,
    bounds: tuple[float, float, float, float],
    resolution: int = 512,
) -> np.ndarray:
    """
    Orthographic top-down render of the floor Gaussians.

    bounds is (min_x, min_z, max_x, max_z) in metres. Returns an 8-bit BGR
    image. Empty cells are filled from their neighbours so the shading map
    has no holes.
    """
    min_x, min_z, max_x, max_z = bounds
    if max_x <= min_x or max_z <= min_z:
        raise ValueError("invalid bounds")

    acc = np.zeros((resolution, resolution, 3), dtype=np.float64)
    count = np.zeros((resolution, resolution), dtype=np.int32)

    xs = ((centres[:, 0] - min_x) / (max_x - min_x) * (resolution - 1)).astype(int)
    zs = ((centres[:, 2] - min_z) / (max_z - min_z) * (resolution - 1)).astype(int)
    inside = (xs >= 0) & (xs < resolution) & (zs >= 0) & (zs < resolution)

    np.add.at(acc, (zs[inside], xs[inside]), colours[inside])
    np.add.at(count, (zs[inside], xs[inside]), 1)

    filled = count > 0
    out = np.zeros_like(acc)
    out[filled] = acc[filled] / count[filled][:, None]
    image = np.clip(out, 0, 255).astype(np.uint8)

    if not filled.all():
        # inpaint the gaps rather than leaving black pixels that would read as
        # shadows in the shading map.
        holes = (~filled).astype(np.uint8) * 255
        image = cv2.inpaint(image, holes, 3, cv2.INPAINT_TELEA)
    return image


def shading_map(
    floor_render: np.ndarray, blur_sigma: float = 24.0, clip: tuple[float, float] = (0.55, 1.6)
) -> np.ndarray:
    """
    Lighting-only map from a floor render (brief M3).

    Heavy blur removes the tile pattern but keeps shadows and light falloff.
    Normalised around 1.0 so the viewer can multiply a new albedo by it.
    Returned as float32 for testing; `encode_shading_png` writes the 8-bit form.
    """
    grey = (
        cv2.cvtColor(floor_render, cv2.COLOR_BGR2GRAY)
        if floor_render.ndim == 3
        else floor_render
    )
    lum = grey.astype(np.float32) / 255.0
    blurred = cv2.GaussianBlur(lum, (0, 0), blur_sigma)
    mean = float(blurred.mean())
    if mean < 1e-6:
        return np.ones_like(blurred)
    normalised = blurred / mean
    return np.clip(normalised, clip[0], clip[1]).astype(np.float32)


def encode_shading_png(shading: np.ndarray, scale: float = 2.0) -> np.ndarray:
    """
    Pack a shading map into 8-bit. 1.0 maps to mid-grey (128) so the viewer
    decodes with `value / 128 * scale/2`. Documented in docs/SPEC.md.
    """
    encoded = np.clip(shading / scale * 255.0, 0, 255)
    return encoded.astype(np.uint8)


def decode_shading_png(encoded: np.ndarray, scale: float = 2.0) -> np.ndarray:
    return encoded.astype(np.float32) / 255.0 * scale
