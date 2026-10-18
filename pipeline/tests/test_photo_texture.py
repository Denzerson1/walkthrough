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
