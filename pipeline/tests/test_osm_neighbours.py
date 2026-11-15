"""
scripts/osm_neighbours.py: the houses round a flat, placed by its measured
bearings. A sign wrong here puts the neighbours behind the wrong window.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "unreal"))

import osm_neighbours  # noqa: E402
import prepare_flat  # noqa: E402

BEARINGS = {"minusZ_balcony": 191.5, "plusX_endWall": 281.5, "plusZ": 11.5}


def test_scene_xz_undoes_to_east_north():
    for x, z in ((3.0, -2.0), (-5.5, 4.25), (0.0, 1.0)):
        east, north = prepare_flat.to_east_north(x, z, BEARINGS)
        assert osm_neighbours.scene_xz(east, north, BEARINGS) == pytest.approx((x, z), abs=1e-3)


def test_a_house_south_of_the_flat_is_out_of_the_balcony_side():
    # Due south is 11.5 deg east of the balcony's normal (191.5 deg): mostly -z, a little -x.
    x, z = osm_neighbours.scene_xz(0.0, -20.0, BEARINGS)
    assert z == pytest.approx(-20 * math.cos(math.radians(11.5)), abs=1e-3)
    assert x == pytest.approx(-20 * math.sin(math.radians(11.5)), abs=1e-3)


def _osm(points: list[tuple[float, float]], way: int, kind: str = "yes") -> dict:
    lat0, lon0 = 48.0, 16.0
    kx = math.cos(math.radians(lat0)) * osm_neighbours.METRES_PER_DEG_LON_EQUATOR
    nodes = [{"type": "node", "id": way * 10 + i,
              "lat": lat0 + n / osm_neighbours.METRES_PER_DEG_LAT, "lon": lon0 + e / kx}
             for i, (e, n) in enumerate(points)]
    ids = [n["id"] for n in nodes]
    return {"elements": [*nodes, {"type": "way", "id": way, "nodes": [*ids, ids[0]],
                                  "tags": {"building": kind}}]}


def test_blocks_keep_area_and_skip_the_flats_own_building():
    site = {"sceneOrigin": {"latitude": 48.0, "longitude": 16.0}, "bearings": BEARINGS}
    house = _osm([(30, 0), (40, 0), (40, 12), (30, 12)], 1)
    own = _osm([(-5, -5), (5, -5), (5, 5), (-5, 5)], 2)
    osm = {"elements": house["elements"] + own["elements"]}
    out = osm_neighbours.blocks(osm, site, own=2, radius=100)
    assert [b["id"] for b in out] == ["osm-1"]
    b = out[0]
    w, d = b["max"][0] - b["min"][0], b["max"][2] - b["min"][2]
    assert w * d == pytest.approx(120, rel=0.02)
    assert b["min"][1] == -osm_neighbours.FLOOR_ABOVE_GROUND
    assert b["max"][1] - b["min"][1] == pytest.approx(6.8)


def test_far_houses_are_left_out():
    site = {"sceneOrigin": {"latitude": 48.0, "longitude": 16.0}, "bearings": BEARINGS}
    far = _osm([(300, 0), (310, 0), (310, 10), (300, 10)], 3)
    assert osm_neighbours.blocks(far, site, own=0, radius=120) == []
