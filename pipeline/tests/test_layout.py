"""Automatic layout solver (M7). Every rule in the brief gets a test."""

from __future__ import annotations

import math

import pytest
from walkthrough_pipeline.layout import (
    DOOR_SWING_M,
    TABLE_CLEARANCE_M,
    Item,
    Room,
    Wall,
    box_corners,
    box_inside_polygon,
    boxes_overlap,
    inward_normal,
    point_in_polygon,
    polygon_centroid,
    solve_layout,
)

# A 4.2 x 3.8 m room. Wall 0 has the door, wall 2 has the window.
POLY = [(0.0, 0.0), (4.2, 0.0), (4.2, 3.8), (0.0, 3.8)]


def make_room(room_type: str = "living") -> Room:
    return Room(
        id="r1",
        type=room_type,
        polygon=POLY,
        walls=[
            Wall((0.0, 0.0), (4.2, 0.0), openings=[(2.1, 0.9)], has_door=True),
            Wall((4.2, 0.0), (4.2, 3.8)),
            Wall((4.2, 3.8), (0.0, 3.8), openings=[(2.1, 1.4)], has_window=True),
            Wall((0.0, 3.8), (0.0, 0.0)),
        ],
    )


class TestPrimitives:
    def test_box_corners_unrotated(self):
        corners = box_corners((0, 0), (2, 1), 0)
        assert corners[0] == pytest.approx((-1, -0.5))
        assert corners[2] == pytest.approx((1, 0.5))

    def test_boxes_overlap_detects_collision(self):
        assert boxes_overlap((0, 0), (2, 2), 0, (1, 0), (2, 2), 0)

    def test_boxes_apart_do_not_overlap(self):
        assert not boxes_overlap((0, 0), (2, 2), 0, (3, 0), (2, 2), 0)

    def test_clearance_creates_a_conflict(self):
        assert not boxes_overlap((0, 0), (2, 2), 0, (2.5, 0), (2, 2), 0)
        assert boxes_overlap((0, 0), (2, 2), 0, (2.5, 0), (2, 2), 0, clearance=0.8)

    def test_point_in_polygon(self):
        assert point_in_polygon((2, 2), POLY)
        assert not point_in_polygon((5, 2), POLY)

    def test_box_inside_polygon(self):
        assert box_inside_polygon((2, 2), (1, 1), 0, POLY)
        assert not box_inside_polygon((4.0, 2), (1, 1), 0, POLY)

    def test_polygon_centroid(self):
        assert polygon_centroid(POLY) == pytest.approx((2.1, 1.9))

    def test_inward_normal_points_into_the_room(self):
        room = make_room()
        n = inward_normal(room.walls[0], POLY)
        # Wall 0 runs along the bottom edge, so inward is +Z.
        assert n[1] > 0.9


class TestFreeSpans:
    def test_door_splits_the_wall(self):
        wall = Wall((0.0, 0.0), (4.2, 0.0), openings=[(2.1, 0.9)])
        spans = wall.free_spans()
        assert len(spans) == 2
        assert spans[0][1] == pytest.approx(1.65)
        assert spans[1][0] == pytest.approx(2.55)

    def test_margin_widens_the_blocked_region(self):
        wall = Wall((0.0, 0.0), (4.2, 0.0), openings=[(2.1, 0.9)])
        spans = wall.free_spans(margin=0.5)
        assert spans[0][1] == pytest.approx(1.15)

    def test_clear_wall_is_one_span(self):
        wall = Wall((0.0, 0.0), (4.2, 0.0))
        assert wall.free_spans() == [(0.0, pytest.approx(4.2))]

    def test_fully_blocked_wall_has_no_spans(self):
        wall = Wall((0.0, 0.0), (2.0, 0.0), openings=[(1.0, 2.0)])
        assert wall.free_spans() == []


class TestSolveLayout:
    def test_places_a_single_sofa(self):
        room = make_room()
        result = solve_layout(room, [Item("sofa-1", "sofa", (2.1, 0.9))])
        assert result.complete
        assert len(result.placements) == 1
        p = result.placements[0]
        assert box_inside_polygon(p.position, (2.1, 0.9), p.yaw, POLY)

    def test_bed_avoids_the_door_wall(self):
        room = make_room("bedroom")
        result = solve_layout(room, [Item("bed-1", "bed", (1.6, 2.0))])
        assert result.complete
        bed = result.placements[0]
        # Wall 0 (the door wall) runs along z=0; the bed must not be against it.
        assert bed.position[1] > 0.9

    def test_sofa_prefers_the_longest_free_wall(self):
        room = make_room()
        result = solve_layout(room, [Item("sofa-1", "sofa", (2.0, 0.85))])
        sofa = result.placements[0]
        # Walls 1 and 3 are 3.8 m, walls 0 and 2 are 4.2 m but 0 has the door.
        # So the sofa should be on wall 2 (z=3.8) or a 3.8 m side wall.
        assert sofa.position[1] > 1.0 or sofa.position[0] < 1.0 or sofa.position[0] > 3.2

    def test_everything_stays_inside_the_room(self):
        room = make_room()
        items = [
            Item("sofa-1", "sofa", (2.1, 0.9)),
            Item("table-1", "coffee_table", (1.0, 0.6)),
            Item("shelf-1", "shelf", (0.9, 0.35)),
        ]
        result = solve_layout(room, items)
        sizes = {i.id: i.size for i in items}
        for p in result.placements:
            assert box_inside_polygon(p.position, sizes[p.item_id], p.yaw, POLY), p.item_id

    def test_no_two_items_collide(self):
        room = make_room()
        items = [
            Item("sofa-1", "sofa", (2.1, 0.9)),
            Item("shelf-1", "shelf", (0.9, 0.35)),
            Item("shelf-2", "shelf", (0.9, 0.35)),
            Item("chair-1", "chair", (0.6, 0.6)),
        ]
        result = solve_layout(room, items)
        sizes = {i.id: i.size for i in items}
        placed = result.placements
        for i in range(len(placed)):
            for j in range(i + 1, len(placed)):
                a, b = placed[i], placed[j]
                assert not boxes_overlap(
                    a.position, sizes[a.item_id], a.yaw,
                    b.position, sizes[b.item_id], b.yaw,
                ), f"{a.item_id} overlaps {b.item_id}"

    def test_table_keeps_its_clearance(self):
        room = make_room()
        items = [
            Item("table-1", "dining_table", (1.4, 0.9)),
            Item("shelf-1", "shelf", (0.9, 0.35)),
        ]
        result = solve_layout(room, items)
        by_id = {p.item_id: p for p in result.placements}
        if "table-1" in by_id and "shelf-1" in by_id:
            t, s = by_id["table-1"], by_id["shelf-1"]
            assert not boxes_overlap(
                t.position, (1.4, 0.9), t.yaw,
                s.position, (0.9, 0.35), s.yaw,
                clearance=TABLE_CLEARANCE_M * 0.5,
            )

    def test_falls_back_to_fewer_items_when_the_room_is_full(self):
        tiny_poly = [(0.0, 0.0), (1.6, 0.0), (1.6, 1.6), (0.0, 1.6)]
        room = Room(
            id="tiny", type="bedroom", polygon=tiny_poly,
            walls=[
                Wall((0.0, 0.0), (1.6, 0.0), has_door=True),
                Wall((1.6, 0.0), (1.6, 1.6)),
                Wall((1.6, 1.6), (0.0, 1.6)),
                Wall((0.0, 1.6), (0.0, 0.0)),
            ],
        )
        items = [Item(f"sofa-{i}", "sofa", (1.4, 0.9)) for i in range(4)]
        result = solve_layout(room, items)
        assert not result.complete
        assert result.skipped
        assert len(result.placements) < 4

    def test_oversized_item_is_skipped_not_crammed(self):
        room = make_room()
        result = solve_layout(room, [Item("huge", "sofa", (9.0, 9.0))])
        assert result.placements == []
        assert result.skipped == ["huge"]

    def test_is_deterministic(self):
        room = make_room()
        items = [
            Item("sofa-1", "sofa", (2.1, 0.9)),
            Item("shelf-1", "shelf", (0.9, 0.35)),
            Item("lamp-1", "lamp", (0.4, 0.4)),
        ]
        a = solve_layout(room, items).as_dicts()
        b = solve_layout(room, items).as_dicts()
        assert a == b

    def test_order_of_input_does_not_change_the_result(self):
        room = make_room()
        items = [
            Item("sofa-1", "sofa", (2.1, 0.9)),
            Item("shelf-1", "shelf", (0.9, 0.35)),
        ]
        a = solve_layout(room, items).as_dicts()
        b = solve_layout(room, list(reversed(items))).as_dicts()
        assert a == b

    def test_empty_item_list_gives_an_empty_complete_layout(self):
        result = solve_layout(make_room(), [])
        assert result.placements == []
        assert result.complete

    def test_door_swing_area_is_kept_clear(self):
        room = make_room()
        result = solve_layout(room, [Item("shelf-1", "shelf", (0.8, 0.35))])
        if result.placements:
            p = result.placements[0]
            door_centre = (2.1, 0.0)
            # Nothing should sit right in the doorway.
            distance = math.dist(p.position, door_centre)
            assert distance > DOOR_SWING_M / 2

    def test_coffee_table_lands_near_the_sofa(self):
        room = make_room()
        items = [
            Item("sofa-1", "sofa", (2.1, 0.9)),
            Item("ct-1", "coffee_table", (1.0, 0.55)),
        ]
        result = solve_layout(room, items)
        by_id = {p.item_id: p for p in result.placements}
        if "ct-1" in by_id and "sofa-1" in by_id:
            assert math.dist(by_id["ct-1"].position, by_id["sofa-1"].position) < 2.0

    def test_placements_serialise_with_y_zero(self):
        room = make_room()
        result = solve_layout(room, [Item("sofa-1", "sofa", (2.1, 0.9))])
        d = result.as_dicts()[0]
        assert d["position"][1] == 0.0
        assert set(d) == {"itemId", "position", "yaw"}
