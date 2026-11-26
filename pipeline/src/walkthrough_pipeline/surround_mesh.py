"""
A surround's photos stood in 3D (surround.py): each one a mesh at its
depth, in the flat's scene coordinates.

- Each photo's pixels become a grid of vertices pushed out along their rays
  to the photo's depth. The sky is cut away (the dome behind shows the sky
  of each time of day), by the photo's own outline of the roofs and trees
  against it (the texture's alpha). So is the flat's own balcony and
  house, which the photos show and the flat's model has.
- Each direction, seen from where most of the photos were taken, is given
  to one photo: the most central one that shows it. Nothing is blended, so
  nothing is doubled.
- A photo whose depth disagrees with the others' where they overlap (taken
  by turning on the spot, it had no second view to measure from) takes its
  depth from theirs.
- Lawn and paving are laid flat on the ground's levels; a quad across the
  edge of something near, or seen at a glance along it, is laid flat on
  what is behind. From where the photo was taken none of this shows: the
  view is the photo. Walking about, near moves against far as it should.
"""

from __future__ import annotations

import io
import warnings
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from . import surround_recon as recon
from .photo_texture import linear_to_srgb, srgb_to_linear

STEP = 4  # pixels between mesh vertices, on the stereo's photos
CELL = 0.2  # degrees per cell of the panorama the photos compete in
FAR = 700.0  # metres: nothing stands farther than this
NEAR = 2.0  # metres: nearer than this is the balcony's own
EDGE = 1.15  # a quad whose corners' distances differ by more spans two things
GLANCE = 0.16  # cosine to its photo's line of sight below which a surface is laid flat
GROW = 0.12  # metres the flat's own boxes are grown by
AGREE = 0.06  # a photo's depth agrees if it is within this of the others', at the median
SEAM = 2.5  # degrees the photos' scores are smoothed over before they compete
OVERLAP = 8  # vertices a photo's mesh runs on under its neighbours', past its own share
PATCH = 1500  # cells: a photo's share smaller than this goes to its neighbours
SPECK = 400  # vertices: a share's island smaller than this is left out
BEHIND = 1.012  # how much farther that rim stands, so the neighbour's own lies in front


@dataclass
class Layout:
    """What of the flat a surround's photos show, in scene metres, and how they are used."""

    #: Boxes (x0, x1, y0, y1, z0, z1): the building, the balcony's parts.
    own: list[tuple[float, ...]]
    lean: list[int]  # which of them (rails) a photo may be taken leaning over
    #: Boxes of air, where nothing of the surroundings stands: the balcony's
    #: own, and just outside its rails.
    air: list[tuple[float, ...]]
    sun: np.ndarray  # towards the sun when the photos were taken
    quality: dict[str, float]  # photo name: how gladly it is used where several see the same
    #: Photo name: the share of the sun's veil taken off it.
    veil: dict[str, float] = field(default_factory=dict)
    #: Photos whose cameras' middle the rest is seen from.
    centre: list[str] = field(default_factory=list)
    #: x below, z below, z above, y below: what stands there is laid flat.
    lay: tuple[float, float, float, float] | None = None
    #: Where the photos are seen from, if not from where they were taken (a
    #: window over the street they were taken in).
    seen_from: tuple[float, float, float] | None = None
    #: Pixels the depth is smoothed over, if any: photos seen from far off
    #: where they were taken (from a window over the street) stand as smooth
    #: fronts. Their depth's every step was a tear from up there.
    smooth: float = 0.0


def box_hits(start: np.ndarray, dirs: np.ndarray, boxes: list, lean: list[int]) -> np.ndarray:
    """
    Distance along each ray (`start`, unit `dirs`) to the nearest of `boxes`
    it enters; inf where none. A box the ray starts in is not counted, nor
    one of `lean` it starts within 0.7 m of: the photo was taken from there.
    """
    best = np.full(len(dirs), np.inf)
    with np.errstate(divide="ignore", invalid="ignore"):
        for k, (x0, x1, y0, y1, z0, z1) in enumerate(boxes):
            lo = np.array([x0, y0, z0]) - GROW
            hi = np.array([x1, y1, z1]) + GROW
            reach = 0.7 if k in lean else 0.0
            if np.all(start > lo - reach) and np.all(start < hi + reach):
                continue
            t0, t1 = (lo - start) / dirs, (hi - start) / dirs
            near = np.minimum(t0, t1).max(axis=1)
            far = np.maximum(t0, t1).min(axis=1)
            entered = (far >= np.maximum(near, 0)) & np.isfinite(near) & (near > 0)
            best = np.where(entered, np.minimum(best, near), best)
    return best


def inside_boxes(points: np.ndarray, boxes: list) -> np.ndarray:
    out = np.zeros(len(points), bool)
    for x0, x1, y0, y1, z0, z1 in boxes:
        out |= ((points[:, 0] > x0 - GROW) & (points[:, 0] < x1 + GROW)
                & (points[:, 1] > y0 - GROW) & (points[:, 1] < y1 + GROW)
                & (points[:, 2] > z0 - GROW) & (points[:, 2] < z1 + GROW))
    return out


def sky_mask(bgr: np.ndarray, mono: np.ndarray) -> np.ndarray:
    """
    Where a photo shows sky: sky's colour (blue, or bright and pale: a
    cloud) where the depth network sees nothing (nought for sky and cloud;
    a little more already for hills and pale blocks on the horizon), in
    stretches of at least a 250th of the photo (pale window frames are
    small; blue ones of a tenth of that), the specks the colour test misses
    inside them filled.
    """
    hsv = cv2.cvtColor(bgr.astype(np.float32) / 255, cv2.COLOR_BGR2HSV)
    hue, sat, val = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    blue = (hue > 185) & (hue < 250) & (val > 0.4)
    coloured = blue | ((sat < 0.16) & (val > 0.72))
    coloured = cv2.erode(coloured.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
    empty = mono < 0.004 * np.percentile(mono, 99)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(
        (coloured & empty).astype(np.uint8), connectivity=4)
    big = np.flatnonzero(stats[:, cv2.CC_STAT_AREA] >= 0.004 * mono.size)
    sky = np.isin(labels, big[big > 0])
    # A pocket of blue between a tree and the photo's edge is sky at a tenth of that size.
    count, labels, stats, _ = cv2.connectedComponentsWithStats(
        (coloured & blue & empty & ~sky).astype(np.uint8), connectivity=4)
    pockets = np.flatnonzero(stats[:, cv2.CC_STAT_AREA] >= 0.0004 * mono.size)
    sky |= np.isin(labels, pockets[pockets > 0])
    count, labels, stats, _ = cv2.connectedComponentsWithStats((~sky).astype(np.uint8),
                                                               connectivity=4)
    return sky | np.isin(labels, np.flatnonzero(stats[:, cv2.CC_STAT_AREA] < 40))


def guided(guide: np.ndarray, src: np.ndarray, radius: int, eps: float) -> np.ndarray:
    """`src` smoothed, but not across the edges of `guide` (He's guided filter)."""
    size = (2 * radius + 1, 2 * radius + 1)
    mean_g, mean_s = cv2.blur(guide, size), cv2.blur(src, size)
    a = (cv2.blur(guide * src, size) - mean_g * mean_s) / (
        cv2.blur(guide * guide, size) - mean_g * mean_g + eps)
    return cv2.blur(a, size) * guide + cv2.blur(mean_s - a * mean_g, size)


def sun_veil(bgr: np.ndarray, rays: np.ndarray, sun: np.ndarray) -> np.ndarray:
    """
    The sun's veil over a photo, 0..1 of full white per pixel. Towards the
    sun a lens lays a wash of light over the picture, and streaks across
    it: what is darkest in each small neighbourhood shows how much (haze
    removal's dark channel), kept to the picture's own edges by a guided
    filter, so a streak over a dark tree is found and the pale glasshouse
    beside it is not. Counted within 40 degrees of the sun. `rays`: each
    pixel's unit direction, `sun`: towards the sun, in the same frame.
    """
    off = np.degrees(np.arccos(np.clip(rays @ sun, -1, 1)))
    near = np.clip((40 - off) / 20, 0, 1).astype(np.float32)
    if near.max() <= 0:
        return np.zeros(bgr.shape[:2], np.float32)
    h, w = bgr.shape[:2]
    lin = srgb_to_linear(bgr.astype(np.float32) / 255)
    k = max(7, int(0.012 * max(h, w)) | 1)
    low = lin.min(axis=2)
    dark = cv2.erode(low, np.ones((k, k), np.uint8))
    return np.clip(guided(lin.mean(axis=2), dark, int(0.03 * max(h, w)), 1e-3), 0, low) * near


def unveil(bgr: np.ndarray, rays: np.ndarray, sun: np.ndarray, strength: float) -> np.ndarray:
    """A photo with `strength` of the sun's veil (sun_veil) taken off, the rest stretched back."""
    if strength <= 0:
        return bgr
    veil = sun_veil(bgr, rays, sun)
    if veil.max() <= 0:
        return bgr
    veil = np.minimum(veil * strength, 0.75)
    lin = srgb_to_linear(bgr.astype(np.float32) / 255)
    out = (lin - veil[..., None]) / (1 - veil[..., None])
    return (linear_to_srgb(np.clip(out, 0, 1)) * 255).astype(np.uint8)


def squeeze(dist: np.ndarray) -> np.ndarray:
    """Distances with the far ones drawn in to under FAR (the viewer's far plane)."""
    return np.where(dist > FAR / 2,
                    FAR / 2 + FAR / 2 * (1 - np.exp(-(dist - FAR / 2) / (FAR / 2))), dist)


def pano_cells(points: np.ndarray, centre: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Each point's column and row in the CELL-degree panorama seen from `centre` (column 0: -z)."""
    d = points - centre
    azimuth = np.degrees(np.arctan2(d[:, 0], -d[:, 2])) % 360
    elevation = np.degrees(np.arcsin(np.clip(d[:, 1] / np.linalg.norm(d, axis=1), -1, 1)))
    return ((azimuth / CELL).astype(int) % int(360 / CELL),
            np.clip(((90 - elevation) / CELL).astype(int), 0, int(180 / CELL) - 1))


class Photo:
    """One photo as vertices in the scene: where each stands, and which are the surroundings."""

    def __init__(self, view: recon.View, bgr: np.ndarray, mono: np.ndarray, depth: np.ndarray,
                 matrix: np.ndarray, origin: np.ndarray, layout: Layout):
        self.view, self.name = view, view.name
        self.quality = layout.quality.get(view.name, 0.0)
        self.to_camera = view.rotation @ np.linalg.inv(matrix)  # scene direction -> camera, scaled
        self.to_camera /= np.linalg.norm(self.to_camera[0])
        h, w = depth.shape
        self.size = (w, h)
        self.veil = layout.veil.get(view.name, 0.0)
        self.smooth = bool(layout.smooth)
        rays = view.rays()
        rays /= np.linalg.norm(rays, axis=-1, keepdims=True)
        self.taken = bgr
        self.bgr = unveil(bgr, rays, self.to_camera @ layout.sun, self.veil)
        self.centre = matrix @ view.centre + origin
        ys, xs = np.mgrid[0:h:STEP, 0:w:STEP]
        self.grid = ys.shape
        self.px = np.column_stack([xs.ravel() + 0.5, ys.ravel() + 0.5])
        z = cv2.medianBlur(depth.astype(np.float32), 5)
        if layout.smooth:
            there = (~sky_mask(bgr, mono) & (z > 0)).astype(np.float32)
            log = cv2.GaussianBlur(np.log(np.maximum(z, 1e-3)) * there, (0, 0), layout.smooth)
            z = np.exp(log / np.maximum(cv2.GaussianBlur(there, (0, 0), layout.smooth), 1e-4))
        z = z[ys, xs].ravel().astype(np.float64)
        points = ((view.rays(STEP).reshape(-1, 3) * z[:, None] - view.translation)
                  @ view.rotation) @ matrix.T + origin
        ray = points - self.centre
        self.dist = np.linalg.norm(ray, axis=1)
        self.dirs = ray / self.dist[:, None]
        # As taken: without its veil a sun's rim is no sky's colour.
        self.sky_full = sky_mask(bgr, mono)
        sky = self.sky_full[ys, xs].ravel() & (self.dirs[:, 1] > -0.02)
        # Its own: near, inside the building or the balcony, or on a wall of
        # theirs give or take the depth's error (0.6 m before it, or beyond).
        own = ((self.dist < NEAR) | inside_boxes(points, [*layout.own, *layout.air])
               | (box_hits(self.centre, self.dirs, layout.own, layout.lean) < self.dist + 0.6))
        own = cv2.dilate(own.reshape(self.grid).astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
        solid = ~sky.reshape(self.grid) & ~own
        # Against the sky the network's depth slides off a roof or a tree
        # over a few pixels: within three vertices of the sky a vertex takes
        # the nearest depth round it, the roof's own.
        grid = np.where(solid, self.dist.reshape(self.grid), np.inf).astype(np.float32)
        nearest = cv2.erode(grid, np.ones((5, 5), np.uint8)).ravel()
        by_sky = cv2.dilate(sky.reshape(self.grid).astype(np.uint8), np.ones((7, 7), np.uint8)) > 0
        self.dist = np.where((by_sky & solid).ravel() & np.isfinite(nearest), nearest, self.dist)
        # One vertex into the sky all round: the texture's alpha cuts the
        # outline there, not the mesh's coarse grid. They stand at the
        # distance of what they border.
        fringe = ((cv2.dilate(solid.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0) & ~solid
                  & sky.reshape(self.grid) & ~own)
        spread = cv2.dilate(np.where(solid, self.dist.reshape(self.grid), 0).astype(np.float32),
                            np.ones((3, 3), np.uint8))
        self.dist = np.where(fringe.ravel(), spread.ravel(), self.dist)
        self.fringe = fringe.ravel()
        self.keep = solid.ravel() | self.fringe
        self.colour = self.bgr[ys, xs].reshape(-1, 3)[:, ::-1].astype(np.float64) / 255  # RGB
        u, v = self.px[:, 0] / w, self.px[:, 1] / h
        self.central = 1 - 2 * np.maximum(np.abs(u - 0.5), np.abs(v - 0.5))
        self.gain = np.ones(3)
        self.flat = np.zeros(len(self.dist), bool)  # laid on the ground

    @property
    def points(self) -> np.ndarray:
        return self.centre + self.dirs * self.dist[:, None]

    def at(self, which: np.ndarray, dist: np.ndarray) -> np.ndarray:
        """Scene points at `dist` along the rays of vertices `which`, far ones drawn in."""
        return self.centre + self.dirs[which] * squeeze(dist)[:, None]

    def lay_on_ground(self, levels: list[float], lay: tuple | None) -> None:
        """
        Lawn and paving laid flat: a vertex within half a metre of a ground
        level is put on it. The network's depth leaves the ground rippled,
        which from where the photo was taken does not show, and from across
        the flat smears it. In the `lay` zone (seen from the side only: in
        front of rooms far from where the photos were taken) low growth is
        laid on the lowest level too: stood up at its depth it is a tangle
        from those rooms; laid down it is at least where it grows.
        """
        if not levels:
            return
        height = self.centre[1] + self.dirs[:, 1] * self.dist
        down = self.dirs[:, 1] < -0.03
        for level in levels:
            on = (self.centre[1] - level) / np.maximum(-self.dirs[:, 1], 1e-6)
            near = (down & (np.abs(height - level) < 0.5) & (on > 0.6 * self.dist)
                    & (on < 1.7 * self.dist))
            self.dist = np.where(near, on, self.dist)
            self.flat |= near
        if lay:
            points = self.points
            on = (self.centre[1] - min(levels)) / np.maximum(-self.dirs[:, 1], 1e-6)
            low = (down & ~self.flat & (points[:, 0] < lay[0]) & (points[:, 2] < lay[1])
                   & (points[:, 2] > lay[2]) & (points[:, 1] < lay[3]) & (on < 3.0 * self.dist))
            self.dist = np.where(low, on, self.dist)
            self.flat |= low


def ground_levels(photos: list[Photo]) -> list[float]:
    """The heights the low points pile up at: lawn, paving, a street lower down."""
    low = np.concatenate([(p.centre[1] + p.dirs[:, 1] * p.dist)[
        p.keep & ~p.fringe & (p.dirs[:, 1] < -0.05) & (p.dist < 60)] for p in photos])
    if len(low) < 1000:
        return []
    hist, edges = np.histogram(low, bins=np.arange(low.min() - 0.2, -0.8, 0.1))
    smooth = np.convolve(hist, [0.25, 0.5, 0.25], mode="same")
    peaks = [i for i in range(1, len(smooth) - 1)
             if smooth[i] >= smooth[i - 1] and smooth[i] > smooth[i + 1]
             and smooth[i] > 0.04 * smooth.sum()]
    return [float(np.median(low[(low > edges[i] - 0.15) & (low < edges[i + 1] + 0.15)]))
            for i in peaks]


def splat(photos: list[Photo], centre: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Per photo, in the panorama seen from `centre`: its score and distance where it shows any."""
    width, height = int(360 / CELL), int(180 / CELL)
    score = np.zeros((len(photos), height, width), np.float32)
    depth = np.zeros((len(photos), height, width), np.float32)
    kernel = np.ones((5, 5), np.uint8)
    for k, p in enumerate(photos):
        ax, ey = pano_cells(p.points, centre)
        shows = p.keep & ~p.fringe
        s = np.where(shows, np.maximum(p.central, 0.02) * p.quality, 0).astype(np.float32)
        np.maximum.at(score[k], (ey, ax), s)
        np.maximum.at(depth[k], (ey[shows], ax[shows]), p.dist[shows].astype(np.float32))
        score[k], depth[k] = cv2.dilate(score[k], kernel), cv2.dilate(depth[k], kernel)
    return score, depth


def disagreement(depth: np.ndarray) -> np.ndarray:
    """Per photo, how far its distances are off the others' where three overlap (the median)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        middle = np.nanmedian(np.where(depth > 0, depth, np.nan), axis=0)
    seen = (depth > 0).sum(axis=0)
    out = np.ones(len(depth))
    for k in range(len(depth)):
        both = (depth[k] > 0) & (seen >= 3)
        if both.sum() > 500:
            out[k] = float(np.median(np.abs(depth[k][both] / middle[both] - 1)))
    return out


def agreed_cloud(depth: np.ndarray, good: np.ndarray, centre: np.ndarray) -> np.ndarray:
    """What the photos that agree measured, as scene points: one per panorama cell they show."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        middle = np.nanmedian(np.where(depth[good] > 0, depth[good], np.nan), axis=0)
    ey, ax = np.nonzero(np.isfinite(middle))
    azimuth, elevation = np.radians((ax + 0.5) * CELL), np.radians(90 - (ey + 0.5) * CELL)
    dirs = np.column_stack([np.sin(azimuth) * np.cos(elevation), np.sin(elevation),
                            -np.cos(azimuth) * np.cos(elevation)])
    return centre + dirs * middle[ey, ax][:, None]


def match_exposure(photos: list[Photo], centre: np.ndarray, depth: np.ndarray) -> None:
    """
    A colour gain per photo so that neighbours agree where they show the
    same thing: the least squares of their log ratios, held to the photos
    used most gladly, and applied most of the way only: a phone exposes a
    view down into a garden and one across at the sky differently, and each
    looks right so.
    """
    width, height = int(360 / CELL), int(180 / CELL)
    total = np.zeros((len(photos), height, width, 3), np.float32)
    count = np.zeros((len(photos), height, width), np.float32)
    for k, p in enumerate(photos):
        ax, ey = pano_cells(p.points, centre)
        lin = srgb_to_linear(p.colour)
        ok = p.keep & ~p.fringe & (lin.max(axis=1) < 0.9) & (lin.min(axis=1) > 0.004)
        np.add.at(total[k], (ey[ok], ax[ok]), lin[ok])
        np.add.at(count[k], (ey[ok], ax[ok]), 1)
    rows, rhs = [], []
    for i in range(len(photos)):
        for j in range(i + 1, len(photos)):
            both = ((count[i] > 0) & (count[j] > 0)
                    & (np.abs(depth[i] / np.maximum(depth[j], 1e-3) - 1) < 0.1))
            if both.sum() < 150:
                continue
            a = total[i][both] / count[i][both][:, None]
            b = total[j][both] / count[j][both][:, None]
            ratio = np.median(np.log(np.maximum(a, 1e-4) / np.maximum(b, 1e-4)), axis=0)
            row = np.zeros(len(photos))
            row[i], row[j] = -1, 1  # log gain j - log gain i = log(a / b)
            weight = min(float(both.sum()), 4000.0) ** 0.5
            rows.append(row * weight)
            rhs.append(ratio * weight)
    if not rows:
        return
    held = np.array([p.quality for p in photos]) ** 2
    log_gain = np.linalg.lstsq(np.vstack([np.array(rows), np.diag(held * 0.5)]),
                               np.vstack([np.array(rhs), np.zeros((len(photos), 3))]),
                               rcond=None)[0]
    glad = [k for k, p in enumerate(photos) if p.quality >= 0.9] or list(range(len(photos)))
    log_gain -= np.median(log_gain[glad], axis=0)
    for k, p in enumerate(photos):
        p.gain = np.exp(0.7 * np.clip(log_gain[k], -0.6, 0.6))


def triangles(p: Photo, mine: np.ndarray, push: np.ndarray | None = None
              ) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
    """
    Positions, texture pixels and triangles of the photo's share `mine` (its
    grid, bools); `push`: per vertex, how many times its distance it stands at.
    """
    push = np.ones(mine.size) if push is None else push
    index = np.arange(mine.size).reshape(p.grid)
    quad = mine[:-1, :-1] & mine[1:, :-1] & mine[:-1, 1:] & mine[1:, 1:]
    corners = np.stack([index[:-1, :-1][quad], index[1:, :-1][quad], index[:-1, 1:][quad],
                        index[1:, 1:][quad]], 1)
    if len(corners) < 40:
        return None
    d = p.dist[corners]
    split = (d.max(axis=1) > EDGE * d.min(axis=1)) & (d.max(axis=1) - d.min(axis=1) > 0.5)
    if p.smooth:  # one smooth front: its quads laid flat one by one were a field of dots
        split[:] = False
    whole = corners[~split]
    # What the photo saw at a glance along it (a hedge's side, down a wall)
    # it has no picture of, only a smear: laid flat like an edge below,
    # unless it is the ground, which is seen so from everywhere.
    points = p.points
    a3, b3, c3 = points[whole[:, 0]], points[whole[:, 1]], points[whole[:, 2]]
    normal = np.cross(b3 - a3, c3 - a3)
    normal /= np.maximum(np.linalg.norm(normal, axis=1), 1e-9)[:, None]
    facing = np.abs((normal * p.dirs[whole[:, 0]]).sum(axis=1))
    straight = (facing > GLANCE) | p.flat[whole].all(axis=1) | p.smooth
    glancing, whole = whole[~straight], whole[straight]
    used = np.unique(whole)
    remap = np.full(mine.size, -1)
    remap[used] = np.arange(len(used))
    positions, pixels = [p.at(used, p.dist[used] * push[used])], [p.px[used]]
    a, b, c, e = (remap[whole[:, i]] for i in range(4))
    tris = [np.column_stack([a, b, c]), np.column_stack([c, b, e])]
    # A quad across the edge of something near: laid flat on what is behind
    # it, at its farthest corner's distance. Stretched from near to far it
    # smeared across the view from anywhere but where the photo was taken.
    edge = np.concatenate([corners[split], glancing])
    if len(edge):
        flat = edge.ravel()
        positions.append(p.at(flat, np.repeat(p.dist[edge].max(axis=1), 4) * push[flat]))
        pixels.append(p.px[flat])
        base = len(used) + 4 * np.arange(len(edge))
        tris += [np.column_stack([base, base + 1, base + 2]),
                 np.column_stack([base + 2, base + 1, base + 3])]
    return np.concatenate(positions), np.concatenate(pixels), np.concatenate(tris)


def texture(p: Photo, frame: Path, distortion: tuple[float, ...], sun: np.ndarray,
            long_side: int = 2048) -> np.ndarray:
    """
    The photo at full size, undistorted to the stereo's pinhole camera
    (`distortion`: the model's fx, fy, cx, cy, k1, k2, p1, p2 for the frame),
    its veil off and its gain on. BGR.
    """
    fx, fy, cx, cy, k1, k2, p1, p2 = distortion
    source = cv2.imread(str(frame))
    view = p.view
    f = source.shape[0] / view.size[1]  # the stereo's camera -> full size
    w, h = int(view.size[0] * f), int(view.size[1] * f)
    u, v = np.meshgrid(np.arange(w) + 0.5, np.arange(h) + 0.5)
    x = (u / f - view.k[0, 2]) / view.k[0, 0]
    y = (v / f - view.k[1, 2]) / view.k[1, 1]
    r2 = x * x + y * y
    radial = 1 + k1 * r2 + k2 * r2 * r2
    xd = x * radial + 2 * p1 * x * y + p2 * (r2 + 2 * x * x)
    yd = y * radial + p1 * (r2 + 2 * y * y) + 2 * p2 * x * y
    out = cv2.remap(source, (xd * fx + cx - 0.5).astype(np.float32),
                    (yd * fy + cy - 0.5).astype(np.float32), cv2.INTER_LANCZOS4,
                    borderMode=cv2.BORDER_REPLICATE)
    scale = long_side / max(w, h)
    if scale < 1:
        out = cv2.resize(out, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA)
    if p.veil > 0:
        rays = np.stack([cv2.resize(c, out.shape[1::-1]) for c in (x, y, np.ones_like(x))], -1)
        rays /= np.linalg.norm(rays, axis=-1, keepdims=True)
        out = unveil(out, rays, p.to_camera @ sun, p.veil)
    if not np.allclose(p.gain, 1):
        lin = srgb_to_linear(out[..., ::-1].astype(np.float32) / 255) * p.gain
        out = (linear_to_srgb(np.clip(lin, 0, 1)) * 255).astype(np.uint8)[..., ::-1]
    return out


def sky_panorama(photos: list[Photo], sun: np.ndarray, directions: np.ndarray
                 ) -> tuple[np.ndarray, np.ndarray]:
    """
    The photos' own sky laid on a panorama's `directions` (sky.directions):
    linear RGB, and how surely each spot of it is sky. Not round the sun:
    the viewer draws it where it stands at each time, and the photos' glare
    would stay behind as a second one.
    """
    height, width = directions.shape[:2]
    colour, total, weight = np.zeros((height, width, 3)), np.zeros((height, width)), np.zeros(
        (height, width))
    off = np.degrees(np.arccos(np.clip(directions @ sun, -1, 1)))
    glare = np.clip((off - 14) / 14, 0, 1)
    glare = glare * glare * (3 - 2 * glare)
    for p in photos:
        w, h = p.size
        cam = directions @ p.to_camera.T
        z = cam[..., 2]
        with np.errstate(divide="ignore", invalid="ignore"):
            px = p.view.k[0, 0] * cam[..., 0] / z + p.view.k[0, 2]
            py = p.view.k[1, 1] * cam[..., 1] / z + p.view.k[1, 2]
        inside = (z > 0.05) & (px >= 0) & (px <= w - 1) & (py >= 0) & (py <= h - 1)
        mx = np.where(inside, px, -1).astype(np.float32)
        my = np.where(inside, py, -1).astype(np.float32)
        # As taken: haze removal would take the sky's own light for a veil.
        filled = cv2.inpaint(p.taken, (~p.sky_full).astype(np.uint8), 5, cv2.INPAINT_TELEA)
        lin = srgb_to_linear(filled[..., ::-1].astype(np.float32) / 255) * p.gain
        seen = cv2.remap(lin.astype(np.float32), mx, my, cv2.INTER_LINEAR,
                         borderMode=cv2.BORDER_CONSTANT)
        clear = cv2.erode(p.sky_full.astype(np.uint8), np.ones((9, 9), np.uint8)).astype(np.float32)
        clear = cv2.remap(cv2.GaussianBlur(clear, (0, 0), 5), mx, my, cv2.INTER_LINEAR,
                          borderMode=cv2.BORDER_CONSTANT) * glare
        edge = min(w, h) * 0.3
        border = np.clip(np.minimum.reduce([px, w - 1 - px, py, h - 1 - py]) / edge, 0, 1)
        sure = np.where(inside, border * border * (3 - 2 * border) * clear, 0.0)
        colour += seen * (sure * p.quality ** 2)[..., None]
        total += sure * p.quality ** 2
        weight = np.maximum(weight, sure)
    return colour / np.maximum(total, 1e-6)[..., None], weight


@dataclass
class Built:
    meshes: list  # glb.Mesh: positions, uvs, triangles, WebP bytes
    centre: np.ndarray  # where the photos compete from, scene metres
    ground: np.ndarray  # (h, w, 3) linear RGB per direction from centre, sky.py's convention
    ground_known: np.ndarray
    photos: list[Photo]
    report: dict


def build(photos: list[Photo], layout: Layout, frames: dict[str, Path],
          distortion: tuple[float, ...],
          redo_depth=None) -> Built:
    """
    The meshes of `photos` and the ground panorama. `redo_depth(photo,
    cloud)` gives a photo whose depth disagrees a new one from the scene
    points the others agree on (surround.py pins the network's depth to
    them), or None.
    """
    central = [p.centre for p in photos if p.name in layout.centre] or [p.centre for p in photos]
    centre = np.array(layout.seen_from) if layout.seen_from else np.median(central, axis=0)
    score, depth = splat(photos, centre)
    off = disagreement(depth)
    report = {"depthOffBefore": {p.name: round(float(o), 3)
                                 for p, o in zip(photos, off, strict=True)}}
    for _ in range(2):
        good = off < AGREE
        if good.all() or not good.any() or redo_depth is None:
            break
        cloud = agreed_cloud(depth, good, centre)
        for k in np.flatnonzero(~good):
            again = redo_depth(photos[k], cloud)
            if again is not None:
                photos[k] = again
        score, depth = splat(photos, centre)
        off = disagreement(depth)
    for p, o in zip(photos, off, strict=True):
        if o > 0.15:
            p.quality *= 0.5  # still unsure of it: used only where nothing else shows
    report["depthOff"] = {p.name: round(float(o), 3) for p, o in zip(photos, off, strict=True)}
    levels = ground_levels(photos)
    report["groundLevels"] = [round(v, 2) for v in levels]
    for p in photos:
        p.lay_on_ground(levels, layout.lay)
    score, depth = splat(photos, centre)
    match_exposure(photos, centre, depth)
    report["gains"] = {p.name: round(float(p.gain.mean()), 2) for p in photos}
    # Smoothed before they compete: cell by cell, two photos of much the same
    # score took turns, and their shares were a patchwork of blocks, each a
    # little off the next in depth and exposure.
    smooth = np.stack([cv2.GaussianBlur(layer, (0, 0), SEAM / CELL) * (layer > 0)
                       for layer in score])
    winner = np.where(score.max(axis=0) > 0, smooth.argmax(axis=0), -1)
    # A small patch of one photo among the others' shares goes to them: left
    # in, it stood out of place; left out, the blurred ground showed as a hole.
    for _ in range(2):
        for k in range(len(photos)):
            _, labels, stats, _ = cv2.connectedComponentsWithStats(
                (winner == k).astype(np.uint8), connectivity=8)
            small = np.flatnonzero(stats[:, cv2.CC_STAT_AREA] < PATCH)
            others = np.delete(score, k, axis=0).max(axis=0) > 0
            smooth[k][np.isin(labels, small[small > 0]) & others] = 0
        winner = np.where(smooth.max(axis=0) > 0, smooth.argmax(axis=0), -1)

    width, height = int(360 / CELL), int(180 / CELL)
    ground = np.zeros((height, width, 3), np.float32)
    ground_n = np.zeros((height, width), np.float32)
    meshes, sizes = [], {}
    for k, p in enumerate(photos):
        ax, ey = pano_cells(p.points, centre)
        mine = (p.keep & (winner[ey, ax] == k)).reshape(p.grid)
        shows = mine.ravel() & ~p.fringe
        np.add.at(ground, (ey[shows], ax[shows]), (srgb_to_linear(p.colour) * p.gain)[shows])
        np.add.at(ground_n, (ey[shows], ax[shows]), 1)
        # Its mesh runs on under its neighbours' a little, set back behind
        # them: where their depths differ, seen from the side, the gap
        # between two shares showed the blurred ground behind as a hole.
        own_share = mine
        mine = (cv2.dilate(mine.astype(np.uint8), np.ones((2 * OVERLAP + 1,) * 2, np.uint8)) > 0
                ) & p.keep.reshape(p.grid)
        push = np.where(mine & ~(cv2.dilate(own_share.astype(np.uint8), np.ones((3, 3), np.uint8))
                                 > 0), BEHIND, 1.0).ravel()
        # Specks (a far aerial, a bird) are left out: judged without their fringe.
        body = mine & ~p.fringe.reshape(p.grid)
        count, labels, stats, _ = cv2.connectedComponentsWithStats(body.astype(np.uint8),
                                                                   connectivity=8)
        small = np.flatnonzero(stats[:, cv2.CC_STAT_AREA] < SPECK)
        body &= ~np.isin(labels, small[small > 0])
        mine &= cv2.dilate(body.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
        built = triangles(p, mine, push)
        if built is None or len(built[2]) < 3000:
            continue  # a scrap of a photo among the others' shares: a patch out of place
        positions, px, tri = built
        image = texture(p, frames[p.name], distortion, layout.sun)
        th, tw = image.shape[:2]
        fx, fy = tw / p.size[0], th / p.size[1]
        lo = np.floor(px.min(axis=0) - 2 * STEP).clip(0)
        hi = np.ceil(px.max(axis=0) + 2 * STEP).clip(None, [p.size[0], p.size[1]])
        rows = slice(int(lo[1] * fy), int(hi[1] * fy))
        cols = slice(int(lo[0] * fx), int(hi[0] * fx))
        alpha = cv2.resize((~p.sky_full).astype(np.uint8) * 255, (tw, th),
                           interpolation=cv2.INTER_LINEAR)[rows, cols]
        # Sky's blue would fringe the roofs where the cut falls between
        # pixels: the colour just under the cut is carried out from the roofs.
        rgb = cv2.cvtColor(image[rows, cols], cv2.COLOR_BGR2RGB)
        gone = (alpha < 128).astype(np.uint8)
        rim = cv2.dilate(gone, np.ones((3, 3), np.uint8)) & (
            cv2.erode(gone, np.ones((9, 9), np.uint8)) == 0)
        rgb = cv2.inpaint(rgb, rim.astype(np.uint8), 3, cv2.INPAINT_TELEA)
        webp = io.BytesIO()
        Image.fromarray(np.dstack([rgb, alpha])).save(webp, "WEBP", quality=86, method=4)
        meshes.append((positions, (px - lo) / (hi - lo), tri, webp.getvalue()))
        sizes[p.name] = {"vertices": int(len(positions)), "triangles": int(len(tri)),
                         "texture": [int(rgb.shape[1]), int(rgb.shape[0])],
                         "kB": len(webp.getvalue()) // 1024}
    report["meshes"] = sizes
    known = ground_n > 0
    ground[known] /= ground_n[known][:, None]
    # In sky.py's convention: the middle column looks along -z.
    return Built(meshes, centre, np.roll(ground, width // 2, axis=1),
                 np.roll(known, width // 2, axis=1), photos, report)
