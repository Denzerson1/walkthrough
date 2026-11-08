"""
Replacement floors at their real size. The catalog had the oak planks'
square 1.9 m texture laid over 1.2 x 0.2 m — planks squashed six times
along their length — and tiles up to seven times too small.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FLOORS = sorted((ROOT / "catalog" / "floors").glob("*.json"))

#: ambientCG's stated physical size of each texture downloaded
#: (api/v2/full_json?include=dimensionsData), metres.
PUBLISHED = {
    "WoodFloor039": (1.9, 1.9), "Concrete034": (1.1, 0.55), "WoodFloor041": (1.9, 1.9),
    "WoodFloor040": (1.9, 1.9), "Tiles074": (2.1, 2.1), "Tiles101": (1.0, 1.0),
    "WoodFloor048": (1.3, 1.3), "Tiles103": (1.0, 1.0), "Tiles100": (1.0, 1.0),
    "WoodFloor037": (1.9, 1.9), "Tiles093": (2.45, 2.45),
}


def _floors():
    return [json.loads(p.read_text(encoding="utf-8")) for p in FLOORS]


@pytest.mark.parametrize("floor", _floors(), ids=lambda f: f["id"])
def test_a_texture_is_laid_at_its_published_size(floor):
    asset = floor.get("source", "").rsplit("id=", 1)[-1]
    if asset not in PUBLISHED:
        pytest.skip(f"{asset}: no published size")
    assert tuple(floor["tileSizeM"]) == PUBLISHED[asset]


@pytest.mark.parametrize("floor", _floors(), ids=lambda f: f["id"])
def test_a_tile_has_its_texture_aspect(floor):
    """A tile of another shape than its image stretches the pattern."""
    cv2 = pytest.importorskip("cv2")
    albedo = (floor.get("maps") or {}).get("albedo", "")
    image = ROOT / "data" / albedo.removeprefix("/files/") if albedo else None
    if image is None or not image.is_file():
        pytest.skip("texture not downloaded (pnpm seed:assets)")
    h, w = cv2.imread(str(image)).shape[:2]
    tw, th = floor["tileSizeM"]
    assert tw / th == pytest.approx(w / h, rel=0.01)
