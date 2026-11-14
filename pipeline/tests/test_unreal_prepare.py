"""
scripts/unreal/prepare_flat.py: the model and the placement facts handed to
Unreal. A wrong bearing or a mirrored axis there puts the flat's windows on
the wrong street, and nothing in the render says so.
"""

from __future__ import annotations

import json
import math
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "unreal"))

import prepare_flat  # noqa: E402

BEARINGS = {"minusZ_balcony": 191.5, "plusX_endWall": 281.5, "plusZ": 11.5}


def test_scene_axes_go_to_their_compass_bearings():
    # One metre out of the balcony side (-z) goes towards 191.5 deg: mostly south, a bit west.
    east, north = prepare_flat.to_east_north(0.0, -1.0, BEARINGS)
    assert math.degrees(math.atan2(east, north)) % 360 == pytest.approx(191.5, abs=0.01)
    # One metre along +x goes towards 281.5 deg: mostly west.
    east, north = prepare_flat.to_east_north(1.0, 0.0, BEARINGS)
    assert math.degrees(math.atan2(east, north)) % 360 == pytest.approx(281.5, abs=0.01)
    assert math.hypot(east, north) == pytest.approx(1.0, abs=1e-4)


def test_the_plan_is_turned_not_mirrored():
    # Seen from above with north up, +x is a quarter turn clockwise of -z.
    ax = prepare_flat.to_east_north(1.0, 0.0, BEARINGS)
    amz = prepare_flat.to_east_north(0.0, -1.0, BEARINGS)
    cross = amz[0] * ax[1] - amz[1] * ax[0]
    assert cross == pytest.approx(-1.0, abs=1e-3)


def test_outline_grows_outwards_whichever_way_it_winds():
    square = [[0, 0], [4, 0], [4, 2], [0, 2]]
    for poly in (square, square[::-1]):
        grown = prepare_flat.offset_outline(poly, 0.5)
        xs, ys = [p[0] for p in grown], [p[1] for p in grown]
        assert (min(xs), max(xs), min(ys), max(ys)) == pytest.approx((-0.5, 4.5, -0.5, 2.5))


def test_quat_rotate_quarter_turn_about_y():
    s = math.sqrt(0.5)
    assert prepare_flat.quat_rotate([0, s, 0, s], [1, 0, 0]) == pytest.approx([0, 0, -1], abs=1e-9)


def _glb(gltf: dict, binary: bytes = b"\0\0\0\0") -> bytes:
    text = json.dumps(gltf).encode()
    text += b" " * (-len(text) % 4)
    body = (struct.pack("<II", len(text), 0x4E4F534A) + text
            + struct.pack("<II", len(binary), 0x004E4942) + binary)
    return struct.pack("<III", 0x46546C67, 2, 12 + len(body)) + body


def test_main_drops_the_backdrop_and_baked_light_and_opens_the_sliding_door(tmp_path, monkeypatch):
    accessor = {"min": [-1, 0, -1], "max": [1, 2, 1]}
    names = ["backdrop-houses", "barn-door", *prepare_flat.PINS]
    gltf = {
        "scenes": [{"nodes": list(range(len(names)))}],
        "nodes": [{"name": n, "mesh": 0, "translation": [float(i), 0.0, 0.0]}
                  for i, n in enumerate(names)],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0, "_LIGHT_1845": 0}}]}],
        "accessors": [accessor],
    }
    gltf["nodes"][1]["extras"] = {"door": {"open": 1.0, "close": 0.0, "slide": [0.0, 0.0, -0.8]}}
    root = tmp_path / "data" / "projects" / "p"
    (root / "scene").mkdir(parents=True)
    (root / "input").mkdir()
    (root / "scene" / "flat.glb").write_bytes(_glb(gltf))
    (root / "input" / "site.json").write_text(json.dumps({
        "sceneOrigin": {"latitude": 48.0, "longitude": 16.0}, "bearings": BEARINGS,
        "floor": {"storey": 1}, "render": {"date": "2026-10-15", "time": "15:45"}}))
    (root / "input" / "dressing.json").write_text(json.dumps({
        "plan": {"wallT": 0.4, "outline": [[0, 0], [4, 0], [4, 2], [0, 2]]}}))
    monkeypatch.setattr(prepare_flat, "REPO", tmp_path)

    prepare_flat.main("p")

    out, _ = prepare_flat.read_glb(root / "unreal" / "flat.glb")
    kept = [out["nodes"][i]["name"] for i in out["scenes"][0]["nodes"]]
    assert "backdrop-houses" not in kept and "barn-door" in kept
    assert out["meshes"][0]["primitives"][0]["attributes"] == {"POSITION": 0}
    assert out["nodes"][1]["translation"] == pytest.approx([1.0, 0.0, -0.8])
    scene = json.loads((root / "unreal" / "scene.json").read_text())
    # A pin is its node's centre: translation plus the mesh's middle (0, 1, 0).
    first = prepare_flat.PINS[0]
    assert scene["pins"][first] == pytest.approx([names.index(first), 1.0, 0.0])
    assert len(scene["outlineEastNorth"]) == 4
