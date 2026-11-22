"""
A surround's reconstruction set in the flat's own coordinates.

Structure from motion knows neither up, nor north, nor metres. Here:

- Up, from the photos' vertical lines (house corners, fence posts, the
  balcony's bars): each photo's vanishing point of them, turned into the
  model by its pose; the photos agree to under a degree.
- North, from the sun where a photo has it in frame: its bearing at the
  second the photo was taken is known (flat_geometry.sun_position), so the
  model's direction to it turns the model. The sun's height in the model,
  against its known one, checks up and the poses (flat-02: within 0.3
  degrees in eight photos).
- Metres and where the flat is, from the balcony the photos show and the
  flat's plan has: its deck and the roof over it (their distance apart is
  the plan's ceiling height), the top of its rail (which way the house
  faces, and how far out), a side wall (where along).

Which leaves the compass bearing the balcony faces as something measured:
the model's north against the rail.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np

from . import view_photo
from .surround_recon import View


def sun_in_frame(bgr: np.ndarray) -> tuple[float, float] | None:
    """
    The middle of the sun's burnt-out disc in a photo, if it has one: of
    its largest saturated blob, the point deepest inside (the blob's
    centroid is pulled off by the flare and by a roof cutting into it).
    """
    grey = cv2.GaussianBlur(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY), (0, 0), bgr.shape[0] / 400)
    burnt = (grey >= 252).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(burnt)
    if count < 2:
        return None
    blob = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    if stats[blob, cv2.CC_STAT_AREA] < 0.0015 * grey.size:
        return None
    depth = cv2.distanceTransform((labels == blob).astype(np.uint8), cv2.DIST_L2, 5)
    y, x = np.unravel_index(depth.argmax(), depth.shape)
    return float(x), float(y)


def up_direction(views: list[View], images: list[np.ndarray]) -> tuple[np.ndarray, float]:
    """The model's up, and the photos' median disagreement about it in degrees."""
    ups = []
    for view, bgr in zip(views, images, strict=True):
        try:
            in_camera = view_photo.vertical_direction(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB),
                                                      float(view.k[0, 0]))
        except ValueError:
            continue
        ups.append(view.rotation.T @ in_camera)
    ups = np.array(ups)
    up = np.median(ups, axis=0)
    up /= np.linalg.norm(up)
    for _ in range(5):
        off = np.degrees(np.arccos(np.clip(ups @ up, -1, 1)))
        agree = off < max(3.0, 1.5 * float(np.median(off)))
        up = ups[agree].mean(axis=0)
        up /= np.linalg.norm(up)
    off = np.degrees(np.arccos(np.clip(ups @ up, -1, 1)))
    return up, float(np.median(off[agree]))


def north_direction(up: np.ndarray, suns: list[tuple[np.ndarray, float, float]]
                    ) -> tuple[np.ndarray, list[dict]]:
    """
    The model's north from `suns`: (direction to the sun in the model, its
    compass bearing, its elevation, degrees). Each gives a north; the ones
    whose sun stands at the right height and that agree with the rest are
    averaged. Also returned: what each said, for the record.
    """
    said = []
    for direction, bearing, elevation in suns:
        level = direction - (direction @ up) * up
        level /= np.linalg.norm(level)
        b = math.radians(bearing)
        # `level` points at bearing b; 90 degrees clockwise of it, seen from above, is level x up.
        north = level * math.cos(b) - np.cross(level, up) * math.sin(b)
        said.append({"north": north, "elevation_off": float(
            np.degrees(np.arcsin(np.clip(direction @ up, -1, 1))) - elevation)})
    norths = np.array([s["north"] for s in said])
    good = np.array([abs(s["elevation_off"]) < 1.0 for s in said])
    if not good.any():
        raise ValueError("No sun in frame stands at its known height: the poses or up are off.")
    centre = np.median(norths[good], axis=0)
    centre /= np.linalg.norm(centre)
    off = np.degrees(np.arccos(np.clip(norths @ centre, -1, 1)))
    used = good & (off < 1.5)
    north = norths[used].mean(axis=0)
    north -= (north @ up) * up
    north /= np.linalg.norm(north)
    for s, o, u in zip(said, off, used, strict=True):
        s.update(north=[round(float(v), 4) for v in s["north"]], off=round(float(o), 2),
                 used=bool(u), elevation_off=round(s["elevation_off"], 2))
    return north, said


def scene_axes(facing: float) -> np.ndarray:
    """Rows: the scene's x, y, z in (east, north, up), for a balcony side (-z) facing `facing`."""
    x, z = math.radians(facing + 90), math.radians(facing)
    return np.array([[math.sin(x), math.cos(x), 0.0], [0.0, 0.0, 1.0],
                     [-math.sin(z), -math.cos(z), 0.0]])


@dataclass
class Balcony:
    """The plan's balcony, in scene metres: its floor is y = 0."""

    rail_z: float  # the outer edge the rail stands on (the side the flat faces)
    wall_x: float  # a side wall of it
    wall_towards: int  # -1: that wall is at the low-x end, +1: at the high-x end
    height: float  # deck to the roof over it
    rail_height: float


@dataclass
class Registration:
    scale: float  # metres per model unit
    facing: float  # compass bearing the balcony side faces, degrees
    matrix: np.ndarray  # scene = matrix @ model + origin
    origin: np.ndarray
    found: dict  # what was fitted, for the record

    def to_scene(self, points: np.ndarray) -> np.ndarray:
        return points @ self.matrix.T + self.origin


def _levels(values: np.ndarray, bin_size: float) -> list[tuple[float, int]]:
    """Where `values` pile up: (value, count) of each peak of their histogram, strongest first."""
    lo, hi = float(values.min()), float(values.max())
    # A bin to spare at either end: a peak is found between two neighbours.
    hist, edges = np.histogram(values, bins=np.arange(lo - 2 * bin_size, hi + 3 * bin_size,
                                                      bin_size))
    smooth = np.convolve(hist, [0.25, 0.5, 0.25], mode="same")
    peaks = [i for i in range(1, len(smooth) - 1)
             if smooth[i] >= smooth[i - 1] and smooth[i] > smooth[i + 1]]
    return sorted(((float(edges[i] + bin_size / 2), int(smooth[i])) for i in peaks),
                  key=lambda p: -p[1])


def _outermost(values: np.ndarray, bin_size: float, towards: int, share: float = 0.3) -> float:
    """
    Of the levels `values` pile up at, the farthest towards +1 or -1 that
    holds at least `share` of the strongest's points: a wall, with the
    furniture standing before it; a rail, with the flower boxes hung
    outside it (towards the house is +z).
    """
    levels = _levels(values, bin_size)
    strong = [v for v, n in levels if n >= share * levels[0][1]]
    return max(strong) if towards > 0 else min(strong)


def fit_balcony(points: np.ndarray, cameras: np.ndarray, enu: np.ndarray, balcony: Balcony,
                facing_guess: float) -> Registration:
    """
    The similarity from the model to the scene. `points`: what the photos
    taken on the balcony measured (model units), `cameras`: where those were
    taken; `enu`: rows east, north, up in the model. The rail is taken to
    run within ten degrees of where `facing_guess` puts it.
    """
    centre = cameras.mean(axis=0)
    level_cams = (cameras - centre) @ enu[:2].T
    reach = 1.5 * float(max(np.linalg.norm(level_cams[:, None] - level_cams[None], axis=2).max(),
                            1e-6))
    local = (points - centre) @ enu.T  # east, north, up, from the cameras' middle
    near = local[np.linalg.norm(local[:, :2], axis=1) < reach]
    bin_size = reach / 200

    def piled(level: float, within: float) -> int:
        return int((np.abs(near[:, 2] - level) < within).sum())

    # The deck and the roof over it: of the levels below and above the
    # cameras, the pair that puts the cameras at a height a phone is held at
    # (1.1 to 1.9 m over the deck) and has the most points on both and on
    # the rail's top, where that scale puts it.
    levels = _levels(near[:, 2], bin_size)
    below = [(v, n) for v, n in levels if v < -0.03 * reach][:10]
    above = [(v, n) for v, n in levels if v > 0.03 * reach][:8]
    best = None
    for deck, on_deck in below:
        for soffit, on_soffit in above:
            scale = balcony.height / (soffit - deck)
            if not 1.1 <= -deck * scale <= 1.9:
                continue
            on_rail = piled(deck + balcony.rail_height / scale, 0.03 / scale)
            score = on_deck * on_soffit * (1 + on_rail)
            if best is None or score > best[0]:
                best = (score, deck, soffit)
    if best is None:
        raise ValueError("No deck and roof found in the photos taken on the balcony.")
    _, deck, soffit = best
    deck = float(np.median(near[np.abs(near[:, 2] - deck) < 2 * bin_size][:, 2]))
    soffit = float(np.median(near[np.abs(near[:, 2] - soffit) < 2 * bin_size][:, 2]))
    scale = balcony.height / (soffit - deck)
    # With metres known: a balcony's rail and walls are within eight of where one stands on it.
    reach = 8.0 / scale

    # The rail's top: a straight line at its height, which way the house faces.
    facing = facing_guess
    for _ in range(3):
        axes = scene_axes(facing)
        level = local @ axes.T / 1.0  # scene axes, still model units, cameras' middle the origin
        band = np.abs(level[:, 1] - (deck + balcony.rail_height / scale)) < 0.03 / scale
        towards = level[:, 2] < 0  # on the side the flat faces
        rail = level[band & towards & (np.linalg.norm(level[:, [0, 2]], axis=1) < reach)]
        z_rail = _outermost(rail[:, 2], 0.02 / scale, +1)
        on = rail[np.abs(rail[:, 2] - z_rail) < 0.06 / scale]
        x, z = on[:, 0], on[:, 2]
        for _ in range(6):
            k, c = np.polyfit(x, z, 1)
            keep = np.abs(z - (k * x + c)) < 0.02 / scale
            x, z = x[keep], z[keep]
        turn = math.degrees(math.atan(k))
        facing += turn
        if abs(turn) < 0.05:
            break
    axes = scene_axes(facing)
    level = local @ axes.T
    band = np.abs(level[:, 1] - (deck + balcony.rail_height / scale)) < 0.03 / scale
    rail = level[band & (level[:, 2] < 0) & (np.linalg.norm(level[:, [0, 2]], axis=1) < reach)]
    z_rail = _outermost(rail[:, 2], 0.02 / scale, +1)

    # The side wall: between deck and roof, the plane across the rail's line with most points.
    between = level[(level[:, 1] > deck + 0.3 / scale) & (level[:, 1] < soffit - 0.3 / scale)
                    & (level[:, 2] > z_rail - 0.3 / scale)
                    & (np.linalg.norm(level[:, [0, 2]], axis=1) < reach)]
    side = between[between[:, 0] * balcony.wall_towards > 0][:, 0]
    x_wall = _outermost(side, 0.02 / scale, balcony.wall_towards)

    shift = np.array([balcony.wall_x - scale * x_wall, -scale * deck,
                      balcony.rail_z - scale * z_rail])
    matrix = scale * axes @ enu
    origin = shift - matrix @ centre
    return Registration(scale, facing % 360, matrix, origin, {
        "deck": round(deck, 4), "soffit": round(soffit, 4), "rail": round(float(z_rail), 4),
        "wall": round(float(x_wall), 4), "railPoints": int(len(x)),
        "reach": round(reach, 3)})


def overlay(bgr: np.ndarray, view: View, registration: Registration,
            lines: list[tuple[tuple, tuple]]) -> np.ndarray:
    """A photo with the plan's edges (`lines`, scene metres) drawn on it, to check the fit."""
    out = bgr.copy()
    inverse = np.linalg.inv(registration.matrix)
    for a, b in lines:
        steps = np.linspace(0, 1, 60)[:, None]
        points = np.array(a) + (np.array(b) - np.array(a)) * steps
        uv, depth = view.project((points - registration.origin) @ inverse.T)
        for i in range(len(points) - 1):
            if depth[i] > 0.02 and depth[i + 1] > 0.02 and np.abs(uv[i:i + 2]).max() < 5000:
                cv2.line(out, (int(uv[i][0]), int(uv[i][1])),
                         (int(uv[i + 1][0]), int(uv[i + 1][1])), (0, 255, 255), 2)
    return out
