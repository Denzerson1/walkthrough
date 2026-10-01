"""Gravity alignment, plane fitting and metric scale (M1 `pipeline align`)."""

from __future__ import annotations

import numpy as np
import pytest
from walkthrough_pipeline.align import (
    apply_transform,
    compose,
    fit_plane_ransac,
    gravity_alignment,
    metric_scale,
    rotation_between,
    scale_transform,
)


def tilted_floor_points(n=800, tilt_deg=12.0, seed=1):
    """A floor plane tilted about X, as an uncalibrated reconstruction would be."""
    rng = np.random.default_rng(seed)
    x = rng.uniform(-2, 2, n)
    z = rng.uniform(-2, 2, n)
    y = np.zeros(n) + rng.normal(0, 0.003, n)
    pts = np.stack([x, y, z], axis=1)
    t = np.radians(tilt_deg)
    rot = np.array([[1, 0, 0], [0, np.cos(t), -np.sin(t)], [0, np.sin(t), np.cos(t)]])
    return pts @ rot.T


class TestFitPlaneRansac:
    def test_recovers_a_horizontal_plane(self):
        rng = np.random.default_rng(0)
        pts = np.stack(
            [rng.uniform(-1, 1, 500), np.full(500, 2.0), rng.uniform(-1, 1, 500)], axis=1
        )
        fit = fit_plane_ransac(pts)
        normal = np.array(fit.plane[:3])
        assert abs(abs(normal[1]) - 1.0) < 1e-6
        # Plane is y = 2, so n·p + d = 0 gives d = -2 (or +2 if flipped).
        assert abs(abs(fit.plane[3]) - 2.0) < 1e-6
        assert fit.inlier_ratio > 0.99

    def test_ignores_outliers(self):
        rng = np.random.default_rng(2)
        floor = np.stack(
            [rng.uniform(-2, 2, 600), np.zeros(600), rng.uniform(-2, 2, 600)], axis=1
        )
        noise = rng.uniform(-2, 2, (200, 3))
        noise[:, 1] += 1.5
        fit = fit_plane_ransac(np.vstack([floor, noise]), threshold=0.05)
        assert fit.inliers[:600].mean() > 0.95
        assert abs(np.array(fit.plane[:3])[1]) > 0.99

    def test_up_hint_rejects_a_wall(self):
        rng = np.random.default_rng(3)
        # A wall with more points than the floor.
        wall = np.stack(
            [np.zeros(900), rng.uniform(0, 2.5, 900), rng.uniform(-2, 2, 900)], axis=1
        )
        floor = np.stack(
            [rng.uniform(-2, 2, 400), np.zeros(400), rng.uniform(-2, 2, 400)], axis=1
        )
        pts = np.vstack([wall, floor])

        without = fit_plane_ransac(pts, threshold=0.02, seed=5)
        assert abs(np.array(without.plane[:3])[0]) > 0.9  # picked the wall

        with_hint = fit_plane_ransac(
            pts, threshold=0.02, seed=5, up_hint=np.array([0.0, 1.0, 0.0])
        )
        assert abs(np.array(with_hint.plane[:3])[1]) > 0.9  # picked the floor

    def test_rejects_too_few_points(self):
        with pytest.raises(ValueError):
            fit_plane_ransac(np.zeros((2, 3)))

    def test_rejects_wrong_shape(self):
        with pytest.raises(ValueError):
            fit_plane_ransac(np.zeros((10, 2)))


class TestRotationBetween:
    def test_identity_for_same_vector(self):
        v = np.array([0.0, 1.0, 0.0])
        assert np.allclose(rotation_between(v, v), np.eye(3))

    def test_handles_antiparallel(self):
        a = np.array([0.0, 1.0, 0.0])
        rot = rotation_between(a, -a)
        assert np.allclose(rot @ a, -a, atol=1e-9)

    def test_maps_a_onto_b(self):
        a = np.array([0.3, 0.9, -0.2])
        b = np.array([0.0, 0.0, 1.0])
        rot = rotation_between(a, b)
        assert np.allclose(rot @ (a / np.linalg.norm(a)), b, atol=1e-9)

    def test_is_a_proper_rotation(self):
        rot = rotation_between(np.array([1.0, 2.0, 3.0]), np.array([0.0, 1.0, 0.0]))
        assert np.allclose(rot @ rot.T, np.eye(3), atol=1e-9)
        assert np.isclose(np.linalg.det(rot), 1.0)


class TestGravityAlignment:
    def test_puts_a_tilted_floor_on_y_zero(self):
        pts = tilted_floor_points()
        fit = fit_plane_ransac(pts, up_hint=None)
        # Lift the scene so the bulk sits above the floor, as a real capture does.
        scene = np.vstack([pts, pts + np.array([0.0, 1.2, 0.0])])
        transform = gravity_alignment(fit.plane, scene)
        aligned = apply_transform(pts, transform)
        assert np.abs(aligned[:, 1]).mean() < 0.01

    def test_does_not_flip_the_scene_upside_down(self):
        pts = tilted_floor_points()
        above = pts + np.array([0.0, 1.5, 0.0])
        scene = np.vstack([pts, above])
        fit = fit_plane_ransac(pts)
        transform = gravity_alignment(fit.plane, scene)
        aligned_above = apply_transform(above, transform)
        assert aligned_above[:, 1].mean() > 0

    def test_normal_points_up_after_alignment(self):
        pts = tilted_floor_points(tilt_deg=25.0)
        scene = np.vstack([pts, pts + np.array([0.0, 2.0, 0.0])])
        fit = fit_plane_ransac(pts)
        transform = gravity_alignment(fit.plane, scene)
        aligned = apply_transform(pts, transform)
        refit = fit_plane_ransac(aligned)
        assert abs(abs(np.array(refit.plane[:3])[1]) - 1.0) < 1e-3


class TestMetricScale:
    def test_computes_the_ratio(self):
        assert metric_scale(2.0, 0.9) == pytest.approx(0.45)

    def test_scale_transform_scales_distances(self):
        pts = np.array([[0.0, 0.0, 0.0], [2.0, 0.0, 0.0]])
        scaled = apply_transform(pts, scale_transform(0.45))
        assert np.linalg.norm(scaled[1] - scaled[0]) == pytest.approx(0.9)

    def test_rejects_non_positive_inputs(self):
        with pytest.raises(ValueError):
            metric_scale(0.0, 0.9)
        with pytest.raises(ValueError):
            metric_scale(2.0, -1.0)


class TestCompose:
    def test_applies_first_transform_first(self):
        translate = np.eye(4)
        translate[0, 3] = 1.0
        scale = scale_transform(2.0)
        combined = compose(translate, scale)
        out = apply_transform(np.array([[0.0, 0.0, 0.0]]), combined)
        # Translate to x=1, then scale by 2 -> x=2.
        assert out[0, 0] == pytest.approx(2.0)

    def test_identity_with_no_arguments(self):
        assert np.allclose(compose(), np.eye(4))
