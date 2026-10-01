"""
Seed the furniture and style catalogs (M6).

No GLB files are downloaded here. Per docs/SPEC.md §2 the posture is
"CC0 core plus flagged DEMO-ONLY extras", and the brief forbids downloading
large assets without asking. Every entry therefore records its intended CC0
source and is marked `assetStatus: "pending"` until the model is fetched and
its licence recorded in docs/LICENSES.md.

Dimensions are real so the layout solver (M7) and collision checks are
meaningful before any mesh exists.

Run:  pnpm seed:furniture
"""

from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FURNITURE = REPO / "catalog" / "furniture"
STYLES = REPO / "catalog" / "styles"

KHRONOS = "https://github.com/KhronosGroup/glTF-Sample-Assets (CC0/CC-BY per model)"
POLY_HAVEN = "https://polyhaven.com/models (CC0)"

# (id, name, category, styles, colour, materials, [w, h, d])
ITEMS: list[tuple[str, str, str, list[str], str, list[str], list[float]]] = [
    # Seating
    ("sofa-oslo-3", "Oslo 3-seat sofa", "sofa", ["modern", "minimalist"],
     "#c8c2b8", ["linen", "oak"], [2.10, 0.80, 0.90]),
    ("sofa-chesterfield", "Chesterfield sofa", "sofa", ["traditional"],
     "#5b3a2e", ["leather"], [2.00, 0.75, 0.95]),
    ("sofa-kjaer-2", "Kjaer 2-seat sofa", "sofa", ["mid-century"],
     "#7d8c7a", ["wool", "teak"], [1.65, 0.78, 0.85]),
    ("armchair-shell", "Shell armchair", "chair", ["mid-century", "modern"],
     "#b8743f", ["moulded ply"], [0.72, 0.80, 0.74]),
    ("chair-windsor", "Windsor chair", "chair", ["traditional"],
     "#6b4a2f", ["beech"], [0.46, 0.95, 0.48]),
    ("chair-stacking-ply", "Stacking ply chair", "chair", ["minimalist", "modern"],
     "#d9d2c4", ["plywood"], [0.45, 0.82, 0.50]),
    # Tables
    ("table-ercol-oval", "Oval dining table", "dining_table", ["mid-century", "traditional"],
     "#8a6a43", ["oak"], [1.60, 0.75, 0.90]),
    ("table-dining-trestle", "Trestle dining table", "dining_table", ["traditional"],
     "#6f5436", ["pine"], [1.80, 0.75, 0.85]),
    ("table-dining-slab", "Slab dining table", "dining_table", ["modern", "minimalist"],
     "#cfc9bd", ["concrete", "steel"], [1.70, 0.74, 0.88]),
    ("coffee-noguchi", "Sculptural coffee table", "coffee_table", ["mid-century"],
     "#4a3a2a", ["walnut", "glass"], [1.20, 0.40, 0.70]),
    ("coffee-block-oak", "Oak block coffee table", "coffee_table", ["minimalist", "modern"],
     "#b9955f", ["oak"], [1.00, 0.36, 0.55]),
    # Storage
    ("shelf-string-wall", "Wall shelving", "shelf", ["mid-century", "minimalist"],
     "#c9b998", ["ash", "steel"], [1.20, 1.60, 0.30]),
    ("sideboard-teak", "Teak sideboard", "sideboard", ["mid-century"],
     "#8b5e35", ["teak"], [1.60, 0.72, 0.45]),
    ("bookcase-painted", "Painted bookcase", "shelf", ["traditional"],
     "#e3ded2", ["painted pine"], [0.90, 1.90, 0.32]),
    ("wardrobe-plain", "Plain wardrobe", "wardrobe", ["minimalist", "modern"],
     "#ddd8cc", ["lacquer"], [1.20, 2.10, 0.60]),
    # Beds
    ("bed-double-upholstered", "Upholstered double bed", "bed", ["modern", "traditional"],
     "#9aa0a6", ["wool"], [1.60, 1.05, 2.05]),
    ("bed-double-oak", "Oak double bed", "bed", ["minimalist", "mid-century"],
     "#b08a52", ["oak"], [1.55, 0.90, 2.05]),
    # Accessories
    ("rug-flatweave-natural", "Natural flatweave rug", "rug", ["minimalist", "modern"],
     "#cfc3ad", ["jute"], [2.40, 0.01, 1.70]),
    ("rug-persian-faded", "Faded Persian rug", "rug", ["traditional", "eclectic"],
     "#9a4f43", ["wool"], [2.60, 0.01, 1.80]),
    ("lamp-arc-floor", "Arc floor lamp", "lamp", ["mid-century", "modern"],
     "#c0bcb4", ["steel", "marble"], [0.45, 1.95, 0.45]),
    ("lamp-tripod-wood", "Wooden tripod lamp", "lamp", ["minimalist", "traditional"],
     "#b89b74", ["beech", "linen"], [0.50, 1.55, 0.50]),
    ("plant-fiddle-fig", "Fiddle-leaf fig", "plant", ["modern", "minimalist", "mid-century"],
     "#3f6b40", ["foliage", "terracotta"], [0.70, 1.60, 0.70]),
    ("desk-simple-oak", "Simple oak desk", "desk", ["minimalist", "mid-century"],
     "#b58a57", ["oak"], [1.40, 0.74, 0.65]),
]

STYLE_DEFS = [
    {
        "id": "modern",
        "name": "Modern",
        "description": "Clean lines, low contrast, a calm neutral palette with one "
                       "quiet accent. Nothing ornate.",
        "palette": ["#f4f1ec", "#c8c2b8", "#8d8578", "#2f3338"],
        "materials": ["linen", "oak", "steel", "lacquer"],
        "floorIds": ["oak-plank-natural", "concrete-polished", "ash-pale-wide"],
        "picks": {
            "living": ["sofa-oslo-3", "coffee-block-oak", "rug-flatweave-natural",
                       "lamp-arc-floor", "plant-fiddle-fig"],
            "bedroom": ["bed-double-upholstered", "wardrobe-plain", "lamp-tripod-wood"],
            "dining": ["table-dining-slab", "chair-stacking-ply"],
            "office": ["desk-simple-oak", "chair-stacking-ply", "shelf-string-wall"],
        },
        "complete": True,
    },
    {
        "id": "minimalist",
        "name": "Minimalist",
        "description": "Few pieces, pale woods, generous empty floor. Every object "
                       "has to earn its place.",
        "palette": ["#faf9f6", "#e8e6e0", "#b9b2a6", "#14171c"],
        "materials": ["ash", "plywood", "jute"],
        "floorIds": ["ash-pale-wide", "painted-board-white", "oak-herringbone-light"],
        "picks": {
            "living": ["sofa-oslo-3", "coffee-block-oak", "plant-fiddle-fig"],
            "bedroom": ["bed-double-oak", "wardrobe-plain"],
            "dining": ["table-dining-slab", "chair-stacking-ply"],
            "office": ["desk-simple-oak", "chair-stacking-ply"],
        },
        "complete": True,
    },
    {
        "id": "mid-century",
        "name": "Mid-Century Modern",
        "description": "Teak and walnut, tapered legs, olive and burnt orange against "
                       "warm neutrals.",
        "palette": ["#e5dcc9", "#b8743f", "#7d8c7a", "#4a3a2a"],
        "materials": ["teak", "walnut", "wool", "moulded ply"],
        "floorIds": ["oak-herringbone-light", "vintage-parquet-chevron", "terrazzo-grey"],
        "picks": {
            "living": ["sofa-kjaer-2", "armchair-shell", "coffee-noguchi",
                       "sideboard-teak", "lamp-arc-floor"],
            "bedroom": ["bed-double-oak", "sideboard-teak"],
            "dining": ["table-ercol-oval", "armchair-shell"],
            "office": ["desk-simple-oak", "shelf-string-wall"],
        },
        "complete": True,
    },
    {
        "id": "traditional",
        "name": "Traditional",
        "description": "Dark woods, deep upholstery, patterned rugs and symmetry.",
        "palette": ["#efe7d7", "#9a4f43", "#5b3a2e", "#2c2a28"],
        "materials": ["leather", "mahogany", "wool", "brass"],
        "floorIds": ["walnut-dark-plank", "terracotta-rustic",
                     "stencil-victorian-geometric"],
        "picks": {
            "living": ["sofa-chesterfield", "chair-windsor", "rug-persian-faded",
                       "bookcase-painted", "lamp-tripod-wood"],
            "bedroom": ["bed-double-upholstered", "bookcase-painted"],
            "dining": ["table-dining-trestle", "chair-windsor"],
            "office": ["desk-simple-oak", "bookcase-painted"],
        },
        "complete": True,
    },
    # Prepared but not stocked, per the brief.
    {
        "id": "contemporary",
        "name": "Contemporary",
        "description": "Current, gallery-like: large forms, matt finishes, restrained colour.",
        "palette": ["#f2f0ec", "#a8a49c", "#3a3f49"],
        "materials": ["concrete", "boucle", "blackened steel"],
        "floorIds": ["concrete-polished", "limestone-sand"],
        "picks": {},
        "complete": False,
    },
    {
        "id": "transitional",
        "name": "Transitional",
        "description": "Traditional shapes stripped of ornament, in a modern palette.",
        "palette": ["#ece6da", "#b89b74", "#4c5056"],
        "materials": ["oak", "linen", "brushed brass"],
        "floorIds": ["oak-plank-natural", "marble-carrara"],
        "picks": {},
        "complete": False,
    },
    {
        "id": "eclectic",
        "name": "Eclectic",
        "description": "Deliberate collisions: periods and patterns mixed with confidence.",
        "palette": ["#e8dcc4", "#2a4e8c", "#9a4f43", "#3f6b40"],
        "materials": ["velvet", "rattan", "painted timber"],
        "floorIds": ["stencil-moroccan-blue", "vintage-parquet-chevron"],
        "picks": {},
        "complete": False,
    },
]


def main() -> int:
    FURNITURE.mkdir(parents=True, exist_ok=True)
    STYLES.mkdir(parents=True, exist_ok=True)

    for item_id, name, category, styles, colour, materials, dims in ITEMS:
        record = {
            "id": item_id,
            "name": name,
            "category": category,
            "styles": styles,
            "colors": [colour],
            "materials": materials,
            "dimensionsM": dims,
            "glb": None,
            "thumbnail": None,
            "assetStatus": "pending",
            "license": "CC0",
            "source": POLY_HAVEN if category in ("plant", "lamp") else KHRONOS,
            "note": (
                "No mesh downloaded yet. The viewer draws a correctly sized placeholder "
                "box. Record the real licence in docs/LICENSES.md when the GLB is added."
            ),
        }
        (FURNITURE / f"{item_id}.json").write_text(
            json.dumps(record, indent=2) + "\n", encoding="utf-8"
        )

    for style in STYLE_DEFS:
        (STYLES / f"{style['id']}.json").write_text(
            json.dumps(style, indent=2) + "\n", encoding="utf-8"
        )

    stocked = sum(1 for s in STYLE_DEFS if s["complete"])
    print(f"Wrote {len(ITEMS)} furniture entries to {FURNITURE.relative_to(REPO)}")
    print(f"Wrote {len(STYLE_DEFS)} styles ({stocked} fully stocked, "
          f"{len(STYLE_DEFS) - stocked} prepared)")
    print("No GLB files downloaded. Every entry is assetStatus=pending.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
