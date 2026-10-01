"""
Seed the floor catalog (M3).

Licensing, per docs/SPEC.md §2: every entry records its licence and source.
Entries whose maps come from an external CC0 library are written with the
source URL but WITHOUT downloading anything — the brief requires asking
before downloading large assets. Procedural patterns are generated locally
and are ours, released CC0.

Run:  pnpm seed:floors
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "api" / "src"))

import cv2  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
CATALOG = REPO / "catalog" / "floors"
THUMBS = CATALOG / "thumbnails"

# ---------------------------------------------------------------------------
# Catalogue entries sourced from CC0 libraries.
#
# maps are left empty: the viewer falls back to a per-category colour until
# the texture files are fetched. Fetching is a separate, explicit step so we
# never pull hundreds of MB without being asked.
# ---------------------------------------------------------------------------

SOURCED: list[dict] = [
    {
        "id": "oak-herringbone-light",
        "name": "Light oak herringbone",
        "category": "wood",
        "tags": ["warm", "scandinavian", "classic", "oak", "pale", "parquet"],
        "description": "Pale oak in a herringbone lay. Bright and calm.",
        "tileSizeM": [0.6, 0.6],
        "glossy": False,
        "license": "CC0",
        "source": "https://ambientcg.com/view?id=WoodFloor041",
    },
    {
        "id": "oak-plank-natural",
        "name": "Natural oak plank",
        "category": "wood",
        "tags": ["warm", "neutral", "oak", "plank", "modern"],
        "description": "Wide natural oak boards with a soft matt finish.",
        "tileSizeM": [1.2, 0.2],
        "glossy": False,
        "license": "CC0",
        "source": "https://ambientcg.com/view?id=WoodFloor043",
    },
    {
        "id": "walnut-dark-plank",
        "name": "Dark walnut plank",
        "category": "wood",
        "tags": ["dark", "warm", "walnut", "traditional", "rich"],
        "description": "Deep walnut boards. Grounding, suits bright rooms.",
        "tileSizeM": [1.2, 0.18],
        "glossy": False,
        "license": "CC0",
        "source": "https://ambientcg.com/view?id=WoodFloor051",
    },
    {
        "id": "ash-pale-wide",
        "name": "Pale ash",
        "category": "wood",
        "tags": ["pale", "cool", "minimalist", "nordic", "ash"],
        "description": "Almost-white ash with a fine grain.",
        "tileSizeM": [1.4, 0.22],
        "glossy": False,
        "license": "CC0",
        "source": "https://ambientcg.com/view?id=WoodFloor048",
    },
    {
        "id": "terracotta-rustic",
        "name": "Rustic terracotta",
        "category": "tile",
        "tags": ["warm", "tuscan", "rustic", "earthy", "terracotta", "mediterranean"],
        "description": "Aged terracotta with uneven firing. Mediterranean.",
        "tileSizeM": [0.3, 0.3],
        "glossy": False,
        "license": "CC0",
        "source": "https://ambientcg.com/view?id=Tiles074",
    },
    {
        "id": "terrazzo-grey",
        "name": "Grey terrazzo",
        "category": "tile",
        "tags": ["cool", "speckled", "mid-century", "contemporary", "terrazzo"],
        "description": "Fine grey terrazzo with pale chips.",
        "tileSizeM": [0.6, 0.6],
        "glossy": True,
        "license": "CC0",
        "source": "https://ambientcg.com/view?id=Terrazzo009",
    },
    {
        "id": "tile-square-white",
        "name": "White square tile",
        "category": "tile",
        "tags": ["bright", "clean", "bathroom", "kitchen", "white"],
        "description": "Plain white square tile with a fine grout line.",
        "tileSizeM": [0.2, 0.2],
        "glossy": True,
        "license": "CC0",
        "source": "https://ambientcg.com/view?id=Tiles101",
    },
    {
        "id": "marble-carrara",
        "name": "Carrara marble",
        "category": "stone",
        "tags": ["cool", "luxury", "veined", "white", "classic", "marble"],
        "description": "White marble with soft grey veining.",
        "tileSizeM": [0.8, 0.8],
        "glossy": True,
        "license": "CC0",
        "source": "https://ambientcg.com/view?id=Marble016",
    },
    {
        "id": "concrete-polished",
        "name": "Polished concrete",
        "category": "stone",
        "tags": ["cool", "industrial", "minimalist", "grey", "loft", "concrete"],
        "description": "Smooth polished concrete, lightly mottled.",
        "tileSizeM": [1.0, 1.0],
        "glossy": True,
        "license": "CC0",
        "source": "https://ambientcg.com/view?id=Concrete034",
    },
    {
        "id": "limestone-sand",
        "name": "Sand limestone",
        "category": "stone",
        "tags": ["warm", "neutral", "soft", "limestone", "coastal"],
        "description": "Pale sandy limestone with a honed finish.",
        "tileSizeM": [0.6, 0.6],
        "glossy": False,
        "license": "CC0",
        "source": "https://ambientcg.com/view?id=Rock030",
    },
    {
        "id": "slate-charcoal",
        "name": "Charcoal slate",
        "category": "stone",
        "tags": ["dark", "cool", "textured", "slate", "contemporary"],
        "description": "Riven charcoal slate with a matt surface.",
        "tileSizeM": [0.4, 0.4],
        "glossy": False,
        "license": "CC0",
        "source": "https://ambientcg.com/view?id=Slate [placeholder]",
    },
]

# ---------------------------------------------------------------------------
# Procedural patterns. Generated here, so they are ours and CC0.
# ---------------------------------------------------------------------------

PROCEDURAL = [
    {
        "id": "painted-board-white",
        "name": "White painted boards",
        "category": "painted",
        "tags": ["bright", "cottage", "painted", "white", "nordic"],
        "description": "Floorboards painted a chalky white.",
        "tileSizeM": [1.2, 0.18],
        "kind": "boards",
        "colours": [(238, 236, 231), (226, 223, 216)],
    },
    {
        "id": "painted-board-sage",
        "name": "Sage painted boards",
        "category": "painted",
        "tags": ["muted", "green", "cottage", "painted", "calm"],
        "description": "Soft sage-green floorboards.",
        "tileSizeM": [1.2, 0.18],
        "kind": "boards",
        "colours": [(168, 178, 160), (158, 169, 150)],
    },
    {
        "id": "stencil-victorian-geometric",
        "name": "Victorian geometric stencil",
        "category": "stencilled",
        "tags": ["pattern", "victorian", "geometric", "classic", "monochrome"],
        "description": "Black and buff geometric pattern in the Victorian manner.",
        "tileSizeM": [0.4, 0.4],
        "kind": "checker",
        "colours": [(44, 42, 40), (214, 203, 182)],
    },
    {
        "id": "stencil-moroccan-blue",
        "name": "Moroccan blue stencil",
        "category": "stencilled",
        "tags": ["pattern", "moroccan", "blue", "eclectic", "bold"],
        "description": "Blue and white stencilled tile pattern.",
        "tileSizeM": [0.3, 0.3],
        "kind": "quatrefoil",
        "colours": [(42, 78, 140), (240, 238, 232)],
    },
    {
        "id": "vintage-parquet-chevron",
        "name": "Vintage chevron parquet",
        "category": "vintage",
        "tags": ["warm", "chevron", "parquet", "period", "golden"],
        "description": "Golden chevron parquet with age in the finish.",
        "tileSizeM": [0.5, 0.5],
        "kind": "chevron",
        "colours": [(164, 118, 70), (142, 100, 58)],
    },
]

SIZE = 512


def _boards(colours) -> np.ndarray:
    img = np.zeros((SIZE, SIZE, 3), np.uint8)
    rng = np.random.default_rng(3)
    board_h = SIZE // 6
    for i in range(6):
        base = np.array(colours[i % 2], dtype=np.float32)
        tint = base * rng.uniform(0.96, 1.04)
        img[i * board_h : (i + 1) * board_h] = np.clip(tint, 0, 255)
        cv2.line(img, (0, i * board_h), (SIZE, i * board_h), (0, 0, 0), 1)
    grain = rng.normal(0, 3.2, (SIZE, SIZE, 1))
    return np.clip(img + grain, 0, 255).astype(np.uint8)


def _checker(colours) -> np.ndarray:
    img = np.zeros((SIZE, SIZE, 3), np.uint8)
    cell = SIZE // 4
    for y in range(4):
        for x in range(4):
            colour = colours[(x + y) % 2]
            img[y * cell : (y + 1) * cell, x * cell : (x + 1) * cell] = colour
    return img


def _quatrefoil(colours) -> np.ndarray:
    fg, bg = colours
    img = np.full((SIZE, SIZE, 3), bg, np.uint8)
    c = SIZE // 2
    r = SIZE // 4
    for dx, dy in ((-r, 0), (r, 0), (0, -r), (0, r)):
        cv2.circle(img, (c + dx, c + dy), r, fg, -1)
    cv2.circle(img, (c, c), r // 2, bg, -1)
    for corner in ((0, 0), (SIZE, 0), (0, SIZE), (SIZE, SIZE)):
        cv2.circle(img, corner, r // 2, fg, -1)
    return img


def _chevron(colours) -> np.ndarray:
    img = np.zeros((SIZE, SIZE, 3), np.uint8)
    strip = SIZE // 8
    for i in range(16):
        colour = colours[i % 2]
        pts = np.array(
            [
                [0, i * strip],
                [SIZE // 2, i * strip - strip],
                [SIZE, i * strip],
                [SIZE, i * strip + strip],
                [SIZE // 2, i * strip],
                [0, i * strip + strip],
            ],
            np.int32,
        )
        cv2.fillPoly(img, [pts], colour)
    rng = np.random.default_rng(11)
    return np.clip(img + rng.normal(0, 3.0, (SIZE, SIZE, 1)), 0, 255).astype(np.uint8)


GENERATORS = {
    "boards": _boards,
    "checker": _checker,
    "quatrefoil": _quatrefoil,
    "chevron": _chevron,
}


def main() -> int:
    CATALOG.mkdir(parents=True, exist_ok=True)
    THUMBS.mkdir(parents=True, exist_ok=True)

    written = 0
    for entry in SOURCED:
        record = dict(entry)
        record["maps"] = {}
        record["note"] = (
            "Texture maps not downloaded yet. Fetch from `source` and place under "
            "data/textures/, then fill `maps`."
        )
        (CATALOG / f"{entry['id']}.json").write_text(
            json.dumps(record, indent=2) + "\n", encoding="utf-8"
        )
        written += 1

    for spec in PROCEDURAL:
        image = GENERATORS[spec["kind"]](spec["colours"])
        thumb_path = THUMBS / f"{spec['id']}.png"
        cv2.imwrite(str(thumb_path), cv2.resize(image, (192, 192), interpolation=cv2.INTER_AREA))

        record = {
            k: v for k, v in spec.items() if k not in ("kind", "colours")
        }
        record["glossy"] = False
        record["license"] = "CC0"
        record["source"] = "Generated procedurally by scripts/seed_floors.py"
        record["maps"] = {"albedo": f"/catalog/floors/thumbnails/{spec['id']}.png"}
        record["thumbnail"] = f"/catalog/floors/thumbnails/{spec['id']}.png"
        (CATALOG / f"{spec['id']}.json").write_text(
            json.dumps(record, indent=2) + "\n", encoding="utf-8"
        )
        written += 1

    print(f"Wrote {written} floor entries to {CATALOG.relative_to(REPO)}")
    print(f"  {len(SOURCED)} sourced (CC0, maps not yet downloaded)")
    print(f"  {len(PROCEDURAL)} procedural (CC0, generated here)")
    print("Record every entry in docs/LICENSES.md before shipping.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
