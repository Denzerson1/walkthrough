"""
Floor-photo processing (M4).

Pure functions on numpy arrays so they can be unit-tested without FastAPI:
perspective rectification, seamless tiling, normal-map generation.
"""

from __future__ import annotations

import cv2
import numpy as np

Corners = list[tuple[float, float]]


def order_corners(corners: Corners) -> np.ndarray:
    """
    Sort four points into top-left, top-right, bottom-right, bottom-left.
    The user drags them in any order, so this must not assume input order.
    """
    pts = np.asarray(corners, dtype=np.float32)
    if pts.shape != (4, 2):
        raise ValueError("expected exactly 4 corners")
    # Top-left has the smallest x+y, bottom-right the largest.
    s = pts.sum(axis=1)
    diff = np.diff(pts, axis=1).ravel()
    return np.array(
        [
            pts[np.argmin(s)],
            pts[np.argmin(diff)],
            pts[np.argmax(s)],
            pts[np.argmax(diff)],
        ],
        dtype=np.float32,
    )


def rectify(image: np.ndarray, corners: Corners, out_size: int = 512) -> np.ndarray:
    """Warp the quad the user marked into a square, head-on tile."""
    src = order_corners(corners)
    dst = np.array(
        [[0, 0], [out_size - 1, 0], [out_size - 1, out_size - 1], [0, out_size - 1]],
        dtype=np.float32,
    )
    matrix = cv2.getPerspectiveTransform(src, dst)
    return cv2.warpPerspective(image, matrix, (out_size, out_size), flags=cv2.INTER_CUBIC)


def propose_quad(image: np.ndarray) -> Corners:
    """
    Suggest a tile region for the user to adjust (step 2 of the M4 flow).

    Finds the largest four-sided contour; falls back to a centred square
    covering 70% of the image when nothing convincing is found.
    """
    h, w = image.shape[:2]
    grey = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    grey = cv2.GaussianBlur(grey, (5, 5), 0)
    edges = cv2.Canny(grey, 50, 150)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1)
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

    best: np.ndarray | None = None
    best_area = 0.0
    min_area = 0.05 * h * w
    for contour in contours:
        peri = cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, 0.02 * peri, True)
        if len(approx) != 4 or not cv2.isContourConvex(approx):
            continue
        area = abs(cv2.contourArea(approx))
        if area > best_area and area > min_area:
            best_area = area
            best = approx.reshape(4, 2)

    if best is None:
        m = 0.15
        return [
            (w * m, h * m),
            (w * (1 - m), h * m),
            (w * (1 - m), h * (1 - m)),
            (w * m, h * (1 - m)),
        ]
    ordered = order_corners([tuple(p) for p in best.astype(float)])
    return [(float(x), float(y)) for x, y in ordered]


def make_seamless(tile: np.ndarray, blend_fraction: float = 0.125) -> np.ndarray:
    """
    Deterministic offset-and-blend, as the brief specifies.

    The tile is rolled by half its size so the former edges meet in the middle,
    then the visible seam cross is blended with a linear ramp. Deterministic:
    the same input always gives the same output.
    """
    if tile.ndim == 2:
        tile = tile[:, :, None]
        squeeze = True
    else:
        squeeze = False

    h, w = tile.shape[:2]
    out = np.roll(np.roll(tile, h // 2, axis=0), w // 2, axis=1).astype(np.float32)

    band_y = max(1, int(h * blend_fraction))
    band_x = max(1, int(w * blend_fraction))

    # Blend across the horizontal seam that now runs through the middle row.
    mid_y = h // 2
    for i in range(band_y):
        alpha = 0.5 * (1.0 - i / band_y)
        top = mid_y - i - 1
        bot = mid_y + i
        if 0 <= top < h and 0 <= bot < h:
            a = out[top].copy()
            b = out[bot].copy()
            out[top] = a * (1 - alpha) + b * alpha
            out[bot] = b * (1 - alpha) + a * alpha

    mid_x = w // 2
    for i in range(band_x):
        alpha = 0.5 * (1.0 - i / band_x)
        left = mid_x - i - 1
        right = mid_x + i
        if 0 <= left < w and 0 <= right < w:
            a = out[:, left].copy()
            b = out[:, right].copy()
            out[:, left] = a * (1 - alpha) + b * alpha
            out[:, right] = b * (1 - alpha) + a * alpha

    result = np.clip(out, 0, 255).astype(np.uint8)
    return result[:, :, 0] if squeeze else result


def tiling_seam_error(tile: np.ndarray) -> float:
    """
    Mean absolute difference between opposite edges, 0-255.

    A seamless tile scores low. Used as the quality check in tests and
    surfaced to the user as a warning when a photo tiles badly.
    """
    arr = tile.astype(np.float32)
    top_bottom = np.abs(arr[0] - arr[-1]).mean()
    left_right = np.abs(arr[:, 0] - arr[:, -1]).mean()
    return float((top_bottom + left_right) / 2)


def normal_from_luminance(tile: np.ndarray, strength: float = 2.0) -> np.ndarray:
    """
    Derive a tangent-space normal map from luminance (brief M4 step 6).

    Not physically correct — luminance is not height — but for floor photos
    the grout lines and grain read as recesses, which is what we want.
    """
    grey = cv2.cvtColor(tile, cv2.COLOR_BGR2GRAY) if tile.ndim == 3 else tile
    grey = cv2.GaussianBlur(grey.astype(np.float32) / 255.0, (0, 0), 1.0)

    dx = cv2.Sobel(grey, cv2.CV_32F, 1, 0, ksize=3)
    dy = cv2.Sobel(grey, cv2.CV_32F, 0, 1, ksize=3)

    nx = -dx * strength
    ny = -dy * strength
    nz = np.ones_like(grey)
    length = np.sqrt(nx**2 + ny**2 + nz**2)
    nx, ny, nz = nx / length, ny / length, nz / length

    # Encode -1..1 into 0..255. OpenCV writes BGR, so pack as (z, y, x).
    normal = np.stack(
        [
            ((nz * 0.5 + 0.5) * 255),
            ((ny * 0.5 + 0.5) * 255),
            ((nx * 0.5 + 0.5) * 255),
        ],
        axis=-1,
    )
    return np.clip(normal, 0, 255).astype(np.uint8)


def default_roughness(tile: np.ndarray, base: float = 0.65) -> np.ndarray:
    """
    Roughness from local contrast: smooth, bright areas read as polished.
    Returns a single-channel 8-bit map.
    """
    grey = cv2.cvtColor(tile, cv2.COLOR_BGR2GRAY) if tile.ndim == 3 else tile
    grey_f = grey.astype(np.float32) / 255.0
    local_var = cv2.GaussianBlur(grey_f**2, (0, 0), 3.0) - cv2.GaussianBlur(grey_f, (0, 0), 3.0) ** 2
    local_var = np.clip(local_var, 0, None)
    texture = np.sqrt(local_var)
    texture = texture / (texture.max() + 1e-6)
    rough = np.clip(base + 0.3 * texture - 0.15, 0.0, 1.0)
    return (rough * 255).astype(np.uint8)


def strip_exif_and_decode(data: bytes) -> np.ndarray:
    """
    Decode an uploaded image. cv2.imdecode keeps only pixels, so EXIF —
    including GPS — is dropped here by construction.
    """
    buf = np.frombuffer(data, dtype=np.uint8)
    image = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("Could not decode the image. Use JPEG or PNG.")
    return image


def encode_png(image: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", image)
    if not ok:  # pragma: no cover - cv2 failure on valid arrays is not expected
        raise ValueError("PNG encoding failed")
    return buf.tobytes()
