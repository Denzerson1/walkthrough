"""
The sky out of the windows, at each baked time of day.

A painted gradient reads as a render. This is the sky the light comes
from: sunlight scattered by air (Rayleigh: blue overhead, gold and pink
low down) and haze (Mie: the bright glow round the sun), integrated along
each line of sight through the atmosphere, the classic single-scattering
model of Nishita et al. It is matched to the owner's photo out of the
window, and where the photo shows sky, the photo's own sky (its exact
blue, its contrails) is kept and shifted to the time of day by the
model's change in colour since the photo was taken.

Panoramas are equirectangular: u = 0.5 looks along scene -z, u grows
turning towards +x, v = 0 is the zenith (see `directions`).
"""

from __future__ import annotations

import math

import cv2
import numpy as np

from .photo_texture import linear_to_srgb, srgb_to_linear

EARTH, TOP = 6_360e3, 6_420e3  # radii, metres
RAYLEIGH = np.array([5.8e-6, 13.5e-6, 33.1e-6])  # scattering per metre at sea level
# A clear day, like the one in the photos (Bruneton's values): little haze.
MIE, MIE_EXTINCTION, MIE_G = 4.0e-6, 4.44e-6, 0.8
RAYLEIGH_HEIGHT, MIE_HEIGHT = 8_000.0, 1_200.0
# Ozone absorbs orange light high up: what keeps the zenith blue at dusk.
OZONE = np.array([0.65e-6, 1.881e-6, 0.085e-6])
OZONE_CENTRE, OZONE_WIDTH = 25_000.0, 15_000.0


def _ozone(h: np.ndarray) -> np.ndarray:
    return np.maximum(0.0, 1 - np.abs(h - OZONE_CENTRE) / OZONE_WIDTH)


def directions(width: int, height: int) -> np.ndarray:
    """Scene unit direction of each equirectangular pixel's centre, (height, width, 3)."""
    u = (np.arange(width) + 0.5) / width
    v = (np.arange(height) + 0.5) / height
    azimuth = (u - 0.5) * 2 * np.pi
    elevation = (0.5 - v) * np.pi
    az, el = np.meshgrid(azimuth, elevation)
    return np.stack([np.sin(az) * np.cos(el), np.sin(el), -np.cos(az) * np.cos(el)], axis=-1)


def _sphere_exit(origin: np.ndarray, direction: np.ndarray, radius: float) -> np.ndarray:
    """Distance along each ray from inside a sphere at the origin to its surface."""
    b = (origin * direction).sum(-1)
    c = (origin * origin).sum(-1) - radius * radius
    return -b + np.sqrt(np.maximum(b * b - c, 0.0))


def atmosphere(sun: np.ndarray, width: int = 256, height: int = 128, steps: int = 16,
               light_steps: int = 8) -> np.ndarray:
    """
    Linear sky radiance (sun of radiance 1 outside the atmosphere) seen from
    the ground, as an equirectangular panorama. Below the horizon the
    horizon's colour continues, darkening: there the view shows ground.
    """
    sun = np.asarray(sun, float) / np.linalg.norm(sun)
    d = directions(width, height)
    up = d.copy()
    up[..., 1] = np.maximum(up[..., 1], math.sin(math.radians(2)))  # lower: as at 2 degrees
    up /= np.linalg.norm(up, axis=-1, keepdims=True)
    eye = np.array([0.0, EARTH + 2.0, 0.0])
    length = _sphere_exit(eye, up, TOP)
    ds = length / steps
    mu = (up @ sun)
    phase_r = 3 / (16 * np.pi) * (1 + mu * mu)
    g = MIE_G
    phase_m = 3 / (8 * np.pi) * ((1 - g * g) * (1 + mu * mu)) / (
        (2 + g * g) * np.power(1 + g * g - 2 * g * mu, 1.5))
    sum_r = np.zeros(d.shape)
    sum_m = np.zeros(d.shape)
    depth_r = np.zeros(d.shape[:2])
    depth_m = np.zeros(d.shape[:2])
    depth_o = np.zeros(d.shape[:2])
    for i in range(steps):
        p = eye + up * ((i + 0.5) * ds)[..., None]
        h = np.linalg.norm(p, axis=-1) - EARTH
        hr, hm = np.exp(-h / RAYLEIGH_HEIGHT) * ds, np.exp(-h / MIE_HEIGHT) * ds
        depth_r += hr
        depth_m += hm
        depth_o += _ozone(h) * ds
        # Towards the sun from here: light that reaches the sample.
        sun_len = _sphere_exit(p, np.broadcast_to(sun, p.shape), TOP)
        sl = sun_len / light_steps
        lr = np.zeros(h.shape)
        lm = np.zeros(h.shape)
        lo = np.zeros(h.shape)
        blocked = np.zeros(h.shape, bool)
        for j in range(light_steps):
            q = p + sun * ((j + 0.5) * sl)[..., None]
            hq = np.linalg.norm(q, axis=-1) - EARTH
            blocked |= hq < 0
            lr += np.exp(-hq / RAYLEIGH_HEIGHT) * sl
            lm += np.exp(-hq / MIE_HEIGHT) * sl
            lo += _ozone(hq) * sl
        tau = (RAYLEIGH[None, None] * (depth_r + lr)[..., None]
               + MIE_EXTINCTION * (depth_m + lm)[..., None]
               + OZONE[None, None] * (depth_o + lo)[..., None])
        att = np.exp(-tau) * (~blocked)[..., None]
        sum_r += att * hr[..., None]
        sum_m += att * hm[..., None]
    sky = 20.0 * (sum_r * RAYLEIGH * phase_r[..., None] + sum_m * MIE * phase_m[..., None])
    # Light scattered more than once, which single scattering leaves out,
    # turns the low sky pale: towards grey of the same brightness there.
    elevation = np.degrees(np.arcsin(np.clip(d[..., 1], -1, 1)))
    keep = (0.35 + 0.65 * np.clip(elevation / 12, 0, 1))[..., None]
    luma = (sky @ [0.2126, 0.7152, 0.0722])[..., None]
    sky = luma + (sky - luma) * keep
    below = np.clip(-d[..., 1] / 0.15, 0, 1)[..., None]
    return sky * (1 - 0.6 * below)


def project_photo(width: int, height: int, rgb_linear: np.ndarray, sky: np.ndarray,
                  right: np.ndarray, up: np.ndarray, forward: np.ndarray, f: float
                  ) -> tuple[np.ndarray, np.ndarray]:
    """
    A photo's sky laid onto the panorama's directions: its colour where it
    shows sky, and a weight, 1 well inside its sky, fading at the photo's
    edges and to 0 at the roofs (where the photo has buildings, not sky).
    """
    h, w = sky.shape
    d = directions(width, height)
    z = d @ forward
    with np.errstate(divide="ignore", invalid="ignore"):
        px = w / 2 + f * (d @ right) / z
        py = h / 2 - f * (d @ up) / z
    inside = (z > 0.05) & (px >= 0) & (px <= w - 1) & (py >= 0) & (py <= h - 1)
    map_x = np.where(inside, px, -1).astype(np.float32)
    map_y = np.where(inside, py, -1).astype(np.float32)
    # Sky colours carried down behind the roofs, so a sliver seen past a
    # roof edge from elsewhere in the room is sky, not a building's colour.
    filled = cv2.inpaint((linear_to_srgb(rgb_linear) * 255).astype(np.uint8),
                         (~sky).astype(np.uint8), 5, cv2.INPAINT_TELEA)
    colour = cv2.remap(srgb_to_linear(filled.astype(np.float32) / 255), map_x, map_y,
                       cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
    # Weighted by how surely each spot is sky: off the roofs' edges by a few
    # pixels, and softly, so the model's sky takes over behind them.
    clear = cv2.erode(sky.astype(np.uint8), np.ones((7, 7), np.uint8)).astype(np.float32)
    clear = cv2.remap(cv2.GaussianBlur(clear, (0, 0), 4), map_x, map_y, cv2.INTER_LINEAR,
                      borderMode=cv2.BORDER_CONSTANT)
    # A wide, smooth fade at the photo's edges: a narrow one drew its outline
    # in the sky, the photo's blue and the model's never quite the same.
    edge = min(w, h) * 0.3
    border = np.clip(np.minimum.reduce([px, w - 1 - px, py, h - 1 - py]) / edge, 0, 1)
    weight = np.where(inside, border * border * (3 - 2 * border) * clear, 0.0)
    return colour, weight


def match_rows(colour: np.ndarray, weight: np.ndarray, model: np.ndarray) -> np.ndarray:
    """
    Per panorama row (elevation), the colour the model must be multiplied by
    to look like the photo's sky there: the median ratio where the photo is
    surely sky, carried to the rows it does not reach from the nearest it
    does, and smoothed. Makes the model's sky the photo's from horizon to
    zenith, so the photo's own sky sits in it without a seam.
    """
    height = colour.shape[0]
    rows = np.full((height, 3), np.nan)
    for y in range(height):
        sure = weight[y] > 0.5
        if sure.sum() >= 8:
            rows[y] = np.median(colour[y][sure] / model[y][sure], axis=0)
    known = np.flatnonzero(~np.isnan(rows[:, 0]))
    if known.size == 0:
        return np.ones((height, 3))
    for c in range(3):
        rows[:, c] = np.interp(np.arange(height), known, rows[known, c])
    rows = cv2.GaussianBlur(rows.astype(np.float32)[:, None, :], (1, 0), sigmaX=0.1,
                            sigmaY=height / 64)[:, 0, :]
    return np.clip(rows, 0.05, 20.0)


def panorama(sun: np.ndarray, photo_sun: np.ndarray | None, photo: tuple | None,
             width: int = 2048, height: int = 1024, adapt: float = 0.5,
             reference: float | None = None) -> tuple[np.ndarray, float]:
    """
    The sky at one time of day as display-ready sRGB (uint8), without the
    sun itself (the viewer draws it where it stands at any time), and the
    brightness it was exposed for (pass the first time's as `reference`
    for the rest, so a camera follows the light only halfway: dusk stays
    darker). `photo` = (colour, weight) from project_photo and the
    photo's sun direction `photo_sun`: the model is matched to it, and the
    photo's own sky kept where it has one.
    """
    model = cv2.resize(atmosphere(sun), (width, height), interpolation=cv2.INTER_CUBIC)
    model = np.maximum(model, 0.0)
    if photo is not None and photo_sun is not None:
        colour, weight = photo
        then = cv2.resize(atmosphere(photo_sun), (width, height), interpolation=cv2.INTER_CUBIC)
        then = np.maximum(then, 1e-6)
        match = match_rows(colour, weight, then)
        model, then = model * match[:, None], then * match[:, None]
        # The photo's sky, as the light has changed since it was taken.
        shifted = colour * np.clip(model / then, 0.0, 8.0)
        model = model * (1 - weight[..., None]) + shifted * weight[..., None]
    else:
        model = model * (0.6 / max(float(np.percentile(model, 90)), 1e-6))
    upper = model[: height // 2]
    brightness = float(np.mean(upper @ [0.2126, 0.7152, 0.0722]))
    if reference is not None:
        model = model * (reference / max(brightness, 1e-6)) ** adapt
    return (linear_to_srgb(np.clip(model, 0, 1)) * 255).astype(np.uint8), (
        reference if reference is not None else brightness)
