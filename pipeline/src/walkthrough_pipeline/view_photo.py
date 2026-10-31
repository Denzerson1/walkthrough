"""
The view out of the windows, from the owner's own photos of it.

A generated neighbourhood reads as a render however it is textured; a
photo of the real one reads as real. Shown flat on a card, though, it
reads as a picture hung outside. So the photo is given depth and stood
where what it shows really is:

- Two photos out of two windows a known distance apart are a stereo pair.
  Features matched between them triangulate to metres.
- The photo is laid on the street front across: the nearest wall of
  those points, a vertical plane parallel to the flat's facade. Laid on a
  plane per wall (the street front, the blocks behind), it tore at every
  roof and chimney where two walls met as soon as you stepped away from
  where it was taken.
- The sky is cut away along the roofs and chimneys (photo_texture.sky_mask);
  the dome behind shows sky instead.
- The camera's tilt comes from the photo's verticals, its turn from the
  street front, which runs parallel to the flat's own facade.

From where the photo was taken the view is the photo, pixel for pixel;
walking about the room, it moves as the houses across do.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps
from scipy.spatial import Delaunay

from . import glb, photo_texture

FULL_FRAME_DIAGONAL_MM = 43.27
_FOCAL_35MM = 41989  # EXIF FocalLengthIn35mmFilm
_EXIF_IFD = 0x8769
_DATE_TAKEN = 36867  # EXIF DateTimeOriginal


def load(path: Path, side: int) -> tuple[np.ndarray, float]:
    """The photo upright as RGB, at most `side` pixels; and its focal length in pixels."""
    image = Image.open(path)
    f35 = image.getexif().get_ifd(_EXIF_IFD).get(_FOCAL_35MM) or 26  # a phone's main lens
    image = ImageOps.exif_transpose(image).convert("RGB")
    image.thumbnail((side, side), Image.LANCZOS)
    rgb = np.asarray(image)
    h, w = rgb.shape[:2]
    return rgb, f35 / FULL_FRAME_DIAGONAL_MM * math.hypot(w, h)


def vertical_direction(rgb: np.ndarray, f: float) -> np.ndarray:
    """
    World up as a unit vector in the camera's frame (x right, y down, z
    forward): where the photo's near-vertical lines meet, at the least
    squares, reweighted against stray lines.
    """
    grey = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    seg = cv2.createLineSegmentDetector().detect(grey)[0].reshape(-1, 4)
    length = np.hypot(seg[:, 2] - seg[:, 0], seg[:, 3] - seg[:, 1])
    angle = np.degrees(np.arctan2(seg[:, 3] - seg[:, 1], seg[:, 2] - seg[:, 0])) % 180
    keep = (length > 0.025 * max(rgb.shape)) & (np.abs(angle - 90) < 12)
    seg, weight = seg[keep], length[keep]
    if len(seg) < 4:
        raise ValueError("Too few vertical lines to level the photo")
    ones = np.ones(len(seg))
    lines = np.cross(np.column_stack([seg[:, :2], ones]), np.column_stack([seg[:, 2:], ones]))
    lines /= np.linalg.norm(lines[:, :2], axis=1)[:, None]
    for _ in range(4):
        vp = np.linalg.svd(lines * weight[:, None])[2][-1]
        point = vp / np.linalg.norm(vp)
        residual = np.abs(lines @ point) / max(np.linalg.norm(point[:2]), 1e-9)
        weight = length[keep] * (residual < max(3 * np.median(residual), 1e-6))
    h, w = rgb.shape[:2]
    ray = np.array([(vp[0] - w / 2 * vp[2]) / f, (vp[1] - h / 2 * vp[2]) / f, vp[2]])
    ray /= np.linalg.norm(ray)
    return ray if ray[1] < 0 else -ray  # up is towards the top of the image: -y


def stereo_points(a: np.ndarray, b: np.ndarray, f: float, baseline: float
                  ) -> tuple[np.ndarray, np.ndarray]:
    """
    Features of photo `a` with their 3D position in a's camera frame, in
    metres: matched with photo `b`, taken `baseline` metres away, the pose
    between them found from the matches (RANSAC on the essential matrix).
    """
    h, w = a.shape[:2]
    k = np.array([[f, 0, w / 2], [0, f, h / 2], [0, 0, 1]])
    sift = cv2.SIFT_create(nfeatures=60000, contrastThreshold=0.02)
    ka, da = sift.detectAndCompute(cv2.cvtColor(a, cv2.COLOR_RGB2GRAY), None)
    kb, db = sift.detectAndCompute(cv2.cvtColor(b, cv2.COLOR_RGB2GRAY), None)
    good = [m for m, n in cv2.BFMatcher().knnMatch(da, db, k=2) if m.distance < 0.8 * n.distance]
    pa = np.float64([ka[m.queryIdx].pt for m in good])
    pb = np.float64([kb[m.trainIdx].pt for m in good])
    e, inliers = cv2.findEssentialMat(pa, pb, k, cv2.RANSAC, 0.9999, 1.2)
    _, r, t, inliers = cv2.recoverPose(e, pa, pb, k, mask=inliers)
    keep = inliers.ravel() > 0
    x = cv2.triangulatePoints(k @ np.hstack([np.eye(3), np.zeros((3, 1))]),
                              k @ np.hstack([r, t]), pa[keep].T, pb[keep].T)
    x = (x[:3] / x[3]).T * baseline / np.linalg.norm(r.T @ t)
    near = (x[:, 2] > 2) & (x[:, 2] < 300)
    return pa[keep][near], x[near]


def facade_turn(rgb: np.ndarray, f: float, up: np.ndarray) -> float:
    """
    How the street front across runs, as the angle (radians) from the level
    right axis towards forward: from the photo's horizontal lines below the
    horizon (window rows, sills, gutters), each turned into a direction in
    space, and the most common of them, weighted by length.
    """
    h, w = rgb.shape[:2]
    seg = cv2.createLineSegmentDetector().detect(cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY))[0]
    seg = seg.reshape(-1, 4)
    length = np.hypot(seg[:, 2] - seg[:, 0], seg[:, 3] - seg[:, 1])
    angle = np.degrees(np.arctan2(seg[:, 3] - seg[:, 1], seg[:, 2] - seg[:, 0])) % 180
    keep = ((length > 0.02 * h) & ((angle < 15) | (angle > 165))
            & ((seg[:, 1] + seg[:, 3]) / 2 > 0.6 * h))
    seg, length = seg[keep], length[keep]
    if len(seg) < 5:
        return 0.0
    ones = np.ones(len(seg))
    r1 = np.column_stack([(seg[:, 0] - w / 2) / f, (seg[:, 1] - h / 2) / f, ones])
    r2 = np.column_stack([(seg[:, 2] - w / 2) / f, (seg[:, 3] - h / 2) / f, ones])
    # A line's direction lies in the plane through the camera and its image,
    # and is level: perpendicular to that plane's normal and to up.
    direction = np.cross(np.cross(r1, r2), up)
    level = direction @ level_frame(up).T
    turn = (np.degrees(np.arctan2(level[:, 2], level[:, 0])) + 90) % 180 - 90
    hist, edges = np.histogram(turn, bins=np.arange(-30.0, 30.5, 0.5), weights=length)
    hist = np.convolve(hist, [0.25, 0.5, 0.25], mode="same")
    return math.radians(float(edges[hist.argmax()]) + 0.25)


def wall_depths(distances: np.ndarray, least: float = 0.04) -> list[float]:
    """
    Where the features cluster in distance out from the camera: the walls,
    standing parallel to the street front. Peaks of the histogram (1 m bins,
    lightly smoothed) holding at least `least` of the points, nearest first.
    """
    bins = np.arange(0.0, max(float(distances.max()), 2.0) + 2.0, 1.0)
    hist = np.histogram(distances, bins=bins)[0].astype(float)
    smooth = np.convolve(hist, [0.25, 0.5, 0.25], mode="same")
    peaks = []
    for i in range(1, len(smooth) - 1):
        if smooth[i] >= smooth[i - 1] and smooth[i] > smooth[i + 1]:
            around = hist[max(0, i - 2) : i + 3]
            if around.sum() >= least * len(distances):
                centres = bins[max(0, i - 2) : i + 3] + 0.5
                peaks.append(float((around * centres).sum() / around.sum()))
    return peaks or [float(np.median(distances))]


@dataclass
class Camera:
    """Where a photo was taken, in scene space, and how its pixels map to rays."""

    centre: np.ndarray  # scene metres
    right: np.ndarray  # scene unit vectors of the image's x, -y and viewing axes
    up: np.ndarray
    forward: np.ndarray
    f: float
    size: tuple[int, int]  # (w, h) of the image f is for

    def ray(self, u: np.ndarray, v: np.ndarray) -> np.ndarray:
        w, h = self.size
        d = (np.outer((u - w / 2) / self.f, self.right) - np.outer((v - h / 2) / self.f, self.up)
             + self.forward)
        return d / np.linalg.norm(d, axis=1)[:, None]


def level_frame(up: np.ndarray) -> np.ndarray:
    """Rows: the level right, up and forward axes, in the camera's frame."""
    forward = np.array([0.0, 0.0, 1.0]) - up[2] * up
    forward /= np.linalg.norm(forward)
    return np.array([np.cross(forward, up), up, forward])


def despeckle(rgb: np.ndarray) -> np.ndarray:
    """
    Dust and drops on the window glass, small and round, painted out from
    around them. Up to 1/90 of the width across: at 1/200 and a contrast
    of 16, and still at 1/140, the larger and fainter ones showed as light
    specks on the dark roofs.
    """
    side = max(3, rgb.shape[1] // 90)
    grey = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.int16)
    diff = np.abs(grey - cv2.medianBlur(rgb, 2 * side + 1)[..., 1].astype(np.int16))
    count, labels, stats, _ = cv2.connectedComponentsWithStats((diff > 12).astype(np.uint8))
    small = (stats[:, cv2.CC_STAT_WIDTH] <= side) & (stats[:, cv2.CC_STAT_HEIGHT] <= side)
    small[0] = False
    mask = cv2.dilate(small[labels].astype(np.uint8), np.ones((3, 3), np.uint8))
    return cv2.inpaint(rgb, mask, 3, cv2.INPAINT_TELEA)


def keep_mask(shape: tuple[int, int], polygon: list[list[float]] | None) -> np.ndarray:
    """The part of the photo to use: inside `polygon` (fractions), else all of it."""
    h, w = shape
    if not polygon:
        return np.ones((h, w), bool)
    mask = np.zeros((h, w), np.uint8)
    cv2.fillPoly(mask, [np.int32([[x * w, y * h] for x, y in polygon])], 1)
    return mask > 0


def in_front(depth: np.ndarray, near: float) -> np.ndarray:
    """
    Distances drawn in so the photo stands in front of the generated houses
    on the same footprints (whose heights are only floor counts): 70% of
    the way to the street front, beyond it a third as fast. From where the
    photo was taken nothing changes; walking about, far still moves less
    than near.
    """
    return np.where(depth <= near, 0.7 * depth, 0.7 * near + 0.3 * (depth - near))


def photo_mesh(camera: Camera, plane: tuple[np.ndarray, float], solid: np.ndarray,
               step: int = 24) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    The photo as a mesh: points on a grid and along the outline of `solid`
    (what is not sky), triangulated, each moved out along its ray onto
    `plane` (normal, offset; then drawn `in_front`). Near the outline the
    grid is four times finer: a triangle is kept or dropped whole, and at
    the coarse grid's size that cut the roofs and the photo's edges into teeth.
    Returns positions, uvs (v down) and triangles.
    """
    h, w = solid.shape

    def grid_points(spacing: int, where: np.ndarray) -> np.ndarray:
        gy, gx = np.mgrid[0 : h + spacing : spacing, 0 : w + spacing : spacing]
        grid = np.column_stack([np.minimum(gx.ravel(), w - 1), np.minimum(gy.ravel(), h - 1)])
        return grid[where[grid[:, 1], grid[:, 0]]]

    def grown(mask: np.ndarray, by: int) -> np.ndarray:
        return cv2.dilate(mask.astype(np.uint8), np.ones((2 * by + 1, 2 * by + 1), np.uint8)) > 0

    edge = grown(solid, 1) & grown(~solid, 1)  # either side of the outline
    contours, _ = cv2.findContours(solid.astype(np.uint8), cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
    outline = [cv2.approxPolyDP(c, 0.8, True).reshape(-1, 2) for c in contours if len(c) > 8]
    points = np.unique(np.vstack([grid_points(step, grown(solid, step)),
                                  grid_points(step // 4, grown(edge, step // 2)),
                                  *outline]).astype(np.float64), axis=0)
    tri = Delaunay(points).simplices
    centre = points[tri].mean(axis=1)
    cx, cy = centre[:, 0].astype(int).clip(0, w - 1), centre[:, 1].astype(int).clip(0, h - 1)
    tri = tri[solid[cy, cx]]
    used = np.unique(tri)
    remap = np.full(len(points), -1)
    remap[used] = np.arange(len(used))
    points, tri = points[used], remap[tri]
    rays = camera.ray(points[:, 0], points[:, 1])
    n, c = plane
    depth = (c - camera.centre @ n) / np.maximum(rays @ n, 1e-6)
    depth = np.clip(depth, 1, 400)
    positions = camera.centre + rays * in_front(depth, float(np.median(depth)))[:, None]
    uvs = points / [w, h]
    return positions, uvs, tri


@dataclass
class View:
    """A photo out of a window, stood in 3D."""

    mesh: glb.Mesh
    camera: Camera
    texture: np.ndarray  # the photo as the mesh shows it, uint8 RGB
    solid: np.ndarray  # where the mesh shows it: buildings, inside `keep`
    sky: np.ndarray  # where it shows sky, inside `keep`
    info: dict  # what was measured, for the record


def taken(path: Path) -> tuple[str, str] | None:
    """When a photo was taken, as ("YYYY-MM-DD", "HH:MM"), from its EXIF."""
    stamp = Image.open(path).getexif().get_ifd(_EXIF_IFD).get(_DATE_TAKEN)
    if not stamp:
        return None
    day, clock = str(stamp).split(" ")
    return day.replace(":", "-"), clock[:5]


def build(photo: Path, partner: Path, centre: np.ndarray, partner_centre: np.ndarray,
          outward: np.ndarray, keep: list[list[float]] | None = None, side: int = 2048) -> View:
    """
    The view out of a window from `photo`, taken there (scene `centre`), and
    `partner`, taken out of another window (`partner_centre`). `outward` is
    the scene direction the flat's facade faces, which the street front
    across runs parallel to; `keep` (fractions) cuts away the window's own
    frame where the shot shows it.
    """
    rgb, f = load(photo, 2400)
    partner_rgb, _ = load(partner, 2400)
    baseline = float(np.linalg.norm(np.asarray(partner_centre) - np.asarray(centre)))
    _, points = stereo_points(rgb, partner_rgb, f, baseline)
    up = vertical_direction(rgb, f)
    frame = level_frame(up)
    level = points @ frame.T  # right, up, forward
    turn = facade_turn(rgb, f, up)
    n = np.array([-math.sin(turn), math.cos(turn)])  # the street front's normal, (right, forward)
    distance = level[:, [0, 2]] @ n
    walls = wall_depths(distance[distance > 1.0])
    # Scene (x, z) of a level (right, forward) vector: along the normal is
    # the facade's outward direction; along t (the normal turned to the
    # right) is the scene's right of it, looking out.
    outward = np.asarray(outward, float)
    out_h = np.array([outward[0], outward[2]]) / math.hypot(outward[0], outward[2])
    right_h = np.array([-out_h[1], out_h[0]])  # looking out along (0, 1), right is -x
    t = np.array([n[1], -n[0]])

    def to_scene(v: np.ndarray) -> np.ndarray:  # level (right, up, forward) -> scene
        horizontal = (np.array([v[0], v[2]]) @ t) * right_h + (np.array([v[0], v[2]]) @ n) * out_h
        return np.array([horizontal[0], v[1], horizontal[1]])

    camera_axes = [to_scene(frame[:, k]) for k in range(3)]  # camera x, y (down), z in scene
    centre = np.asarray(centre, float)
    normal = np.array([out_h[0], 0.0, out_h[1]])
    street = (normal, float(normal @ centre + walls[0]))

    # Without the dust on the glass: the sky panorama is made from it too.
    texture = despeckle(load(photo, side)[0])
    h, w = texture.shape[:2]
    scale = w / rgb.shape[1]
    camera = Camera(centre, camera_axes[0], -camera_axes[1], camera_axes[2], f * scale, (w, h))
    sky = photo_texture.sky_mask(texture.astype(np.float32) / 255)
    kept = keep_mask((h, w), keep)
    solid, sky = ~sky & kept, sky & kept
    positions, uvs, tri = photo_mesh(camera, street, solid)
    jpeg = io.BytesIO()
    Image.fromarray(texture).save(jpeg, "JPEG", quality=90)
    tilt = math.degrees(math.asin(float(np.clip(camera.forward[1], -1, 1))))
    return View((positions, uvs, tri, jpeg.getvalue()), camera, texture, solid, sky, {
        "points": int(len(points)), "walls": [round(d, 1) for d in walls],
        "pitch": round(tilt, 1), "turn": round(math.degrees(turn), 1),
        "vertices": int(len(positions)), "triangles": int(len(tri))})
