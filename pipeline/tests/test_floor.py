"""Floor detection: projection voting, plane refinement, hulls, shading (M3)."""

from __future__ import annotations

import numpy as np
import pytest
from walkthrough_pipeline.floor import (
    CameraPose,
    concave_hull_2d,
    decode_shading_png,
    encode_shading_png,
    refine_floor,
    render_floor_topdown,
    shading_map,
    simplify_polygon,
    vote_floor,
)


def make_pose(width=64, height=48, at_height=1.6) -> CameraPose:
    """A camera at y=at_height looking down and forward along +Z."""
    # Negative pitch tilts the view down, putting the floor below the optical
    # axis and inside the image.
    pitch = np.radians(-25.0)
    rot = np.array(
        [
            [1, 0, 0],
            [0, np.cos(pitch), -np.sin(pitch)],
            [0, np.sin(pitch), np.cos(pitch)],
        ]
    )
    # world -> camera: rotate, then translate so the camera sits at the origin.
    translation = -rot @ np.array([0.0, at_height, -2.0])
    return CameraPose(
        rotation=rot,
        translation=translation,
        fx=50.0,
        fy=50.0,
        cx=width / 2,
        cy=height / 2,
        width=width,
        height=height,
    )


class TestCameraProjection:
    def test_point_in_front_projects_inside_the_image(self):
        pose = make_pose()
        pixels, valid = pose.project(np.array([[0.0, 0.0, 0.0]]))
        assert valid[0]
        assert 0 <= pixels[0, 0] < pose.width

    def test_point_behind_the_camera_is_invalid(self):
        pose = make_pose()
        _, valid = pose.project(np.array([[0.0, 0.0, -10.0]]))
        assert not valid[0]

    def test_point_outside_the_frustum_is_invalid(self):
        pose = make_pose()
        _, valid = pose.project(np.array([[500.0, 0.0, 1.0]]))
        assert not valid[0]


class TestVoteFloor:
    def test_votes_floor_points_as_floor(self):
        rng = np.random.default_rng(0)
        floor = np.stack(
            [rng.uniform(-1, 1, 60), np.zeros(60), rng.uniform(-0.5, 1.5, 60)], axis=1
        )
        poses = [make_pose() for _ in range(4)]
        # Mask where everything the camera sees is floor.
        masks = [np.ones((p.height, p.width), dtype=bool) for p in poses]
        votes = vote_floor(floor, poses, masks, min_votes=3)
        assert votes.sum() > 0
        assert votes.mean() > 0.8

    def test_rejects_points_never_on_floor_pixels(self):
        rng = np.random.default_rng(1)
        pts = np.stack(
            [rng.uniform(-1, 1, 60), np.zeros(60), rng.uniform(-0.5, 1.5, 60)], axis=1
        )
        poses = [make_pose() for _ in range(4)]
        masks = [np.zeros((p.height, p.width), dtype=bool) for p in poses]
        assert vote_floor(pts, poses, masks).sum() == 0

    def test_requires_a_minimum_number_of_observations(self):
        pts = np.array([[0.0, 0.0, 0.0]])
        poses = [make_pose()]
        masks = [np.ones((poses[0].height, poses[0].width), dtype=bool)]
        # Seen once, but min_votes is 3.
        assert not vote_floor(pts, poses, masks, min_votes=3)[0]
        assert vote_floor(pts, poses, masks, min_votes=1)[0]

    def test_ratio_threshold_rejects_ambiguous_points(self):
        pts = np.array([[0.0, 0.0, 0.0]])
        poses = [make_pose() for _ in range(4)]
        masks = []
        for i, p in enumerate(poses):
            # Floor in 2 of 4 views -> ratio 0.5, below the 0.6 default.
            masks.append(np.full((p.height, p.width), i < 2, dtype=bool))
        assert not vote_floor(pts, poses, masks, min_votes=3, min_ratio=0.6)[0]
        assert vote_floor(pts, poses, masks, min_votes=3, min_ratio=0.4)[0]

    def test_mismatched_lengths_raise(self):
        with pytest.raises(ValueError):
            vote_floor(np.zeros((3, 3)), [make_pose()], [])


class TestRefineFloor:
    def test_widens_to_geometrically_coplanar_points(self):
        rng = np.random.default_rng(3)
        n = 400
        centres = np.stack(
            [rng.uniform(-2, 2, n), np.zeros(n), rng.uniform(-2, 2, n)], axis=1
        )
        # Segmentation only voted for half the floor.
        votes = np.zeros(n, dtype=bool)
        votes[: n // 2] = True
        plane, refined = refine_floor(centres, votes)
        assert abs(abs(np.array(plane[:3])[1]) - 1.0) < 1e-3
        # The unvoted half is still on the plane, so it should be recovered.
        assert refined.mean() > 0.95

    def test_excludes_points_above_the_plane(self):
        rng = np.random.default_rng(4)
        floor = np.stack(
            [rng.uniform(-2, 2, 300), np.zeros(300), rng.uniform(-2, 2, 300)], axis=1
        )
        furniture = floor[:100] + np.array([0.0, 0.5, 0.0])
        centres = np.vstack([floor, furniture])
        votes = np.zeros(len(centres), dtype=bool)
        votes[:300] = True
        _, refined = refine_floor(centres, votes, height_tolerance=0.05)
        assert refined[:300].mean() > 0.95
        assert refined[300:].sum() == 0

    def test_raises_without_enough_votes(self):
        with pytest.raises(ValueError):
            refine_floor(np.zeros((10, 3)), np.zeros(10, dtype=bool))


class TestConcaveHull:
    def test_wraps_a_rectangle(self):
        rng = np.random.default_rng(5)
        pts = np.stack([rng.uniform(0, 4, 400), rng.uniform(0, 3, 400)], axis=1)
        hull = concave_hull_2d(pts, alpha=1.0)
        assert hull[:, 0].min() < 0.3
        assert hull[:, 0].max() > 3.7
        assert hull[:, 1].max() > 2.7

    def test_needs_three_points(self):
        with pytest.raises(ValueError):
            concave_hull_2d(np.zeros((2, 2)))

    def test_returns_a_closed_ring_of_points(self):
        rng = np.random.default_rng(6)
        pts = np.stack([rng.uniform(0, 4, 200), rng.uniform(0, 3, 200)], axis=1)
        hull = concave_hull_2d(pts)
        assert hull.ndim == 2 and hull.shape[1] == 2
        assert len(hull) >= 3


class TestSimplifyPolygon:
    def test_reduces_a_dense_rectangle_to_four_corners(self):
        top = [(x / 10, 0.0) for x in range(41)]
        right = [(4.0, z / 10) for z in range(31)]
        bottom = [(4.0 - x / 10, 3.0) for x in range(41)]
        left = [(0.0, 3.0 - z / 10) for z in range(31)]
        poly = np.array(top + right + bottom + left)
        simplified = simplify_polygon(poly, tolerance=0.05)
        assert len(simplified) == 4


class TestShading:
    def test_shading_map_normalises_around_one(self):
        img = np.full((128, 128, 3), 120, dtype=np.uint8)
        shading = shading_map(img)
        assert shading.mean() == pytest.approx(1.0, abs=0.05)

    def test_shading_map_keeps_a_gradient(self):
        grad = np.tile(np.linspace(40, 220, 128, dtype=np.uint8), (128, 1))
        img = np.stack([grad] * 3, axis=-1)
        shading = shading_map(img)
        assert shading[:, 0].mean() < shading[:, -1].mean()

    def test_shading_map_removes_high_frequency_pattern(self):
        rng = np.random.default_rng(7)
        checker = np.indices((128, 128)).sum(axis=0) % 2 * 200
        noisy = np.clip(checker + rng.normal(0, 5, (128, 128)), 0, 255).astype(np.uint8)
        img = np.stack([noisy] * 3, axis=-1)
        shading = shading_map(img)
        # The checkerboard should be gone, leaving an almost flat map.
        assert shading.std() < 0.05

    def test_shading_handles_a_black_render(self):
        img = np.zeros((64, 64, 3), dtype=np.uint8)
        assert np.allclose(shading_map(img), 1.0)

    def test_png_encode_decode_round_trip(self):
        shading = np.full((32, 32), 1.0, dtype=np.float32)
        decoded = decode_shading_png(encode_shading_png(shading))
        assert decoded.mean() == pytest.approx(1.0, abs=0.01)

    def test_png_encoding_preserves_relative_brightness(self):
        shading = np.concatenate(
            [np.full((16, 32), 0.7), np.full((16, 32), 1.3)]
        ).astype(np.float32)
        decoded = decode_shading_png(encode_shading_png(shading))
        assert decoded[:16].mean() < decoded[16:].mean()


class TestRenderFloorTopdown:
    def test_renders_points_into_the_image(self):
        rng = np.random.default_rng(8)
        n = 5000
        centres = np.stack(
            [rng.uniform(0, 4, n), np.zeros(n), rng.uniform(0, 3, n)], axis=1
        )
        colours = np.full((n, 3), 150.0)
        img = render_floor_topdown(centres, colours, (0, 0, 4, 3), resolution=64)
        assert img.shape == (64, 64, 3)
        assert img.mean() > 100

    def test_rejects_degenerate_bounds(self):
        with pytest.raises(ValueError):
            render_floor_topdown(np.zeros((3, 3)), np.zeros((3, 3)), (0, 0, 0, 1))
