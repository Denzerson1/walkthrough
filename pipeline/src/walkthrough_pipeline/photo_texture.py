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


def mean_albedo(path: Path) -> str:
    """A photo's floor colour as #rrggbb, white-balanced on the paper."""
    lin, paper, _ = albedo(path)
    c = linear_to_srgb(np.median(lin[~paper], axis=0)) * 255
    r, g, b = (int(round(v)) for v in c)
    return f"#{r:02x}{g:02x}{b:02x}"


# --------------------------------------------------------------------------
# Views out of windows
# --------------------------------------------------------------------------


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
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)  # H in degrees, S and V in 0..1
    hue, sat, val = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    sky = ((hue > 185) & (hue < 250) & (val > 0.45)) | ((sat < 0.12) & (val > 0.82))
    sky = cv2.morphologyEx(sky.astype(np.uint8), cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    _, labels = cv2.connectedComponents(sky)
    touching = set(np.unique(labels[0][sky[0] > 0]).tolist())
    mask = np.isin(labels, list(touching)) & (sky > 0)
    # Contrails, birds and specks inside the sky are sky too.
    mask = cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
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


def surface_texture(kind: str, colour: str, px: int = 1024, seed: int = 0) -> Image.Image:
    """
    A seamless texture for one metre of a surface, around its colour:
    `veneer` (oak: long fibres and growth bands along x), `stone` (a
    concrete-look worktop: soft mottling) or `boucle` (nubbly fabric).
    Plain colour read as plastic on big oak and fabric surfaces; this is
    the grain a camera sees at walking distance, not a scan of the piece.
    """
    rng = np.random.default_rng(seed)
    if kind == "veneer":
        fibres = (0.5 * _tiling_noise(rng, (256, 4), px) + 0.3 * _tiling_noise(rng, (96, 8), px)
                  + 0.2 * _tiling_noise(rng, (24, 16), px))
        warp = _tiling_noise(rng, (4, 2), px)
        y = np.arange(px, dtype=np.float32)[:, None] / px
        bands = 0.5 + 0.5 * np.sin(2 * np.pi * (y * 9 + 0.35 * warp))
        pores = (_tiling_noise(rng, (512, 32), px) > 0.82).astype(np.float32)
        value = 0.65 * fibres + 0.35 * bands - 0.12 * pores
        spread = 0.14
    elif kind == "stone":
        value = 0.6 * _tiling_noise(rng, (12, 12), px) + 0.4 * _tiling_noise(rng, (96, 96), px)
        spread = 0.12
    elif kind == "boucle":
        value = 0.7 * _tiling_noise(rng, (300, 300), px) + 0.3 * _tiling_noise(rng, (40, 40), px)
        spread = 0.18
    else:
        raise ValueError(f"Unknown surface texture {kind!r}")
    value = (value - value.mean()) / max(float(value.std()), 1e-6)
    hexcode = colour.lstrip("#")
    base = srgb_to_linear(np.array([int(hexcode[i : i + 2], 16) / 255 for i in (0, 2, 4)]))
    lin = base[None, None, :] * (1 + spread * np.clip(value, -2.5, 2.5))[..., None] / 1.0
    return Image.fromarray((linear_to_srgb(lin) * 255).astype(np.uint8))
