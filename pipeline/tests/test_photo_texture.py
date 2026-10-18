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
    for kind in ("veneer", "stone", "boucle"):
        tex = np.asarray(pt.surface_texture(kind, "#a98b6b", px=256)).astype(float)
        assert tex.shape == (256, 256, 3)
        mean = pt.linear_to_srgb(pt.srgb_to_linear(tex / 255).mean(axis=(0, 1))) * 255
        assert np.abs(mean - [0xA9, 0x8B, 0x6B]).max() < 12
        # Seamless: the wrap-around step is no larger than a step inside the tile.
        wrap = np.abs(tex[:, 0] - tex[:, -1]).mean()
        inside = np.abs(np.diff(tex, axis=1)).mean()
        assert wrap < 3 * inside + 1
