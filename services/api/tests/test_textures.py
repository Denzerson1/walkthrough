"""Floor photo processing (M4): rectification, tiling, material maps."""

from __future__ import annotations

import cv2
import numpy as np
import pytest
from walkthrough_api import textures


def checkerboard(size=256, cell=32, a=60, b=200) -> np.ndarray:
    grid = (np.indices((size, size)) // cell).sum(axis=0) % 2
    grey = np.where(grid == 0, a, b).astype(np.uint8)
    return np.stack([grey] * 3, axis=-1)


def gradient_tile(size=256) -> np.ndarray:
    row = np.linspace(40, 220, size, dtype=np.uint8)
    grey = np.tile(row, (size, 1))
    return np.stack([grey] * 3, axis=-1)


class TestOrderCorners:
    def test_sorts_into_tl_tr_br_bl(self):
        unordered = [(10, 90), (90, 10), (10, 10), (90, 90)]
        ordered = textures.order_corners(unordered)
        assert tuple(ordered[0]) == (10, 10)
        assert tuple(ordered[2]) == (90, 90)

    def test_already_ordered_is_unchanged(self):
        pts = [(0, 0), (100, 0), (100, 100), (0, 100)]
        ordered = textures.order_corners(pts)
        assert tuple(ordered[0]) == (0, 0)
        assert tuple(ordered[1]) == (100, 0)

    def test_rejects_wrong_count(self):
        with pytest.raises(ValueError):
            textures.order_corners([(0, 0), (1, 1), (2, 2)])


class TestRectify:
    def test_output_is_square(self):
        img = checkerboard()
        out = textures.rectify(img, [(0, 0), (255, 0), (255, 255), (0, 255)], out_size=128)
        assert out.shape[:2] == (128, 128)

    def test_straightens_a_perspective_quad(self):
        img = checkerboard(256, 32)
        # A trapezoid, as a floor photographed at an angle would be.
        quad = [(40, 40), (210, 60), (230, 200), (20, 190)]
        out = textures.rectify(img, quad, out_size=128)
        assert out.shape[:2] == (128, 128)
        # A real tile has structure; a failed warp would be flat.
        assert out.std() > 10

    def test_corner_order_does_not_matter(self):
        img = checkerboard()
        quad = [(0, 0), (255, 0), (255, 255), (0, 255)]
        a = textures.rectify(img, quad, out_size=64)
        b = textures.rectify(img, list(reversed(quad)), out_size=64)
        assert np.array_equal(a, b)


class TestProposeQuad:
    def test_returns_four_corners(self):
        assert len(textures.propose_quad(checkerboard())) == 4

    def test_falls_back_to_a_centred_box_on_a_blank_image(self):
        blank = np.full((200, 300, 3), 128, dtype=np.uint8)
        corners = textures.propose_quad(blank)
        assert len(corners) == 4
        xs = [c[0] for c in corners]
        assert min(xs) > 0 and max(xs) < 300

    def test_corners_stay_inside_the_image(self):
        img = checkerboard(240)
        for x, y in textures.propose_quad(img):
            assert 0 <= x <= 240
            assert 0 <= y <= 240


class TestMakeSeamless:
    def test_preserves_shape_and_dtype(self):
        tile = checkerboard(128, 16)
        out = textures.make_seamless(tile)
        assert out.shape == tile.shape
        assert out.dtype == np.uint8

    def test_reduces_the_seam_on_a_gradient(self):
        tile = gradient_tile(128)
        before = textures.tiling_seam_error(tile)
        after = textures.tiling_seam_error(textures.make_seamless(tile))
        assert after < before

    def test_is_deterministic(self):
        tile = gradient_tile(64)
        a = textures.make_seamless(tile)
        b = textures.make_seamless(tile)
        assert np.array_equal(a, b)

    def test_handles_a_greyscale_tile(self):
        grey = np.tile(np.linspace(0, 255, 64, dtype=np.uint8), (64, 1))
        out = textures.make_seamless(grey)
        assert out.shape == grey.shape

    def test_keeps_the_overall_brightness(self):
        tile = checkerboard(128, 16)
        out = textures.make_seamless(tile)
        assert abs(float(out.mean()) - float(tile.mean())) < 12


class TestSeamError:
    def test_zero_for_a_flat_tile(self):
        flat = np.full((64, 64, 3), 100, dtype=np.uint8)
        assert textures.tiling_seam_error(flat) == pytest.approx(0.0)

    def test_large_for_a_strong_gradient(self):
        assert textures.tiling_seam_error(gradient_tile(64)) > 50


class TestNormalMap:
    def test_shape_and_dtype(self):
        out = textures.normal_from_luminance(checkerboard(128, 16))
        assert out.shape == (128, 128, 3)
        assert out.dtype == np.uint8

    def test_flat_input_gives_a_flat_normal(self):
        flat = np.full((64, 64, 3), 128, dtype=np.uint8)
        out = textures.normal_from_luminance(flat)
        # x and y components should sit at the 128 midpoint.
        assert abs(float(out[:, :, 2].mean()) - 128) < 2
        assert abs(float(out[:, :, 1].mean()) - 128) < 2

    def test_blue_channel_dominates(self):
        # Tangent-space normals mostly point out of the surface (+Z).
        out = textures.normal_from_luminance(checkerboard(128, 16))
        assert out[:, :, 0].mean() > 128

    def test_edges_produce_deflection(self):
        out = textures.normal_from_luminance(checkerboard(128, 16))
        assert out[:, :, 2].std() > 5


class TestRoughness:
    def test_shape_and_range(self):
        out = textures.default_roughness(checkerboard(128, 16))
        assert out.shape == (128, 128)
        assert out.min() >= 0 and out.max() <= 255

    def test_flat_surface_is_near_the_base(self):
        flat = np.full((64, 64, 3), 128, dtype=np.uint8)
        out = textures.default_roughness(flat, base=0.65)
        assert abs(float(out.mean()) / 255 - 0.5) < 0.1


class TestDecodeAndEncode:
    def test_round_trips_a_png(self):
        img = checkerboard(64, 8)
        data = textures.encode_png(img)
        back = textures.strip_exif_and_decode(data)
        assert back.shape == img.shape

    def test_rejects_garbage(self):
        with pytest.raises(ValueError):
            textures.strip_exif_and_decode(b"this is not an image")

    def test_decoding_drops_exif(self):
        img = checkerboard(64, 8)
        ok, buf = cv2.imencode(".jpg", img)
        assert ok
        decoded = textures.strip_exif_and_decode(buf.tobytes())
        # Only pixels survive — there is no metadata on a numpy array.
        assert isinstance(decoded, np.ndarray)
        assert decoded.shape[2] == 3


class TestUploadEndpoints:
    def _png(self, size=256) -> bytes:
        return textures.encode_png(checkerboard(size, 32))

    def test_rejects_an_unsupported_type(self, client):
        resp = client.post(
            "/api/uploads/floor",
            data={"project_id": "demo-01"},
            files={"file": ("x.gif", b"GIF89a", "image/gif")},
        )
        assert resp.status_code == 415

    def test_rejects_an_oversized_file(self, client, settings, monkeypatch):
        monkeypatch.setattr(settings, "max_upload_bytes", 100)
        resp = client.post(
            "/api/uploads/floor",
            data={"project_id": "demo-01"},
            files={"file": ("x.png", self._png(), "image/png")},
        )
        assert resp.status_code == 413

    def test_accepts_a_photo_and_proposes_corners(self, client):
        resp = client.post(
            "/api/uploads/floor",
            data={"project_id": "demo-01"},
            files={"file": ("tile.png", self._png(), "image/png")},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["corners"]) == 4
        assert body["width"] == 256

    def test_rectify_produces_three_maps(self, client):
        up = client.post(
            "/api/uploads/floor",
            data={"project_id": "demo-01"},
            files={"file": ("tile.png", self._png(), "image/png")},
        ).json()
        resp = client.post(
            "/api/uploads/floor/rectify",
            json={
                "upload_id": up["upload_id"],
                "project_id": "demo-01",
                "corners": up["corners"],
                "tile_size_cm": 60,
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["tile_size_m"] == [0.6, 0.6]
        assert body["albedo_url"] and body["normal_url"] and body["roughness_url"]

    def test_rectify_rejects_a_silly_tile_size(self, client):
        up = client.post(
            "/api/uploads/floor",
            data={"project_id": "demo-01"},
            files={"file": ("tile.png", self._png(), "image/png")},
        ).json()
        resp = client.post(
            "/api/uploads/floor/rectify",
            json={
                "upload_id": up["upload_id"],
                "project_id": "demo-01",
                "corners": up["corners"],
                "tile_size_cm": 9000,
            },
        )
        assert resp.status_code == 400

    def test_rectify_requires_four_corners(self, client):
        resp = client.post(
            "/api/uploads/floor/rectify",
            json={
                "upload_id": "nope",
                "project_id": "demo-01",
                "corners": [[0, 0], [1, 1]],
                "tile_size_cm": 60,
            },
        )
        assert resp.status_code == 400

    def test_rectify_on_a_missing_upload_is_404(self, client):
        resp = client.post(
            "/api/uploads/floor/rectify",
            json={
                "upload_id": "does-not-exist",
                "project_id": "demo-01",
                "corners": [[0, 0], [10, 0], [10, 10], [0, 10]],
                "tile_size_cm": 60,
            },
        )
        assert resp.status_code == 404
