"""Floor textures from photos: finding the A4 sheet and the joint lattice."""

from __future__ import annotations

import cv2
import numpy as np
import pytest
from walkthrough_pipeline import photo_texture as pt


def test_find_paper_returns_the_sheets_corners_long_side_first():
    image = np.full((600, 800, 3), 0.35, np.float32)
    # A sheet seen in perspective: wider at the bottom, as from a tilted phone.
    true = np.array([[300, 200], [520, 210], [540, 370], [285, 360]], np.float32)
    cv2.fillPoly(image, [true.astype(np.int32)], (0.95, 0.95, 0.95))
    _, corners = pt.find_paper(image)
    # Same corners, some rotation of the order, long side (top) first.
    for c in true:
        assert np.min(np.linalg.norm(corners - c, axis=1)) < 3
    first = np.linalg.norm(corners[1] - corners[0])
    second = np.linalg.norm(corners[2] - corners[1])
    assert first > second


def test_lattice_finds_evenly_spaced_joints_even_faint_ones():
    rng = np.random.default_rng(0)
    profile = rng.random(400) * 0.2
    joints = np.arange(13, 400, 50)
    for i, j in enumerate(joints):
        profile[j] += 0.5 if i % 2 else 2.0  # every other joint faint
    found = pt._lattice(profile, 40, 60)
    assert found == pytest.approx(list(joints), abs=1)


def test_window_view_cuts_out_the_sky_above_the_buildings(tmp_path):
    from PIL import Image

    photo = np.zeros((400, 300, 3), np.uint8)
    photo[:200] = (120, 160, 220)  # blue sky above
    photo[200:] = (190, 175, 150)  # a facade below
    photo[300:320, 50:100] = (110, 150, 210)  # a window pane reflecting the sky
    path = tmp_path / "view.jpg"
    Image.fromarray(photo).save(path, quality=95)
    image, zenith, horizon, below = pt.window_view(path)
    alpha = np.asarray(image)[..., 3]
    assert alpha[:180].mean() < 10  # the sky is cut away
    assert alpha[220:].mean() > 245  # the facade stays, its blue pane too
    assert zenith.startswith("#") and horizon.startswith("#")
    r, g, b = (int(below[i : i + 2], 16) for i in (1, 3, 5))
    assert r > b  # the facade's warm colour, not the sky's


def test_surface_textures_keep_their_colour_and_tile():
    for kind in ("veneer", "stone", "boucle", "linen", "rug"):
        tex = np.asarray(pt.surface_texture(kind, "#a98b6b", px=256)).astype(float)
        assert tex.shape == (256, 256, 3)
        mean = pt.linear_to_srgb(pt.srgb_to_linear(tex / 255).mean(axis=(0, 1))) * 255
        assert np.abs(mean - [0xA9, 0x8B, 0x6B]).max() < 12
        # Seamless: the wrap-around step is no larger than a step inside the tile.
        wrap = np.abs(tex[:, 0] - tex[:, -1]).mean()
        inside = np.abs(np.diff(tex, axis=1)).mean()
        assert wrap < 3 * inside + 1


def test_paisley_is_ink_on_white_and_tiles():
    tex = np.asarray(pt.surface_texture("paisley", "#33508f", px=256)).astype(float)
    assert tex.shape == (256, 256, 3)
    ground = (tex.min(axis=2) > 230).mean()
    inked = (tex[..., 2] - tex[..., 0] > 40).mean()
    assert 0.4 < ground < 0.9 and inked > 0.05  # mostly white, a good share printed blue
    wrap = np.abs(tex[:, 0] - tex[:, -1]).mean()
    inside = np.abs(np.diff(tex, axis=1)).mean()
    assert wrap < 3 * inside + 1


def test_photo_patch_evens_the_light_sets_brightness_and_tiles(tmp_path):
    # A grained board under light that falls off left to right, next to a
    # white wall in the same light, photographed at half brightness.
    rng = np.random.default_rng(1)
    h, w = 400, 600
    grain = 0.25 + 0.05 * np.sin(np.arange(w) / 3)[None, :] + 0.02 * rng.random((h, w))
    falloff = np.linspace(1.0, 0.5, w)[None, :]
    lin = np.zeros((h, w, 3))
    lin[:, :400] = (grain[:, :400] * falloff[:, :400] * 0.5)[..., None]
    lin[:, 400:] = 0.85 * 0.5 * falloff[:, 400:][..., None]
    photo = tmp_path / "photo.png"
    cv2.imwrite(str(photo), (pt.linear_to_srgb(lin) * 255).astype(np.uint8)[..., ::-1])

    image, size = pt.photo_patch(photo, (0.0, 0.0, 0.6, 1.0), (0.6, 0.4),
                                 wall=(0.7, 0.0, 1.0, 1.0), px_per_m=500)
    assert size == (0.6, 0.4) and image.size == (300, 200)
    tex = np.asarray(image).astype(float)
    lum = pt.srgb_to_linear(tex / 255).mean(axis=2)
    # The falloff is gone: left and right halves are equally bright.
    assert abs(lum[:, :150].mean() - lum[:, 150:].mean()) < 0.02
    # Against the wall the board comes out near its true albedo, not half of it.
    assert 0.2 < lum.mean() < 0.4
    wrap = np.abs(tex[:, 0] - tex[:, -1]).mean()
    assert wrap < 3 * np.abs(np.diff(tex, axis=1)).mean() + 1


def test_parquet_is_seamless_strips_of_the_given_tones():
    colours = np.array([[0.40, 0.25, 0.12], [0.50, 0.32, 0.16], [0.45, 0.28, 0.14]], np.float32)
    image, size = pt.parquet_texture(colours, "#c49c70", px_per_m=400, seed=1)
    a = pt.srgb_to_linear(np.asarray(image, np.float32) / 255)
    assert size == pytest.approx((2.2, 9 * 0.068))
    assert a.shape[:2] == (9 * round(0.068 * 400), round(2.2 * 400))
    # The median tone is the one asked for.
    target = pt.srgb_to_linear(np.array([0xC4, 0x9C, 0x70]) / 255)
    assert np.median(a.reshape(-1, 3), axis=0) == pytest.approx(target, rel=0.15)
    # It wraps without a seam: where it repeats, only the strips that really
    # end there change tone, as at any end joint, not every row at once.
    wrapped = np.hstack([a, a[:, :1]]).mean(axis=2)
    jumps = (np.abs(np.diff(wrapped, axis=1)) > 0.04).mean(axis=0)
    assert jumps[-1] <= jumps.max() and jumps[-1] < 0.5


def test_tile_surface_lays_whole_tiles_with_joints():
    image, size = pt.tile_surface("#c0bcb4", (0.6, 0.3), px_per_m=200)
    a = np.asarray(image, np.float32).mean(axis=2)
    assert size == pytest.approx((1.2, 1.2))
    assert a.shape == (240, 240)
    # Joints every 30 cm down and every 60 cm across, darker than the tiles.
    rows = a.mean(axis=1)
    assert rows[0] < rows[30] and rows[60] < rows[30] and rows[120] < rows[90]
    first_row = a[5:55].mean(axis=0)
    assert first_row[0] < first_row[60] and first_row[120] < first_row[60]


def test_photo_picture_straightens_a_quadrilateral_and_balances_it(tmp_path):
    from PIL import Image

    photo = np.full((400, 400, 3), (230, 220, 200), np.uint8)  # a warm white wall
    quad = np.array([[100, 80], [300, 110], [290, 300], [110, 320]], np.int32)
    cv2.fillPoly(photo, [quad], (40, 90, 160))
    path = tmp_path / "wall.png"
    Image.fromarray(photo).save(path)
    corners = [(x / 400, y / 400) for x, y in quad]
    out = pt.photo_picture(path, corners, (0.5, 0.4), wall=(0.0, 0.0, 0.1, 0.1), px_per_m=200)
    a = np.asarray(out, np.float32)
    assert out.size == (100, 80)
    # Filled edge to edge with the painting, nothing of the wall left in it.
    assert a[5:-5, 5:-5].std(axis=(0, 1)).max() < 3
    # Balanced on the wall: its blue stays blue, warmed no further.
    r, g, b = a[40, 50]
    assert b > g > r


def test_photo_picture_cuts_out_what_is_darker_than_the_wall(tmp_path):
    from PIL import Image

    photo = np.full((200, 200, 3), 235, np.uint8)
    cv2.circle(photo, (100, 100), 60, (20, 20, 20), 6)  # a clock's ring
    path = tmp_path / "clock.png"
    Image.fromarray(photo).save(path)
    out = pt.photo_picture(path, [(0.15, 0.15), (0.85, 0.15), (0.85, 0.85), (0.15, 0.85)],
                           (0.6, 0.6), wall=(0, 0, 0.1, 0.1), cutout=True, px_per_m=200)
    alpha = np.asarray(out)[..., 3]
    assert out.mode == "RGBA"
    assert alpha[60, 60] < 30  # the wall inside the ring: see-through
    assert alpha[60, 9] > 200  # on the ring
