"""The surroundings from photos (surround*.py): the parts that are logic, on made-up scenes."""

from __future__ import annotations

import math

import numpy as np
import pytest
from walkthrough_pipeline import surround, surround_mesh, surround_recon, surround_register


def camera(centre=(0.0, 0.0, 0.0), size=(160, 120), f=120.0) -> surround_recon.View:
    """A camera at `centre` looking along +z, x right, y down."""
    k = np.array([[f, 0, size[0] / 2], [0, f, size[1] / 2], [0, 0, 1.0]])
    rotation = np.eye(3)
    return surround_recon.View("a.jpg", rotation, -rotation @ np.array(centre, float), k, size,
                               np.zeros((0, 2)), np.zeros(0))


# -- the reconstruction's helpers ------------------------------------------


def test_a_depth_map_goes_out_to_points_and_back_to_its_pixels():
    view = camera(centre=(1.0, 2.0, 3.0))
    depth = np.full((120, 160), 5.0)
    points = view.world(depth).reshape(-1, 3)
    assert np.allclose(points[:, 2], 3.0 + 5.0)
    uv, z = view.project(points)
    assert np.allclose(z, 5.0)
    grid = view.rays()[..., :2].reshape(-1, 2) * 120.0 + [80, 60]
    assert np.allclose(uv, grid, atol=1e-6)


def test_the_line_fit_ignores_a_fifth_of_wild_anchors():
    rng = np.random.default_rng(1)
    mono = rng.uniform(0.5, 6.0, 4000)
    inverse = 0.12 * mono + 0.03 + rng.normal(0, 0.002, 4000)
    inverse[:800] = rng.uniform(0.0, 2.0, 800)  # stereo's outliers
    a, b = surround_recon.fit_line(mono, inverse, np.ones(4000))
    assert a == pytest.approx(0.12, abs=0.004)
    assert b == pytest.approx(0.03, abs=0.012)


def test_completed_depth_takes_the_networks_shape_and_the_measured_scale():
    view = camera()
    yy = np.linspace(0, 1, 120)[:, None] * np.ones((1, 160))
    truth = 4.0 + 16.0 * yy  # a ground running away: 4 units at the top to 20 at the bottom
    mono = 30.0 / truth  # the network: relative inverse depth, any scale
    own = np.zeros_like(truth)
    own[::7, ::5] = truth[::7, ::5]  # the stereo measured a fortieth of it
    depth = surround_recon.complete(view, mono, own.astype(np.float32), None)
    assert depth is not None
    assert np.median(np.abs(depth / truth - 1)) < 0.01


def test_a_photo_with_nothing_measured_has_no_depth():
    view = camera()
    assert surround_recon.complete(view, np.ones((120, 160)), np.zeros((120, 160), np.float32),
                                   None) is None


def test_what_lies_behind_a_surface_is_not_read_through_it():
    view = camera()
    xs, ys = np.meshgrid(np.linspace(-2, 2, 200), np.linspace(-1.5, 1.5, 150))
    wall = np.column_stack([xs.ravel(), ys.ravel(), np.full(xs.size, 5.0)])
    behind = np.column_stack([xs.ravel()[::9], ys.ravel()[::9], np.full(xs.size, 9.0)[::9]])
    depth = surround_recon.seen_depth(view, np.vstack([wall, behind]))
    seen = depth[depth > 0]
    assert len(seen) > 1000
    assert (seen > 6).mean() < 0.01


# -- up, north, and the balcony --------------------------------------------


def test_north_from_a_sun_of_known_bearing():
    up = np.array([0.0, 0.0, 1.0])  # a model that happens to be east, north, up
    suns = []
    for bearing, elevation in ((200.0, 19.5), (201.5, 19.3), (140.0, 30.0)):
        b, e = math.radians(bearing), math.radians(elevation)
        suns.append((np.array([math.sin(b) * math.cos(e), math.cos(b) * math.cos(e),
                               math.sin(e)]), bearing, elevation))
    north, said = surround_register.north_direction(up, suns)
    assert np.allclose(north, [0, 1, 0], atol=1e-6)
    assert all(s["used"] for s in said)


def test_a_sun_at_the_wrong_height_or_bearing_is_left_out():
    up = np.array([0.0, 0.0, 1.0])

    def towards(bearing, elevation):
        b, e = math.radians(bearing), math.radians(elevation)
        return np.array([math.sin(b) * math.cos(e), math.cos(b) * math.cos(e), math.sin(e)])

    suns = [(towards(200, 19.5), 200.0, 19.5), (towards(201, 19.4), 201.0, 19.4),
            (towards(202, 19.3), 202.0, 19.3),
            (towards(206, 19.4), 202.0, 19.4),  # its photo's pose is four degrees off
            (towards(90, 45.0), 200.0, 19.5)]   # a white wall taken for the sun
    north, said = surround_register.north_direction(up, suns)
    assert [s["used"] for s in said] == [True, True, True, False, False]
    assert np.degrees(np.arccos(north @ [0, 1, 0])) < 0.05


def test_scene_axes_put_the_balcony_side_on_minus_z():
    axes = surround_register.scene_axes(180.0)  # the flat faces south
    south, west, up = np.array([0, -1.0, 0]), np.array([-1.0, 0, 0]), np.array([0, 0, 1.0])
    assert np.allclose(axes @ south, [0, 0, -1], atol=1e-9)
    assert np.allclose(axes @ west, [1, 0, 0], atol=1e-9)
    assert np.allclose(axes @ up, [0, 1, 0], atol=1e-9)


def balcony_points(rng, facing: float, scale: float, shift: np.ndarray):
    """A balcony's deck, roof, rail and side wall as model points, and cameras on it."""
    plan = surround_register.Balcony(rail_z=-3.6, wall_x=0.9, wall_towards=-1, height=2.56,
                                     rail_height=1.0)

    def scatter(n, x, y, z):
        return np.column_stack([rng.uniform(*x, n), rng.uniform(*y, n), rng.uniform(*z, n)])

    scene = np.vstack([
        scatter(6000, (0.9, 6.5), (0.0, 0.0), (-3.6, -1.7)),      # the deck
        scatter(1500, (0.9, 6.5), (2.56, 2.56), (-3.6, -1.7)),    # the roof over it
        scatter(2500, (0.9, 6.5), (0.99, 1.0), (-3.61, -3.59)),   # the rail's top
        scatter(1200, (0.9, 6.5), (0.85, 1.3), (-3.85, -3.7)),    # flower boxes hung outside it
        scatter(2500, (0.9, 0.9), (0.0, 2.56), (-3.6, -1.7)),     # the side wall
        scatter(600, (1.6, 1.62), (0.0, 1.1), (-2.4, -1.8)),      # a grill before it
    ])
    cameras = scatter(8, (2.5, 5.8), (1.5, 1.75), (-3.3, -1.9))
    axes = surround_register.scene_axes(facing)  # scene = scale * axes @ enu + shift

    def to_model(points):
        return (points - shift) @ np.linalg.inv(scale * axes).T

    return plan, to_model(scene), to_model(cameras), cameras


def test_the_balcony_sets_scale_facing_and_place():
    rng = np.random.default_rng(3)
    plan, points, cameras, where = balcony_points(rng, facing=192.0, scale=3.85,
                                                  shift=np.array([4.0, 1.2, -2.0]))
    fit = surround_register.fit_balcony(points, cameras, np.eye(3), plan, facing_guess=188.0)
    assert fit.scale == pytest.approx(3.85, rel=0.01)
    assert fit.facing == pytest.approx(192.0, abs=0.3)
    assert np.abs(fit.to_scene(cameras) - where).max() < 0.06


def test_no_deck_no_fit():
    rng = np.random.default_rng(4)
    points = rng.uniform(-1, 1, (500, 3)) + [0, 0, 5.0]  # everything above the cameras
    with pytest.raises(ValueError):
        surround_register.fit_balcony(points, rng.uniform(-0.2, 0.2, (6, 3)), np.eye(3),
                                      surround_register.Balcony(-3.6, 0.9, -1, 2.56, 1.0), 180.0)


# -- the photos as meshes ---------------------------------------------------


def test_a_ray_into_the_house_is_its_own_and_one_past_it_is_not():
    box = [(-1.0, 1.0, 0.0, 3.0, 4.0, 6.0)]
    start = np.zeros(3) + [0, 1.5, 0]
    dirs = np.array([[0, 0, 1.0], [0.6, 0, 0.8], [0, 0, -1.0]])
    hit = surround_mesh.box_hits(start, dirs, box, lean=[])
    assert hit[0] == pytest.approx(4.0 - surround_mesh.GROW)
    assert np.isinf(hit[1]) and np.isinf(hit[2])


def test_a_photo_taken_leaning_over_a_rail_is_not_blocked_by_it():
    rail = [(0.0, 6.0, 0.0, 1.5, -4.0, -3.5)]
    start = np.array([3.0, 1.8, -3.4])  # just inside the rail, above it
    down_and_out = np.array([[0.0, -0.6, -0.8]])
    assert np.isfinite(surround_mesh.box_hits(start, down_and_out, rail, lean=[]))[0]
    assert np.isinf(surround_mesh.box_hits(start, down_and_out, rail, lean=[0]))[0]


def test_far_distances_are_drawn_in_under_the_far_plane_and_keep_their_order():
    dist = np.array([10.0, 300.0, 350.0, 1000.0, 50000.0])
    drawn = surround_mesh.squeeze(dist)
    assert np.allclose(drawn[:3], dist[:3])
    assert np.all(np.diff(drawn) > 0) and drawn[-1] <= surround_mesh.FAR


def test_sky_is_the_blue_the_network_sees_nothing_in_not_a_pale_wall():
    bgr = np.zeros((200, 300, 3), np.uint8)
    bgr[:100] = (230, 150, 60)    # blue sky (BGR)
    bgr[100:] = (240, 240, 240)   # a white wall, pale as a cloud
    bgr[40:60, 100:140] = (250, 250, 250)  # a cloud in the sky
    mono = np.zeros((200, 300), np.float32)
    mono[100:] = 3.0              # the wall is near; sky and cloud are nought
    sky = surround_mesh.sky_mask(bgr, mono)
    assert sky[:95].mean() > 0.98 and sky[50, 120]
    assert not sky[105:].any()


def test_the_veil_comes_off_towards_the_sun_only():
    rng = np.random.default_rng(5)
    scene = rng.uniform(0.02, 0.5, (120, 160, 3)).astype(np.float32)
    veiled = scene * 0.7 + 0.3  # a wash of light over all of it
    bgr = (surround.linear_to_srgb(veiled) * 255).astype(np.uint8)
    rays = np.zeros((120, 160, 3))
    rays[..., 2] = 1.0
    towards = surround_mesh.unveil(bgr, rays, np.array([0, 0, 1.0]), 0.9)
    away = surround_mesh.unveil(bgr, rays, np.array([0, 0, -1.0]), 0.9)
    assert np.array_equal(away, bgr)
    assert towards.mean() < bgr.mean() - 20
    assert towards.astype(float).std() > bgr.astype(float).std()  # the contrast is back


# -- what the skies take from it --------------------------------------------


def panoramas(known_rows: slice) -> surround.Panoramas:
    ground = np.zeros((900, 1800, 3), np.float32)
    known = np.zeros((900, 1800), bool)
    known[known_rows, 600:1200] = True
    ground[known] = (0.1, 0.3, 0.05)
    return surround.Panoramas(np.zeros((8, 16, 3), np.float32), np.zeros((8, 16), np.float32),
                              ground, known, np.zeros(3))


def test_the_ground_fill_stays_below_the_skyline_and_the_horizon():
    # Houses from 10 degrees above the horizon (row 400) down to 20 below (row 550).
    colour, cover = surround.ground_fill(panoramas(slice(400, 550)), 1024, 512)
    column = cover[:, 512]
    assert column[: int(512 * 85 / 180)].max() < 0.02   # nothing more than 5 degrees up
    assert column[int(512 * 95 / 180):].min() > 0.98    # all of it from 5 degrees down
    assert cover[:, 100].max() == 0                     # nothing where no photo looks
    assert np.allclose(colour[300, 512], (0.1, 0.3, 0.05), atol=0.02)


def test_the_view_dims_with_the_sky_and_cools_at_dusk():
    def sky(level, tint=(1.0, 1.0, 1.0)):
        lin = np.full((200, 400, 3), level, np.float32) * np.array(tint, np.float32)
        return (surround.linear_to_srgb(np.clip(lin, 0, 1)) * 255).astype(np.uint8)

    tints = surround.daylight_tints(
        {"13:05": sky(0.5), "15:30": sky(0.4, (1.0, 0.8, 0.6)), "17:15": sky(0.01, (1, 0.5, 0.3))},
        taken="13:00")
    assert tints["13:05"] == pytest.approx([1, 1, 1], abs=0.01)
    assert tints["15:30"][0] > tints["15:30"][2]                      # warmer in the afternoon
    assert 0.5 < sum(tints["15:30"]) / 3 < 0.95
    assert max(tints["17:15"]) < 0.06 and tints["17:15"][2] > tints["17:15"][0]  # dark, and blue


def test_a_pocket_of_blue_is_sky_and_a_pale_patch_that_size_is_not():
    bgr = np.full((400, 600, 3), (40, 90, 50), np.uint8)   # a tree all over
    bgr[:150, :300] = (230, 150, 60)                       # open sky
    bgr[250:270, 400:420] = (230, 150, 60)                 # a pocket of sky in the tree
    bgr[250:270, 500:520] = (245, 245, 245)                # a pale patch: a window frame
    mono = np.full((400, 600), 3.0, np.float32)
    mono[:150, :300] = 0
    mono[250:270, 400:420] = 0
    mono[250:270, 500:520] = 0
    sky = surround_mesh.sky_mask(bgr, mono)
    assert sky[260, 410] and not sky[260, 510]

