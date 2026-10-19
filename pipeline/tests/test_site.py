"""The flat's surroundings from OpenStreetMap, and the sun through the day."""

from __future__ import annotations

import json
import math

import pytest
from walkthrough_pipeline import flat_geometry as fg
from walkthrough_pipeline import site
from walkthrough_pipeline.rebuild import sun

ORIGIN = (48.0, 16.0)


def _osm(tmp_path, ways: dict[int, tuple[list[tuple[float, float]], dict]]):
    """An Overpass answer with square-ish buildings given in metres east/north of ORIGIN."""
    elements, node_id = [], 1
    per_deg_lon = site.METRES_PER_DEG_LON_EQUATOR * math.cos(math.radians(ORIGIN[0]))
    for way_id, (corners, tags) in ways.items():
        ids = []
        for east, north in corners:
            lat = ORIGIN[0] + north / site.METRES_PER_DEG_LAT
            lon = ORIGIN[1] + east / per_deg_lon
            elements.append({"type": "node", "id": node_id, "lat": lat, "lon": lon})
            ids.append(node_id)
            node_id += 1
        elements.append({"type": "way", "id": way_id, "nodes": [*ids, ids[0]],
                         "tags": {"building": "yes", **tags}})
    path = tmp_path / "osm.json"
    path.write_text(json.dumps({"elements": elements}))
    return path


def test_heights_follow_floor_counts():
    eaves, top = site.heights({"building:levels": "4", "roof:levels": "1"})
    assert eaves == pytest.approx(site.GROUND_FLOOR_M + 3 * site.FLOOR_M)
    assert top == pytest.approx(eaves + site.ROOF_FLOOR_M)
    assert site.heights({"building:levels": "x"})[0] == pytest.approx(
        site.GROUND_FLOOR_M + (site.DEFAULT_LEVELS - 1) * site.FLOOR_M)


def test_neighbours_land_in_front_of_the_windows(tmp_path):
    # The flat's house faces north onto a street; across it, 20 m north, a
    # house of 5 floors. The windows face north (bearing 0).
    osm = _osm(tmp_path, {
        1: ([(-5, -10), (5, -10), (5, 0), (-5, 0)], {"building:levels": "4"}),
        2: ([(-8, 20), (8, 20), (8, 32), (-8, 32)], {"building:levels": "5"}),
    })
    out = site.surroundings(osm, ORIGIN, building=1, bearing=0, floor_height=15.6,
                            knee_wall_z=3.7)
    assert out["facing"] == pytest.approx(0, abs=0.5)
    (house,) = out["buildings"]  # the flat's own house is left out
    zs = [p[1] for p in house["polygon"]]
    # Its near side is 20 m out from the facade, which is 0.45 m past the knee wall.
    assert min(zs) == pytest.approx(3.7 + 0.45 + 20, abs=0.1)
    xs = [p[0] for p in house["polygon"]]
    assert min(xs) == pytest.approx(-8, abs=0.1) and max(xs) == pytest.approx(8, abs=0.1)
    assert fg.signed_area(house["polygon"]) > 0
    assert house["base"] == pytest.approx(-15.6)


def test_the_window_side_is_the_one_facing_the_bearing(tmp_path):
    osm = _osm(tmp_path, {1: ([(0, 0), (10, 0), (10, 6), (0, 6)], {})})
    nodes, ways = site.load(osm)
    poly = site.outline(ways[0], nodes, ORIGIN)
    a, b = site.facing_side(poly, 180)  # the south side, y = 0
    assert a[1] == pytest.approx(0, abs=0.01) and b[1] == pytest.approx(0, abs=0.01)


def test_the_sun_warms_and_dims_towards_evening():
    dressing = {"location": {"latitude": 48.21, "longitude": 16.37, "utcOffset": 2},
                "sun": {"date": "2026-06-21", "time": "12:00"}, "windowsFace": 324}
    noon = sun(dressing, (0.0, 1.0), 324)
    evening = sun(dressing, (0.0, 1.0), 324, "20:30")
    assert noon["elevation"] > 60 and evening["elevation"] < 5
    assert evening["colour"][2] < noon["colour"][2]  # less blue: warmer
    assert evening["energy"] < noon["energy"] and evening["sky"] < noon["sky"]
    assert noon["time"] == "12:00" and evening["time"] == "20:30"
