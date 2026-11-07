"""
Floor textures made from the customer's own photos.

The capture guide asks for each floor photographed straight down with a
sheet of A4 paper in the shot. The sheet gives the scale (it is 297 mm long)
and the exposure (office paper reflects about 85%), so the texture comes out
at true size and brightness. Not the hue: office paper carries optical
brighteners that glow blue in daylight (a test sheet read R 0.56, G 0.74,
B 0.95), and balancing on it turned a grey oak orange. The phone's own white
balance, set on a room of white walls, is the better judge of hue. The shot itself is too small and
unevenly lit to repeat, so it is re-laid: planks are cut out of it and
re-laid in rows, tiles are filled from clean patches of it with fresh joints.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

A4_LONG_M, A4_SHORT_M = 0.297, 0.210
MAX_VIEW_M = 3.0  # how much floor a rectified shot may cover, as far edges stretch
PAPER_ALBEDO = 0.85
WORK_M_PER_PX = 0.0012  # analysis resolution: grain is still visible


def srgb_to_linear(c: np.ndarray) -> np.ndarray:
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(c: np.ndarray) -> np.ndarray:
    c = np.clip(c, 0.0, 1.0)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * c ** (1 / 2.4) - 0.055)


def find_paper(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The A4 sheet: its mask and its four corners (x, y), long side first."""
    grey = cv2.cvtColor((rgb * 255).astype(np.uint8), cv2.COLOR_RGB2GRAY)
    _, bright = cv2.threshold(grey, int(np.percentile(grey, 96)) - 10, 255, cv2.THRESH_BINARY)
    bright = cv2.morphologyEx(bright, cv2.MORPH_OPEN, np.ones((9, 9), np.uint8))
    count, labels, stats, _ = cv2.connectedComponentsWithStats(bright)
    if count < 2:
        raise ValueError("No sheet of paper found in the floor photo")
    sheet = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    mask = labels == sheet
    contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_NONE)
    edge = max(contours, key=len).reshape(-1, 2).astype(np.float32)
    box = cv2.boxPoints(cv2.minAreaRect(edge))
    # Fit a line to each side, then meet them: a curled or dog-eared corner
    # pulls a rectangle fit off, but each side's line holds.
    lines = []
    for i in range(4):
        a, b = box[i], box[(i + 1) % 4]
        d = (b - a) / np.linalg.norm(b - a)
        off = np.abs((edge - a) @ np.array([-d[1], d[0]]))
        along = (edge - a) @ d
        near = edge[(off < 0.04 * np.linalg.norm(b - a))
                    & (along > 0.15 * np.linalg.norm(b - a))
                    & (along < 0.85 * np.linalg.norm(b - a))]
        vx, vy, x0, y0 = cv2.fitLine(near if len(near) > 10 else np.array([a, b]),
                                     cv2.DIST_HUBER, 0, 0.01, 0.01).ravel()
        lines.append((np.array([x0, y0]), np.array([vx, vy])))
    corners = []
    for i in range(4):
        (p, u), (q, v) = lines[i - 1], lines[i]
        t = np.linalg.solve(np.column_stack([u, -v]), q - p)[0]
        corners.append(p + t * u)
    corners = np.array(corners, dtype=np.float32)
    if np.linalg.norm(corners[1] - corners[0]) < np.linalg.norm(corners[2] - corners[1]):
        corners = np.roll(corners, -1, axis=0)
    return mask, corners


def albedo(path: Path) -> tuple[np.ndarray, np.ndarray, float]:
    """
    The photo as linear albedo at WORK_M_PER_PX, white-balanced on the
    paper, with slow light falloff across the shot evened out. Returns it,
    the paper mask (grown, so its shadow edge is excluded too) and m/px.
    """
    image = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    full = np.asarray(image, dtype=np.float32) / 255
    half = cv2.resize(full, (full.shape[1] // 2, full.shape[0] // 2), interpolation=cv2.INTER_AREA)
    _, corners = find_paper(half)
    # The sheet is a rectangle of known size lying on the floor, so it fixes
    # the whole floor plane: map it to its true shape and the photo becomes a
    # top-down view at a known scale, perspective and all taken out.
    long_px, short_px = A4_LONG_M / WORK_M_PER_PX, A4_SHORT_M / WORK_M_PER_PX
    square = np.array([[0, 0], [long_px, 0], [long_px, short_px], [0, short_px]], np.float32)
    h = cv2.getPerspectiveTransform(corners, square)
    frame = np.array([[0, 0], [half.shape[1], 0], [half.shape[1], half.shape[0]],
                      [0, half.shape[0]]], np.float32).reshape(-1, 1, 2)
    seen = cv2.perspectiveTransform(frame, h).reshape(-1, 2)
    lo = seen.min(axis=0)
    size = np.minimum(seen.max(axis=0) - lo, MAX_VIEW_M / WORK_M_PER_PX).astype(int)
    shift = np.array([[1, 0, -lo[0]], [0, 1, -lo[1]], [0, 0, 1]], np.float64)
    rgb = cv2.warpPerspective(half, shift @ h, (int(size[0]), int(size[1])),
                              flags=cv2.INTER_AREA, borderValue=(0, 0, 0))
    inside = cv2.warpPerspective(np.ones(half.shape[:2], np.float32), shift @ h,
                                 (int(size[0]), int(size[1]))) > 0.99
    lin = srgb_to_linear(rgb)
    mask, _ = find_paper(rgb)
    white = np.median(lin[cv2.erode(mask.astype(np.uint8), np.ones((15, 15))) > 0], axis=0)
    luminance = float(white @ np.array([0.2126, 0.7152, 0.0722]))
    lin = lin * (PAPER_ALBEDO / luminance)
    grown = (cv2.dilate(mask.astype(np.uint8), np.ones((41, 41))) > 0) | ~inside
    return _even_out(lin, ~grown), grown, WORK_M_PER_PX


def _even_out(lin: np.ndarray, valid: np.ndarray, sigma_m: float = 0.2) -> np.ndarray:
    """Divide out light falloff: brightness blurred over 20 cm is shading, not the floor."""
    lum = lin.mean(axis=2)
    w = valid.astype(np.float32)
    sigma = sigma_m / WORK_M_PER_PX
    shade = cv2.GaussianBlur(lum * w, (0, 0), sigma) / np.maximum(
        cv2.GaussianBlur(w, (0, 0), sigma), 1e-6)
    level = float(np.median(lum[valid]))
    return lin * (level / np.maximum(shade, 1e-6))[..., None]


def _line_response(lum: np.ndarray) -> np.ndarray:
    """Thin dark horizontal lines (joints, 1-5 mm), as what a vertical closing fills in."""
    return cv2.morphologyEx(lum, cv2.MORPH_BLACKHAT,
                            cv2.getStructuringElement(cv2.MORPH_RECT, (1, 9)))


def _straighten(lin: np.ndarray, valid: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Turn the photo so the long joints run exactly horizontal: where their rows stand out most."""
    lines = _line_response(lin.mean(axis=2))
    best, best_angle = -1.0, 0.0
    for angle in np.arange(-8, 8.01, 0.25):
        ok = _rotate(valid.astype(np.float32), angle) > 0.99
        profile = np.nan_to_num(_row_profile(_rotate(lines, angle), ok))
        score = float(np.sort(profile)[-12:].mean() / (np.median(profile) + 1e-9))
        if score > best:
            best, best_angle = score, angle
    return _rotate(lin, best_angle), _rotate(valid.astype(np.float32), best_angle) > 0.99


def _row_profile(lum: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """Each row's median brightness over its usable pixels (NaN where too few)."""
    return np.array([
        np.median(row[ok]) if ok.sum() > 50 else np.nan
        for row, ok in zip(lum, valid, strict=True)
    ])


def _rotate(a: np.ndarray, degrees: float) -> np.ndarray:
    h, w = a.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), degrees, 1.0)
    return cv2.warpAffine(a, m, (w, h), flags=cv2.INTER_LINEAR, borderValue=0)


def _lattice(profile: np.ndarray, lo_px: float, hi_px: float) -> list[int]:
    """
    Evenly spaced joints: the spacing and phase whose lines catch the most
    joint response. Planks and tiles are laid at one width, so fitting the
    whole lattice finds faint joints that a peak search misses.
    """
    smooth = cv2.GaussianBlur(profile.astype(np.float32).reshape(-1, 1), (1, 0), 1.5).ravel()
    best, best_lines = -1.0, []
    for period in np.arange(lo_px, hi_px, 0.5):
        for phase in np.arange(0, period, 1.0):
            lines = np.arange(phase, len(smooth) - 1, period).round().astype(int)
            score = float(smooth[lines].mean())
            if score > best:
                best, best_lines = score, lines.tolist()
    return best_lines


def plank_texture(path: Path, plank_width: float, length: float = 2.4, rows: int = 8,
                  px_per_m: int = 800, seed: int = 0) -> tuple[Image.Image, tuple[float, float]]:
    """
    A seamless plank floor re-laid from a photo of it: each row filled with
    lengths of real plank cut from the shot, end joints and long joints drawn
    in. Planks run along the texture's vertical axis. Returns the image and
    its size in metres (across, along).
    """
    lin, paper, _ = albedo(path)
    lin, valid = _straighten(lin, ~paper)
    lines = _line_response(lin.mean(axis=2))
    plank_px = plank_width / WORK_M_PER_PX
    seams = _lattice(np.nan_to_num(_row_profile(lines, valid)), 0.85 * plank_px, 1.15 * plank_px)
    pieces = []  # lengths of single plank, each a full plank width tall
    for top, bottom in zip(seams, seams[1:], strict=False):
        strip = lin[top + 3 : bottom - 2]
        ok = valid[top + 3 : bottom - 2].all(axis=0)
        # Cut at the real end joints too, so no piece carries the stub of
        # the next plank into the texture as a sliver.
        ends = cv2.morphologyEx(strip.mean(axis=2), cv2.MORPH_BLACKHAT,
                                cv2.getStructuringElement(cv2.MORPH_RECT, (9, 1)))
        across = np.median(ends, axis=0)
        level = np.median(across) + 6 * np.median(np.abs(across - np.median(across)))
        for x in np.flatnonzero(across > level):
            ok[max(0, x - 4) : x + 5] = False
        runs = np.flatnonzero(np.diff(np.concatenate([[0], ok.astype(int), [0]])))
        for a, b in zip(runs[::2], runs[1::2], strict=True):
            if (b - a) * WORK_M_PER_PX >= 0.3:
                pieces.append(strip[:, a:b])
    if len(pieces) < 3:
        raise ValueError(f"Too few whole planks found in {path.name}")

    rng = np.random.default_rng(seed)
    out_w, row_h = round(length * px_per_m), round(plank_width * px_per_m)
    texture = np.zeros((rows * row_h, out_w, 3), np.float32)
    recent: list[int] = []  # so the same plank never lies next to itself
    lengths = [round(p.shape[1] * px_per_m * WORK_M_PER_PX) for p in pieces]
    for r in range(rows):
        x = 0
        while x < out_w:
            # How much plank to lay next: a random length, or all that is
            # left of the row if that would leave a sliver at its end.
            want = int(rng.uniform(0.45, 1.3) * px_per_m)
            if out_w - x - want < 0.3 * px_per_m:
                want = out_w - x
            long_enough = [i for i, p in enumerate(lengths) if p >= want and i not in recent[-6:]]
            if long_enough:
                choice = int(rng.choice(long_enough))
            else:
                choice = max(range(len(pieces)), key=lambda i: lengths[i])
                want = min(want, lengths[choice])
            recent.append(choice)
            piece = pieces[choice]
            if rng.random() < 0.5:
                piece = piece[:, ::-1]
            if rng.random() < 0.5:
                piece = piece[::-1]
            piece = cv2.resize(piece, (lengths[choice], row_h), interpolation=cv2.INTER_AREA)
            take = min(want, piece.shape[1], out_w - x)
            start = int(rng.integers(0, piece.shape[1] - take + 1))
            texture[r * row_h : (r + 1) * row_h, x : x + take] = piece[:, start : start + take]
            texture[r * row_h : (r + 1) * row_h, x : x + 2] *= 0.6  # end joint
            x += take
        texture[r * row_h : r * row_h + 2] *= 0.6  # long joint
    texture = np.ascontiguousarray(np.rot90(texture))  # planks along the vertical axis
    image = Image.fromarray((linear_to_srgb(texture) * 255).astype(np.uint8))
    return image, (rows * plank_width, length)


def tile_texture(path: Path, tile: tuple[float, float], offset: float, joint: float = 0.003,
                 px_per_m: int = 800, seed: int = 0) -> tuple[Image.Image, tuple[float, float]]:
    """
    A seamless tiled floor: tiles of `tile` (long, short) metres laid in rows
    offset by `offset`, each filled from a whole tile face in the photo, with
    grout joints. Returns the image and its size in metres (across, along).
    """
    lin, paper, _ = albedo(path)
    lin, valid = _straighten(lin, ~paper)
    lum = cv2.GaussianBlur(lin.mean(axis=2), (0, 0), 1.5)  # the surface's grain is not a joint
    short_px = tile[1] / WORK_M_PER_PX
    rows = _lattice(np.nan_to_num(_row_profile(_line_response(lum), valid)),
                    0.85 * short_px, 1.15 * short_px)
    across = cv2.morphologyEx(lum, cv2.MORPH_BLACKHAT,
                              cv2.getStructuringElement(cv2.MORPH_RECT, (9, 1)))
    inset = round(0.02 / WORK_M_PER_PX)
    faces = []  # tile faces, joints and their shadows trimmed off
    # The rows at the photo's edges are cut short but still good surface.
    edges = [0, *rows, lum.shape[0]]
    for top, bottom in zip(edges, edges[1:], strict=False):
        if (bottom - top) * WORK_M_PER_PX < 0.15:
            continue
        band = slice(top + inset, bottom - inset)
        ok = valid[band].all(axis=0)
        profile = np.median(across[band], axis=0)
        usable = profile[ok]
        if usable.size == 0:
            continue
        level = np.median(usable) + 10 * np.median(np.abs(usable - np.median(usable)))
        for x in np.flatnonzero(ok & (profile > level)):
            ok[max(0, x - inset) : x + inset + 1] = False
        runs = np.flatnonzero(np.diff(np.concatenate([[0], ok.astype(int), [0]])))
        for x0, x1 in zip(runs[::2], runs[1::2], strict=True):
            if (x1 - x0) * WORK_M_PER_PX >= 0.12:
                faces.append(lin[band, x0:x1])
    if not faces:
        raise ValueError(f"No whole tile face found in {path.name}")

    rng = np.random.default_rng(seed)
    long_px, short_px = round(tile[0] * px_per_m), round(tile[1] * px_per_m)  # output size
    period = round(tile[0] / offset) if offset else 1
    texture = np.zeros((period * short_px, long_px, 3), np.float32)
    grout = np.median(lin[valid], axis=0) * 0.55
    for r in range(period):
        shift = round(r * offset * px_per_m) % long_px
        face = cv2.resize(faces[(r + int(rng.integers(len(faces)))) % len(faces)],
                          (long_px, short_px), interpolation=cv2.INTER_CUBIC)
        if rng.random() < 0.5:
            face = face[:, ::-1]
        face = np.roll(face, shift, axis=1)
        face[:, shift : shift + max(1, round(joint * px_per_m))] = grout
        face[: max(1, round(joint * px_per_m))] = grout
        texture[r * short_px : (r + 1) * short_px] = face
    image = Image.fromarray((linear_to_srgb(texture) * 255).astype(np.uint8))
    return image, (tile[0], period * tile[1])


def strip_colours(path: Path, count: int = 400, seed: int = 0) -> np.ndarray:
    """
    The colours of a strip floor's strips in a photo of it with an A4 sheet:
    the floor rectified (albedo) and white-balanced on the sheet channel by
    channel, sampled in 4 cm squares, what is not floor (a shoe, a rug, the
    sheet's shadow) dropped as far from the floor's own colours. Linear RGB.
    """
    lin, grown, _ = albedo(path)
    rgb = (linear_to_srgb(np.clip(lin, 0, 1)) * 255).astype(np.uint8)
    mask, _ = find_paper(rgb.astype(np.float32) / 255)
    white = np.median(lin[cv2.erode(mask.astype(np.uint8), np.ones((15, 15))) > 0], axis=0)
    lin = lin * (white.mean() / np.maximum(white, 1e-6))
    step = round(0.04 / WORK_M_PER_PX)
    rng = np.random.default_rng(seed)
    ys, xs = np.nonzero(~grown)
    picks = rng.choice(len(ys), size=min(len(ys), count * 4), replace=False)
    samples = []
    for i in picks:
        y, x = ys[i], xs[i]
        cell = grown[y : y + step, x : x + step]
        if cell.shape != (step, step) or cell.any():
            continue
        samples.append(np.median(lin[y : y + step, x : x + step].reshape(-1, 3), axis=0))
    samples = np.array(samples)
    log = np.log(np.maximum(samples, 1e-4))
    far = np.linalg.norm(log - np.median(log, axis=0), axis=1)
    return samples[far < np.percentile(far, 80)][:count]


def _oak_grain(rng, length_px: int, width_px: int, px_per_m: int) -> np.ndarray:
    """
    One oak strip's shading (about 1), along x: where the saw cut the growth
    rings. Most strips are plain-sawn, the board's face tangent to the rings,
    so they show as nested arches (cathedrals) drawn out along it; the rest
    rift-sawn, fine lines straight along it with the odd silver fleck of a
    ray. Each ring's latewood a dark band, pores in it, fibres over all. The
    straight lines alone (the first take) read as laminate (0064, 0091).
    """
    u = np.arange(length_px, dtype=np.float32)[None, :] / px_per_m
    v = np.arange(width_px, dtype=np.float32)[:, None] / px_per_m
    width, length = width_px / px_per_m, length_px / px_per_m
    wobble = (_tiling_noise(rng, (2, 4), max(width_px, length_px))
              [:width_px, :length_px] - 0.5) * 0.0012
    spacing = rng.uniform(0.0022, 0.0042)
    plain = rng.random() < 0.65
    if plain:
        # Ring radius on the face: a cylinder of radius R, the face cut at a
        # slant along it, so each ring crosses it in an arch.
        radius = rng.uniform(0.04, 0.14)
        centre = rng.uniform(-0.3, 1.3) * width
        slant = rng.uniform(0.01, 0.03) * rng.choice([-1.0, 1.0])
        r = (v - centre) ** 2 / (2 * radius) + slant * (u - rng.uniform(0, length)) + wobble
    else:
        r = v * rng.uniform(0.9, 1.4) + 0.004 * u + wobble
    phase = r / spacing
    frac = phase - np.floor(phase)
    latewood = np.exp(-(((frac - 0.82) / 0.07) ** 2))
    # Fibres: fine streaks along the strip, a little tone drifting along it.
    fibres = cv2.resize(_tiling_noise(rng, (80, 6), 256), (length_px, width_px),
                        interpolation=cv2.INTER_LINEAR)
    drift = cv2.resize(_tiling_noise(rng, (2, 3), 64), (length_px, width_px),
                       interpolation=cv2.INTER_LINEAR)
    pores = (rng.random((width_px, length_px)) > 0.985).astype(np.float32)
    pores = cv2.GaussianBlur(pores, (0, 0), sigmaX=1.6, sigmaY=0.4) * 2.5 * (0.3 + latewood)
    shade = (1 - 0.2 * latewood + 0.08 * (fibres - 0.5) + 0.1 * (drift - 0.5)
             - 0.12 * np.clip(pores, 0, 1))
    if not plain:
        rays = (rng.random((width_px, length_px)) > 0.9992).astype(np.float32)
        rays = cv2.GaussianBlur(rays, (0, 0), sigmaX=6.0, sigmaY=0.8) * 12
        shade += 0.08 * np.clip(rays, 0, 1)
    knot = rng.random() < 0.06
    if knot:
        ky, kx = rng.uniform(0.2, 0.8) * width_px, rng.uniform(0.1, 0.9) * length_px
        yy, xx = np.mgrid[0:width_px, 0:length_px]
        d = np.hypot((yy - ky) / 1.0, (xx - kx) / 1.8) / (0.004 * px_per_m)
        shade *= 1 - 0.45 * np.exp(-(d ** 2))
    return shade.astype(np.float32)


def parquet_texture(colours: np.ndarray, colour: str | None = None, strip: float = 0.068,
                    strips: int = 3, length: float = 2.2, boards: int = 3, px_per_m: int = 1200,
                    seed: int = 0) -> tuple[Image.Image, tuple[float, float]]:
    """
    A seamless 3-strip parquet (the flat's oak, flat-02): boards of `strips`
    strips `strip` wide, each strip row of a board cut into lengths of 30 to
    65 cm, the boards' ends staggered by a third, each strip its own tone
    drawn from `colours` (strip_colours) with oak's grain along it: fibres,
    growth rings and pores. `colour` (sRGB hex) moves the tones' median to
    it: under a night's lamps the sheet is blown out and cannot balance them.
    Strips run along x. Returns the image and its size in metres (along, across).
    """
    rng = np.random.default_rng(seed)
    out_l, strip_px = round(length * px_per_m), round(strip * px_per_m)
    rows = boards * strips
    tex = np.zeros((rows * strip_px, out_l, 3), np.float32)
    median = np.median(colours, axis=0)
    if colour is not None:
        hexcode = colour.lstrip("#")
        target = srgb_to_linear(np.array([int(hexcode[i : i + 2], 16) / 255 for i in (0, 2, 4)]))
        colours = colours * (target / median)
        median = target
    for b in range(boards):
        offset = round(b * out_l / boards)
        for r in range(strips):
            y0 = (b * strips + r) * strip_px
            x = 0
            cuts = []
            while x < out_l:
                piece = round(rng.uniform(0.3, 0.65) * px_per_m)
                if out_l - x - piece < 0.25 * px_per_m:
                    piece = out_l - x
                cuts.append((x, piece))
                x += piece
            for x0, piece in cuts:
                # Its own lightness, the hue barely moved: a sample in shadow
                # came out grey, and oak strips differ in light, not in hue.
                ratio = colours[rng.integers(len(colours))] / median
                light = float(ratio.mean())
                tone = median * light ** 0.8 * (ratio / light) ** 0.25
                shade = _oak_grain(rng, piece, strip_px, px_per_m)
                block = tone[None, None, :] * shade[..., None]
                xs = (np.arange(piece) + x0 + offset) % out_l
                tex[y0 : y0 + strip_px, xs] = block
                # the end joint: a dark hairline with a soft shadow
                tex[y0 : y0 + strip_px, xs[:2]] *= 0.55
                tex[y0 : y0 + strip_px, xs[2:4]] *= 0.85
            tex[y0 : y0 + 1] *= 0.6 if r else 0.45  # long joints, the board's deeper
            tex[y0 + 1 : y0 + 2] *= 0.85
    image = Image.fromarray((linear_to_srgb(np.clip(tex, 0, 1)) * 255).astype(np.uint8))
    return image, (length, rows * strip)


def mean_albedo(path: Path) -> str:
    """A photo's floor colour as #rrggbb, white-balanced on the paper."""
    lin, paper, _ = albedo(path)
    c = linear_to_srgb(np.median(lin[~paper], axis=0)) * 255
    r, g, b = (int(round(v)) for v in c)
    return f"#{r:02x}{g:02x}{b:02x}"


# --------------------------------------------------------------------------
# Views out of windows
# --------------------------------------------------------------------------


def sky_mask(rgb: np.ndarray) -> np.ndarray:
    """
    Where a photo out of a window shows sky: the blue or bright, unsaturated
    region joined to its top edge (rgb as floats 0..1). Contrails, birds and
    specks inside the sky count as sky.
    """
    hsv = cv2.cvtColor(rgb.astype(np.float32), cv2.COLOR_RGB2HSV)  # H in degrees, S, V 0..1
    hue, sat, val = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    sky = ((hue > 185) & (hue < 250) & (val > 0.45)) | ((sat < 0.12) & (val > 0.82))
    sky = cv2.morphologyEx(sky.astype(np.uint8), cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    _, labels = cv2.connectedComponents(sky)
    touching = set(np.unique(labels[0][sky[0] > 0]).tolist())
    mask = np.isin(labels, list(touching)) & (sky > 0)
    mask = cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    return mask > 0


def window_view(path: Path, crop: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0),
                max_side: int = 1600) -> tuple[Image.Image, str, str, str]:
    """
    The customer's photo out of a window with its sky cut out; the sky's
    colour at the top of the cut and at its foot; and the colour along the
    photo's bottom edge, for what lies below what the photo shows.

    Shown whole on a plane behind the glass, a photo's edges and its own sky
    read as a picture hung outside, and from an angle the plane ran out into
    blank background. Cut out, the buildings stand on their own in front of
    a sky dome painted in the photo's sky colours, which goes on past them.
    The sky is the blue or bright, unsaturated region joined to the top edge.
    `crop` (left, top, right, bottom, as fractions) cuts away the window's own
    frame and glare where the shot was taken through the glass.
    """
    image = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    w, h = image.size
    image = image.crop((round(crop[0] * w), round(crop[1] * h),
                        round(crop[2] * w), round(crop[3] * h)))
    image.thumbnail((max_side, max_side))
    rgb = np.asarray(image, np.float32) / 255
    mask = sky_mask(rgb).astype(np.uint8)
    alpha = 1.0 - cv2.GaussianBlur(mask.astype(np.float32), (0, 0), 1.5)
    rows = np.flatnonzero(mask.any(axis=1))
    lin = srgb_to_linear(rgb)

    def colour(row_lo: int, row_hi: int) -> str:
        band = mask[row_lo:row_hi] > 0
        c = np.median(lin[row_lo:row_hi][band], axis=0) if band.any() else np.array([0.5, 0.6, 0.8])
        r, g, b = (int(round(v)) for v in linear_to_srgb(c) * 255)
        return f"#{r:02x}{g:02x}{b:02x}"

    if rows.size:
        top, bottom = int(rows[0]), int(rows[-1]) + 1
        span = max(1, (bottom - top) // 10)
        zenith, horizon = colour(top, top + span), colour(bottom - span, bottom)
    else:
        zenith, horizon = "#8fb0d6", "#dfe8f0"
    foot = max(1, lin.shape[0] // 12)
    below = lin[-foot:][mask[-foot:] == 0]
    c = np.median(below, axis=0) if below.size else np.array([0.5, 0.45, 0.4])
    r, g, b = (int(round(v)) for v in linear_to_srgb(c) * 255)
    rgba = np.dstack([rgb, alpha]) * 255
    image = Image.fromarray(rgba.astype(np.uint8), "RGBA")
    return image, zenith, horizon, f"#{r:02x}{g:02x}{b:02x}"


# --------------------------------------------------------------------------
# Generated surfaces: veneer, worktop, fabric
# --------------------------------------------------------------------------


def _tiling_noise(rng, cells: tuple[int, int], px: int) -> np.ndarray:
    """Smooth value noise on a px x px tile that wraps seamlessly; cells (across y, along x)."""
    grid = rng.random(cells).astype(np.float32)
    padded = np.pad(grid, 2, mode="wrap")
    scale_y, scale_x = px / cells[0], px / cells[1]
    big = cv2.resize(padded, (round(padded.shape[1] * scale_x), round(padded.shape[0] * scale_y)),
                     interpolation=cv2.INTER_CUBIC)
    oy, ox = round(2 * scale_y), round(2 * scale_x)
    return big[oy : oy + px, ox : ox + px]


def _paisley(colour: str, px: int, rng) -> Image.Image:
    """
    flat-02's duvet (0109, 0140): paisleys, small flowers, leaves and vines in
    `colour` and a paler tint of it on white, one metre, seamless. Cut from
    the photo it came out as grey granite (its creases and shade baked in,
    its print too fine); drawn, it is a print. Drawn at twice the size and
    scaled down, so the lines stay smooth; every shape is drawn again a tile
    over, so what crosses an edge comes in at the other.
    """
    from PIL import ImageDraw

    big = 2 * px
    ink = tuple(int(colour.lstrip("#")[i : i + 2], 16) for i in (0, 2, 4))
    ground = (242, 242, 244)
    pale = tuple(round(0.45 * c + 0.55 * g) for c, g in zip(ink, ground, strict=True))
    image = Image.new("RGB", (big, big), ground)
    draw = ImageDraw.Draw(image)
    t = np.linspace(0, 2 * np.pi, 96, endpoint=False)
    # A teardrop, its tip at y = -1 and its round end at y = 1.
    tear = np.stack([0.75 * np.sin(t) * np.sin(t / 2) ** 2.5, -np.cos(t)], axis=1)

    def place(points, centre, size, angle):
        c, s = np.cos(angle), np.sin(angle)
        p = points * size / 2
        return np.stack([c * p[:, 0] - s * p[:, 1], s * p[:, 0] + c * p[:, 1]], axis=1) + centre

    def shape(points, fill=None, outline=None, width=0):
        for dx in (-big, 0, big):
            for dy in (-big, 0, big):
                draw.polygon([(x + dx, y + dy) for x, y in points], fill=fill,
                             outline=outline, width=width)

    def dot(centre, r, fill):
        for dx in (-big, 0, big):
            for dy in (-big, 0, big):
                x, y = centre[0] + dx, centre[1] + dy
                draw.ellipse((x - r, y - r, x + r, y + r), fill=fill)

    line = max(2, big // 400)
    for _ in range(40):  # vines
        start = rng.random(2) * big
        heading, bend = rng.uniform(0, 2 * np.pi), rng.uniform(-2.5, 2.5)
        steps = np.linspace(0, 1, 30)[:, None]
        angle = heading + bend * steps
        pts = start + np.cumsum(np.hstack([np.cos(angle), np.sin(angle)]) * big * 0.012, axis=0)
        for dx in (-big, 0, big):
            for dy in (-big, 0, big):
                draw.line([(x + dx, y + dy) for x, y in pts], fill=pale, width=line)
    for _ in range(24):  # paisleys, 12 to 20 cm long
        centre, size = rng.random(2) * big, big * rng.uniform(0.12, 0.2)
        angle, curl = rng.uniform(0, 2 * np.pi), rng.choice([-1.0, 1.0]) * 0.7
        a = curl * np.clip(-tear[:, 1], 0, None) ** 2
        curled = np.stack([np.cos(a) * tear[:, 0] - np.sin(a) * tear[:, 1],
                           np.sin(a) * tear[:, 0] + np.cos(a) * tear[:, 1]], axis=1)
        for k, (scale, fill) in enumerate(((1.0, pale), (0.8, ground), (0.6, pale), (0.36, ink))):
            shape(place(curled * scale + [0, 1 - scale], centre, size, angle), fill=fill,
                  outline=ink, width=line + (2 if k == 0 else 0))
        for x, y in place(curled * 1.12 + [0, -0.12], centre, size, angle)[::5]:
            dot((x, y), line * 1.6, ink)
    for _ in range(80):  # flowers
        centre, r = rng.random(2) * big, big * rng.uniform(0.006, 0.012)
        for k in range(6):
            a = k * np.pi / 3
            dot(centre + 1.7 * r * np.array([np.cos(a), np.sin(a)]), r, ink if k % 2 else pale)
        dot(centre, r * 0.8, ink)
    for _ in range(130):  # leaves
        shape(place(tear, rng.random(2) * big, big * rng.uniform(0.025, 0.045),
                    rng.uniform(0, 2 * np.pi)), fill=ink if rng.random() < 0.5 else pale)
    return image.resize((px, px), Image.LANCZOS)


def surface_texture(kind: str, colour: str, px: int = 1024, seed: int = 0) -> Image.Image:
    """
    A seamless texture for one metre of a surface, around its colour:
    `veneer` (oak: long fibres and growth bands along x; `oak-fine` the
    same, even and faint, for a worktop), `stone` (a
    concrete-look worktop: a fine sandy grain over faint clouds, the odd
    pore), `boucle` (nubbly fabric), `rug` (a distressed streaky pile),
    `linen` (bedding: soft creases over a fine weave) or `paisley` (a
    printed duvet, `colour` its ink on white).
    Plain colour read as plastic on big oak and fabric surfaces; this is
    the grain a camera sees at walking distance, not a scan of the piece.
    """
    rng = np.random.default_rng(seed)
    if kind in ("veneer", "oak-fine"):
        fibres = (0.5 * _tiling_noise(rng, (256, 4), px) + 0.3 * _tiling_noise(rng, (96, 8), px)
                  + 0.2 * _tiling_noise(rng, (24, 16), px))
        warp = _tiling_noise(rng, (4, 2), px)
        y = np.arange(px, dtype=np.float32)[:, None] / px
        bands = 0.5 + 0.5 * np.sin(2 * np.pi * (y * 9 + 0.35 * warp))
        pores = (_tiling_noise(rng, (512, 32), px) > 0.82).astype(np.float32)
        if kind == "oak-fine":
            # A kitchen worktop's oak (0079): an even, fine grain, its growth
            # bands faint. As veneer it read as orange streaks.
            value, spread = 0.82 * fibres + 0.12 * bands - 0.06 * pores, 0.07
        else:
            value, spread = 0.65 * fibres + 0.35 * bands - 0.12 * pores, 0.14
    elif kind == "stone":
        # Soft mottling alone read as a blur up close (the owner's island):
        # the grain is what makes it read as stone.
        grain = rng.standard_normal((px, px)).astype(np.float32)
        grain = (grain + np.roll(grain, 1, 0) + np.roll(grain, 1, 1)) / 3
        pores = (rng.random((px, px)) > 0.9985).astype(np.float32)
        value = (0.55 * grain / grain.std() + 0.3 * _tiling_noise(rng, (160, 160), px)
                 + 0.35 * _tiling_noise(rng, (5, 5), px) - 1.5 * pores)
        spread = 0.08
    elif kind == "boucle":
        value = 0.7 * _tiling_noise(rng, (300, 300), px) + 0.3 * _tiling_noise(rng, (40, 40), px)
        spread = 0.18
    elif kind == "linen":
        # Made bedding at walking distance: a fine weave and a few soft broad
        # creases over it. Ridged folds over the whole cloth (the first take)
        # read as wind-blown sand on flat-02's mattresses.
        weave = _tiling_noise(rng, (512, 512), px)
        creases = 1 - np.abs(2 * _tiling_noise(rng, (3, 5), px) - 1)
        value = weave / weave.std() + 0.8 * creases / creases.std()
        spread = 0.035
    elif kind == "rug":
        # A distressed modern rug (0084, 0091): dark and pale streaks along
        # its length, thicker in cloudy patches, the pile's fibre over all.
        # Cut from the photo, the patch caught the floor and a chair's foot.
        streaks = _tiling_noise(rng, (260, 6), px) - 0.5
        patches = _tiling_noise(rng, (5, 4), px)
        fibre = _tiling_noise(rng, (500, 500), px) - 0.5
        value = 1.4 * streaks * (0.3 + patches) + 0.5 * (patches - 0.5) + 0.25 * fibre
        spread = 0.32
    elif kind == "paisley":
        return _paisley(colour, px, rng)
    else:
        raise ValueError(f"Unknown surface texture {kind!r}")
    value = (value - value.mean()) / max(float(value.std()), 1e-6)
    hexcode = colour.lstrip("#")
    base = srgb_to_linear(np.array([int(hexcode[i : i + 2], 16) / 255 for i in (0, 2, 4)]))
    lin = base[None, None, :] * (1 + spread * np.clip(value, -2.5, 2.5))[..., None] / 1.0
    return Image.fromarray((linear_to_srgb(lin) * 255).astype(np.uint8))


# --------------------------------------------------------------------------
# The neighbourhood: Viennese facades and tiled roofs
# --------------------------------------------------------------------------

BAY_M, STOREY_M = 3.2, 3.7  # one window bay, one floor of a Gründerzeit house


def photo_picture(path: Path, corners: list, size_m: tuple[float, float],
                  wall: tuple[float, float, float, float] | None = None, cutout: bool = False,
                  px_per_m: int = 1600) -> Image.Image:
    """
    A painting, print or the clock cut from a photo of the room: the four
    `corners` (fractions of the photo, clockwise from top left) mapped to a
    rectangle `size_m` across and down at `px_per_m`, white-balanced channel
    by channel on the `wall` box (white wall in the same light, 85%).
    `cutout`: an RGBA image whose alpha keeps what is darker than the wall.
    """
    image = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    rgb = np.asarray(image, np.float32) / 255
    h, w = rgb.shape[:2]
    src = np.array([[x * w, y * h] for x, y in corners], np.float32)
    out_w, out_h = round(size_m[0] * px_per_m), round(size_m[1] * px_per_m)
    dst = np.array([[0, 0], [out_w, 0], [out_w, out_h], [0, out_h]], np.float32)
    flat = cv2.warpPerspective(rgb, cv2.getPerspectiveTransform(src, dst), (out_w, out_h),
                               flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
    lin = srgb_to_linear(np.clip(flat, 0, 1))
    white = None
    if wall is not None:
        x0, y0, x1, y1 = wall
        patch = srgb_to_linear(rgb[round(y0 * h) : round(y1 * h), round(x0 * w) : round(x1 * w)])
        white = np.median(patch.reshape(-1, 3), axis=0)
        lin = lin * (0.85 / np.maximum(white, 1e-6))
    out = linear_to_srgb(np.clip(lin, 0, 1))
    if not cutout:
        return Image.fromarray((out * 255).astype(np.uint8))
    lum = lin.mean(axis=2)
    alpha = np.clip((0.55 - lum) / 0.2, 0, 1)
    alpha = cv2.GaussianBlur(alpha, (0, 0), 1.0)
    rgba = np.dstack([out, alpha])
    return Image.fromarray((rgba * 255).astype(np.uint8), "RGBA")


def tile_surface(colour: str, tile: tuple[float, float] = (0.6, 0.3), joint: float = 0.0025,
                 px_per_m: int = 1024, seed: int = 0) -> tuple[Image.Image, tuple[float, float]]:
    """
    Large stone-look tiles (flat-02's bathroom, 0131, 0138): `tile` (long,
    short) metres in rows offset by half a tile, each a shade of its own, of
    the concrete-look `stone` grain round `colour`, grey joints between them.
    Seamless over a square of whole tiles; returns it and its size in metres.
    """
    rng = np.random.default_rng(seed)
    long_m, short_m = tile
    rows = 2 * max(1, round(long_m / short_m))
    size = rows * short_m
    px = round(size * px_per_m)
    stone = np.asarray(surface_texture("stone", colour, px=px, seed=seed), np.float32) / 255
    lin = srgb_to_linear(stone)
    # The photos' stone clouds softly, lighter and darker over a hand's width.
    clouds = 0.6 * _tiling_noise(rng, (7, 7), px) + 0.4 * _tiling_noise(rng, (19, 19), px)
    lin *= (1 + 0.14 * (clouds - clouds.mean()))[..., None]
    long_px, short_px = long_m * px_per_m, short_m * px_per_m
    width = max(2, round(joint * px_per_m))
    for r in range(rows):
        y0, y1 = round(r * short_px), round((r + 1) * short_px)
        shift = (r % 2) * long_px / 2
        starts = [round(shift + k * long_px) % px for k in range(round(size / long_m) + 1)]
        for x in starts:
            # Each tile its own shade, very slightly.
            tone = 1 + rng.normal(0, 0.025)
            span = (np.arange(round(long_px)) + x) % px
            lin[y0:y1, span] *= tone
            lin[y0:y1, span[:width]] *= 0.72
        lin[y0 : y0 + width] *= 0.72
    image = Image.fromarray((linear_to_srgb(np.clip(lin, 0, 1)) * 255).astype(np.uint8))
    return image, (size, size)


def facade_texture(colour: str, px_per_m: int = 160, seed: int = 0) -> Image.Image:
    """
    One bay of one floor of a Viennese apartment house, tiling sideways and
    upwards: rendered plaster in `colour`, a tall double window with white
    frames, its surround, a sill and a hood moulding, and the floor's cornice
    band. Its bottom edge is a floor level.
    """
    rng = np.random.default_rng(seed)
    w, h = round(BAY_M * px_per_m), round(STOREY_M * px_per_m)

    def box(img, x0, y0, x1, y1, c):  # metres, y up from the floor line
        img[h - round(y1 * px_per_m) : h - round(y0 * px_per_m),
            round(x0 * px_per_m) : round(x1 * px_per_m)] = c

    hexcode = colour.lstrip("#")
    plaster = srgb_to_linear(np.array([int(hexcode[i : i + 2], 16) / 255 for i in (0, 2, 4)]))
    light, dark = np.minimum(plaster * 1.12, 1.0), plaster * 0.78
    frame, glass_top, glass_low = (srgb_to_linear(np.array(c)) for c in
                                   ((0.91, 0.90, 0.88), (0.36, 0.40, 0.45), (0.17, 0.19, 0.22)))
    img = np.empty((h, w, 3), np.float32)
    img[:] = plaster
    cx, ww, wh, sill = BAY_M / 2, 1.25, 2.05, 0.95
    # The window's surround, the hood moulding over it, its sill, its frame.
    box(img, cx - ww / 2 - 0.12, sill - 0.04, cx + ww / 2 + 0.12, sill + wh + 0.12, light)
    box(img, cx - ww / 2 - 0.25, sill + wh + 0.2, cx + ww / 2 + 0.25, sill + wh + 0.34, light)
    box(img, cx - ww / 2 - 0.25, sill + wh + 0.17, cx + ww / 2 + 0.25, sill + wh + 0.2, dark)
    box(img, cx - ww / 2 - 0.18, sill - 0.1, cx + ww / 2 + 0.18, sill - 0.02, light)
    box(img, cx - ww / 2, sill, cx + ww / 2, sill + wh, frame)
    # The panes: dark glass catching a little sky at the top.
    for x0, x1 in ((cx - ww / 2 + 0.07, cx - 0.03), (cx + 0.03, cx + ww / 2 - 0.07)):
        for y0, y1 in ((sill + 0.07, sill + wh * 0.68), (sill + wh * 0.72, sill + wh - 0.07)):
            r0, r1 = h - round(y1 * px_per_m), h - round(y0 * px_per_m)
            t = np.linspace(1, 0, r1 - r0)[:, None]
            img[r0:r1, round(x0 * px_per_m) : round(x1 * px_per_m)] = (
                glass_low + (glass_top - glass_low) * t[..., None] ** 2)
    box(img, 0, STOREY_M - 0.2, BAY_M, STOREY_M - 0.12, light)  # cornice band
    box(img, 0, STOREY_M - 0.12, BAY_M, STOREY_M - 0.09, dark)
    grain = cv2.GaussianBlur(rng.normal(0, 1, (h, w)).astype(np.float32), (0, 0), 1.2)
    img *= (1 + 0.035 * grain)[..., None]
    return Image.fromarray((linear_to_srgb(img) * 255).astype(np.uint8))


def roof_tile_texture(colour: str, px: int = 512, seed: int = 0) -> Image.Image:
    """One metre of a clay tile roof in `colour`: courses 0.18 m apart, tiles 0.25 m wide,
    each a little different; courses run along x."""
    rng = np.random.default_rng(seed)
    hexcode = colour.lstrip("#")
    base = srgb_to_linear(np.array([int(hexcode[i : i + 2], 16) / 255 for i in (0, 2, 4)]))
    courses, per_course = 6, 4  # 1 m / 0.167 m and 1 m / 0.25 m, so it tiles
    img = np.empty((px, px, 3), np.float32)
    ch, tw = px // courses, px // per_course
    for r in range(courses):
        shift = (tw // 2) * (r % 2)
        for c in range(per_course + 1):
            tone = base * rng.uniform(0.8, 1.15) * np.array([1, rng.uniform(0.92, 1.05), 1])
            x0 = (c * tw + shift) % px
            for x in range(x0, x0 + tw):
                img[r * ch : (r + 1) * ch, x % px] = tone
        rows = np.linspace(1.0, 0.7, ch)[:, None, None]  # each course darkens to its lower lap
        img[r * ch : (r + 1) * ch] *= rows
    return Image.fromarray((linear_to_srgb(img) * 255).astype(np.uint8))


# --------------------------------------------------------------------------
# Materials cut from the capture photos
# --------------------------------------------------------------------------


def photo_patch(path: Path, box: tuple[float, float, float, float], size_m: tuple[float, float],
                wall: tuple[float, float, float, float] | None = None,
                colour: str | None = None, rotate: bool = False,
                px_per_m: int = 600) -> tuple[Image.Image, tuple[float, float]]:
    """
    A seamless texture of a material cut from a capture photo: the patch
    `box` (fractions of the photo: left, top, right, bottom), `size_m` across
    and down in reality. Light falloff across the patch is divided out; its
    brightness is set against `wall`, a patch of white wall in the same light
    (taken as 85% albedo), or else to `colour`. `rotate` turns it a quarter,
    so a grain that runs down the photo runs along x, as box UVs expect.
    Returns the image and the metres it spans (x, y).
    """
    image = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    rgb = srgb_to_linear(np.asarray(image, np.float32) / 255)
    h, w = rgb.shape[:2]

    def cut(b):
        return rgb[round(b[1] * h) : round(b[3] * h), round(b[0] * w) : round(b[2] * w)]

    patch = cut(box)
    if rotate:
        patch, size_m = np.ascontiguousarray(np.rot90(patch)), (size_m[1], size_m[0])
    out_w, out_h = round(size_m[0] * px_per_m), round(size_m[1] * px_per_m)
    patch = cv2.resize(patch, (out_w, out_h), interpolation=cv2.INTER_AREA)
    lum = patch.mean(axis=2)
    shade = cv2.GaussianBlur(lum, (0, 0), max(out_w, out_h) / 4)
    patch = patch * (lum.mean() / np.maximum(shade, 1e-6))[..., None]
    if wall is not None:
        white = cut(wall).reshape(-1, 3).mean(axis=0)
        patch = patch * (0.85 / max(float(white @ np.array([0.2126, 0.7152, 0.0722])), 1e-6))
    elif colour is not None:
        hexcode = colour.lstrip("#")
        target = srgb_to_linear(np.array([int(hexcode[i : i + 2], 16) / 255 for i in (0, 2, 4)]))
        patch = patch * (target / np.maximum(patch.reshape(-1, 3).mean(axis=0), 1e-6))
    # Seamless: half-shifted copy blended in where the original's edges are,
    # so every edge of the tile continues into its opposite one.
    shifted = np.roll(patch, (out_h // 2, out_w // 2), axis=(0, 1))
    wy = np.sin(np.linspace(0, np.pi, out_h))[:, None] ** 0.6
    wx = np.sin(np.linspace(0, np.pi, out_w))[None, :] ** 0.6
    weight = (wy * wx)[..., None]
    seamless = patch * weight + shifted * (1 - weight)
    return Image.fromarray((linear_to_srgb(seamless) * 255).astype(np.uint8)), size_m
