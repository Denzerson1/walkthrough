"""The view out of the windows from the owner's photos, and the sky behind it."""

from __future__ import annotations

import math

import cv2
import numpy as np
import pytest
from walkthrough_pipeline import glb, sky, view_photo


def test_glb_holds_each_photo_mesh_with_its_texture(tmp_path):
    tri = np.array([[0, 1, 2]])
    mesh = (np.eye(3), np.array([[0, 0], [1, 0], [0, 1]]), tri, b"\xff\xd8jpeg")
    glb.write_textured_meshes(tmp_path / "v.glb", [mesh, mesh], "view-photo")
    doc = glb.read_json(tmp_path / "v.glb")
    assert len(doc["meshes"]) == 2 and len(doc["images"]) == 2
    assert doc["materials"][1]["name"] == "view-photo"
    assert "KHR_materials_unlit" in doc["materials"][0]["extensions"]
    assert doc["accessors"][0]["count"] == 3 and doc["accessors"][2]["count"] == 3
    assert (tmp_path / "v.glb").stat().st_size % 4 == 0


def test_walls_are_where_the_features_cluster():
    rng = np.random.default_rng(0)
    d = np.concatenate([rng.normal(12, 0.3, 200), rng.normal(50, 1.0, 300), rng.uniform(0, 80, 20)])
    walls = view_photo.wall_depths(d)
    assert walls[0] == pytest.approx(12, abs=0.5)
    assert any(abs(w - 50) < 1.5 for w in walls)
    label = view_photo.nearest_wall(np.array([12.3, 49.0, 30.0]), walls)
    assert walls[label[0]] == pytest.approx(12, abs=0.5)
    assert walls[label[1]] == pytest.approx(50, abs=1.5)
    assert label[2] == -1


def test_drawn_in_keeps_order_and_stays_in_front():
    d = np.array([5.0, 12.0, 20.0, 60.0])
    near = view_photo.in_front(d, 12.0)
    assert np.all(np.diff(near) > 0)  # far stays behind near
    assert np.all(near < d)  # in front of the generated houses on the same ground


def test_level_frame_is_upright_and_orthonormal():
    up = np.array([0.0, -math.cos(0.2), math.sin(0.2)])  # a camera pitched up 0.2 rad
    frame = view_photo.level_frame(up)
    assert np.allclose(frame @ frame.T, np.eye(3), atol=1e-9)
    assert frame[2] @ up == pytest.approx(0)
    assert frame[0] == pytest.approx([1, 0, 0])


def test_the_tilt_is_read_from_converging_verticals():
    # Vertical edges seen by a camera pitched up 10 degrees: they lean
    # towards a point above the image.
    w, h, f = 900, 1200, 1000.0
    pitch = math.radians(10)
    img = np.full((h, w, 3), 230, np.uint8)
    for x in np.linspace(-6, 6, 9):
        pts = []
        for y in (-4.0, 6.0):
            p = np.array([x, y, 15.0])  # camera frame of a level camera (y up)
            # pitch up: rotate the world down about x
            yc = p[1] * math.cos(pitch) - p[2] * math.sin(pitch)
            zc = p[1] * math.sin(pitch) + p[2] * math.cos(pitch)
            pts.append((int(w / 2 + f * p[0] / zc), int(h / 2 - f * yc / zc)))
        cv2.line(img, pts[0], pts[1], (20, 20, 20), 4)
    up = view_photo.vertical_direction(img, f)
    assert math.degrees(math.asin(up[2])) == pytest.approx(10, abs=1.0)


def test_keep_mask_cuts_the_window_frame():
    mask = view_photo.keep_mask((100, 100), [[0, 0], [1, 0], [1, 0.8], [0, 0.8]])
    assert mask[10, 50] and not mask[95, 50]
    assert view_photo.keep_mask((10, 10), None).all()


def test_sky_panorama_conventions_and_colour():
    d = sky.directions(64, 32)
    assert d[16, 32] == pytest.approx([0, 0, -1], abs=0.1)  # centre looks along -z
    assert d[0, 0, 1] > 0.99  # top row: the zenith
    noon = sky.atmosphere(np.array([0.0, 1.0, 0.3]), 64, 32)
    zenith, low = noon[2].mean(axis=0), noon[14].mean(axis=0)
    assert zenith[2] > zenith[0]  # blue overhead
    assert (low[2] / low[0]) < (zenith[2] / zenith[0])  # paler towards the horizon


def test_sky_model_is_matched_row_by_row_to_the_photo():
    model = np.ones((20, 30, 3))
    colour = np.zeros((20, 30, 3))
    colour[5:10] = [0.2, 0.4, 0.8]
    weight = np.zeros((20, 30))
    weight[5:10] = 1.0
    match = sky.match_rows(colour, weight, model)
    assert match[7] == pytest.approx([0.2, 0.4, 0.8], abs=0.05)
    assert match[0] == pytest.approx([0.2, 0.4, 0.8], abs=0.05)  # carried beyond the photo


def test_photo_sky_weight_is_zero_on_the_buildings():
    h, w = 300, 200
    rgb = np.full((h, w, 3), 0.5)
    is_sky = np.zeros((h, w), bool)
    is_sky[:150] = True
    colour, weight = sky.project_photo(256, 128, rgb, is_sky, np.array([1.0, 0, 0]),
                                       np.array([0, 1.0, 0]), np.array([0, 0, -1.0]), 200.0)
    d = sky.directions(256, 128)
    ahead_up = np.argmin(np.linalg.norm(d - np.array([0, 0.2, -0.98]), axis=-1))
    ahead_down = np.argmin(np.linalg.norm(d - np.array([0, -0.2, -0.98]), axis=-1))
    assert weight.ravel()[ahead_up] > 0.3
    assert weight.ravel()[ahead_down] == pytest.approx(0, abs=1e-3)


def test_a_second_photo_keeps_only_what_the_first_does_not_show():
    # The first camera looks down +z from the origin, 200 x 100 px, f = 100:
    # it sees out to x = +-10 at z = 10.
    cam = view_photo.Camera(np.zeros(3), np.array([1.0, 0, 0]), np.array([0, 1.0, 0]),
                            np.array([0, 0, 1.0]), 100.0, (200, 100))
    shown = np.ones((100, 200), bool)
    first = view_photo.View((np.zeros((0, 3)), None, None, b""), cam, None, shown, ~shown, {})
    # Two triangles of the second photo's mesh at z = 10: one inside the
    # first's frame, one beyond its right edge.
    positions = np.array([[0, 0, 10], [1, 0, 10], [0, 1, 10],
                          [15, 0, 10], [16, 0, 10], [15, 1, 10]], float)
    tri = np.array([[0, 1, 2], [3, 4, 5]])
    second = view_photo.View((positions, np.zeros((6, 2)), tri, b"jpeg"), cam, None, shown,
                             ~shown, {})
    kept = view_photo.outside_of(second, first).mesh[2]
    assert kept.tolist() == [[3, 4, 5]]
    # Where the first photo's frame was cut away (keep), the second still shows.
    first.solid[:] = False
    assert len(view_photo.outside_of(second, first).mesh[2]) == 2


def test_what_stands_just_behind_the_street_front_lies_on_it():
    assert view_photo.street_front([12.0, 18.6, 33.5, 52.0]) == [12.0, 33.5, 52.0]
    assert view_photo.street_front([10.6, 15.4, 23.5]) == [10.6, 23.5]
    assert view_photo.street_front([12.0]) == [12.0]
