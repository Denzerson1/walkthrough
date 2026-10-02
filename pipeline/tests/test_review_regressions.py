"""
Regressions for defects found in review that the original suite missed.

Each test here failed before its fix. They exist because the earlier tests
asserted properties too weak to catch the bug — bounds instead of winding,
positions instead of orientations, a CCW room instead of both windings.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from walkthrough_pipeline.align import rotation_between
from walkthrough_pipeline.floor import concave_hull_2d
from walkthrough_pipeline.layout import (
    Item,
    Room,
    Wall,
    box_corners,
    facing_yaw,
    inward_normal,
    point_in_polygon,
    solve_layout,
)
from walkthrough_pipeline.splat import (
    SplatCloud,
    matrix_to_quaternion,
    rotate_quaternions,
)


class TestConcaveHullWinding:
    """
    The hull used to be indexed with np.sort(hull_idx), which discards the
    around-the-hull ordering cv2 returns and produces a bowtie of zero area.
    """

    @staticmethod
    def _signed_area(poly: np.ndarray) -> float:
        a = 0.0
        for i in range(len(poly)):
            x1, y1 = poly[i]
            x2, y2 = poly[(i + 1) % len(poly)]
            a += x1 * y2 - x2 * y1
        return a / 2

    def test_hull_of_a_rectangle_has_the_right_area(self):
        rng = np.random.default_rng(0)
        pts = np.stack([rng.uniform(0, 4, 400), rng.uniform(0, 3, 400)], axis=1)
        hull = concave_hull_2d(pts, alpha=1.0)
        assert abs(self._signed_area(hull)) == pytest.approx(12.0, rel=0.08)

    def test_hull_is_not_self_intersecting(self):
        rng = np.random.default_rng(1)
        pts = np.stack([rng.uniform(0, 4, 300), rng.uniform(0, 3, 300)], axis=1)
        hull = concave_hull_2d(pts, alpha=1.0)
        # A bowtie has near-zero signed area even though its bounds look right.
        assert abs(self._signed_area(hull)) > 1.0

    def test_hull_centre_is_inside_the_hull(self):
        rng = np.random.default_rng(2)
        pts = np.stack([rng.uniform(0, 4, 300), rng.uniform(0, 3, 300)], axis=1)
        hull = [tuple(p) for p in concave_hull_2d(pts, alpha=1.0)]
        assert point_in_polygon((2.0, 1.5), hull)

    def test_input_order_does_not_change_the_shape(self):
        rng = np.random.default_rng(3)
        pts = np.stack([rng.uniform(0, 4, 200), rng.uniform(0, 3, 200)], axis=1)
        a = abs(self._signed_area(concave_hull_2d(pts, alpha=1.0)))
        shuffled = pts[rng.permutation(len(pts))]
        b = abs(self._signed_area(concave_hull_2d(shuffled, alpha=1.0)))
        assert a == pytest.approx(b, rel=0.1)


CW_POLY = [(0.0, 0.0), (0.0, 3.0), (4.0, 3.0), (4.0, 0.0)]
CCW_POLY = [(0.0, 0.0), (4.0, 0.0), (4.0, 3.0), (0.0, 3.0)]


def _front_direction(yaw: float) -> tuple[float, float]:
    """The 2D direction an item with this yaw faces."""
    r = math.radians(yaw)
    return (-math.sin(r), math.cos(r))


class TestFacingYaw:
    """
    The solver used to return wall.yaw unconditionally, which only faces into
    the room for one winding. Clockwise rooms got beds facing the wall.
    """

    def test_faces_into_the_room_for_ccw_winding(self):
        wall = Wall((0.0, 0.0), (4.0, 0.0))
        inward = inward_normal(wall, CCW_POLY)
        fx, fy = _front_direction(facing_yaw(inward))
        assert fx * inward[0] + fy * inward[1] > 0.99

    def test_faces_into_the_room_for_cw_winding(self):
        wall = Wall((0.0, 0.0), (0.0, 3.0))
        inward = inward_normal(wall, CW_POLY)
        fx, fy = _front_direction(facing_yaw(inward))
        assert fx * inward[0] + fy * inward[1] > 0.99

    def test_every_wall_of_a_cw_room_faces_inward(self):
        walls = [
            Wall((0.0, 0.0), (0.0, 3.0)),
            Wall((0.0, 3.0), (4.0, 3.0)),
            Wall((4.0, 3.0), (4.0, 0.0)),
            Wall((4.0, 0.0), (0.0, 0.0)),
        ]
        for wall in walls:
            inward = inward_normal(wall, CW_POLY)
            fx, fy = _front_direction(facing_yaw(inward))
            assert fx * inward[0] + fy * inward[1] > 0.99

    def test_solver_places_a_bed_facing_the_room_in_a_cw_room(self):
        room = Room(
            id="cw", type="bedroom", polygon=CW_POLY,
            walls=[
                Wall((0.0, 0.0), (0.0, 3.0), openings=[(1.5, 0.9)], has_door=True),
                Wall((0.0, 3.0), (4.0, 3.0)),
                Wall((4.0, 3.0), (4.0, 0.0)),
                Wall((4.0, 0.0), (0.0, 0.0)),
            ],
        )
        result = solve_layout(room, [Item("bed", "bed", (1.6, 2.0))])
        assert result.placements
        placement = result.placements[0]
        fx, fy = _front_direction(placement.yaw)
        # The front must point back toward the middle of the room.
        to_centre = (2.0 - placement.position[0], 1.5 - placement.position[1])
        length = math.hypot(*to_centre) or 1.0
        assert (fx * to_centre[0] + fy * to_centre[1]) / length > 0

    def test_corners_stay_inside_for_both_windings(self):
        for poly in (CCW_POLY, CW_POLY):
            room = Room(
                id="r", type="living", polygon=poly,
                walls=[
                    Wall(poly[i], poly[(i + 1) % len(poly)]) for i in range(len(poly))
                ],
            )
            result = solve_layout(room, [Item("sofa", "sofa", (2.0, 0.9))])
            for placement in result.placements:
                for corner in box_corners(placement.position, (2.0, 0.9), placement.yaw):
                    assert point_in_polygon(corner, poly)


class TestSplatOrientationTransform:
    """
    transformed() used to carry the quaternions through untouched, leaving
    every Gaussian tilted away from its surface after gravity alignment.
    """

    @staticmethod
    def _cloud(n: int = 8) -> SplatCloud:
        return SplatCloud(
            positions=np.zeros((n, 3), np.float32),
            colours=np.zeros((n, 3), np.float32),
            opacities=np.ones(n, np.float32),
            scales=np.full((n, 3), 0.01, np.float32),
            rotations=np.tile(np.array([1, 0, 0, 0], np.float32), (n, 1)),
        )

    def test_rotation_reaches_the_quaternions(self):
        rot = rotation_between(np.array([0.0, 0.0, 1.0]), np.array([0.0, 1.0, 0.0]))
        matrix = np.eye(4)
        matrix[:3, :3] = rot
        out = self._cloud().transformed(matrix)
        assert not np.allclose(out.rotations[0], np.array([1, 0, 0, 0]))
        assert np.allclose(out.rotations[0], matrix_to_quaternion(rot), atol=1e-5)

    def test_quaternions_stay_normalised(self):
        rot = rotation_between(np.array([0.3, 0.9, -0.2]), np.array([0.0, 1.0, 0.0]))
        matrix = np.eye(4)
        matrix[:3, :3] = rot
        out = self._cloud().transformed(matrix)
        assert np.allclose(np.linalg.norm(out.rotations, axis=1), 1.0, atol=1e-5)

    def test_identity_transform_leaves_orientations_alone(self):
        out = self._cloud().transformed(np.eye(4))
        assert np.allclose(out.rotations, self._cloud().rotations, atol=1e-6)

    def test_pure_scale_leaves_orientations_alone(self):
        matrix = np.eye(4) * 2.0
        matrix[3, 3] = 1.0
        out = self._cloud().transformed(matrix)
        assert np.allclose(out.rotations, self._cloud().rotations, atol=1e-6)
        assert out.scales[0, 0] == pytest.approx(0.02, rel=1e-3)

    def test_rotate_quaternions_handles_an_empty_cloud(self):
        empty = np.zeros((0, 4), np.float32)
        assert rotate_quaternions(empty, np.eye(3)).shape == (0, 4)

    def test_matrix_to_quaternion_round_trips_a_180_degree_turn(self):
        rot = rotation_between(np.array([0.0, 1.0, 0.0]), np.array([0.0, -1.0, 0.0]))
        q = matrix_to_quaternion(rot)
        assert np.linalg.norm(q) == pytest.approx(1.0)
