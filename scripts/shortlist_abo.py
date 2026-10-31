"""
Shortlist Amazon Berkeley Objects (ABO, CC BY 4.0, credit "Amazon.com") for
the style kits, from the metadata only: no model is downloaded here.

    uv run python scripts/shortlist_abo.py

Needs the metadata under data/abo/ (git-ignored), about 95 MB:

    base=https://amazon-berkeley-objects.s3.amazonaws.com
    curl -o data/abo/3dmodels.csv.gz $base/3dmodels/metadata/3dmodels.csv.gz
    curl -o data/abo/images.csv.gz $base/images/metadata/images.csv.gz
    curl $base/archives/abo-listings.tar | tar -x -C data/abo

Writes data/abo/shortlist.json (per kit and category, the best-scoring
listings whose model's size agrees with the listing's) and a contact sheet
per category, data/abo/sheets/<category>.jpg, of their product photos, to
choose from by eye.
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import re
import sys
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
ABO = ROOT / "data" / "abo"
BASE = "https://amazon-berkeley-objects.s3.amazonaws.com"
#: Listing units, in metres.
UNITS = {"inches": 0.0254, "centimeters": 0.01, "millimeters": 0.001, "meters": 1.0, "feet": 0.3048}

#: Category: (ABO product types, a name pattern it must match, one it must not).
CATEGORIES: dict[str, tuple[set[str], str, str]] = {
    "sofa": (
        {"SOFA", "HOME_FURNITURE_AND_DECOR"},
        r"\bsofa|couch|loveseat",
        r"sectional|sleeper|futon|cover|bed\b|kids",
    ),
    "armchair": (
        {"CHAIR", "HOME_FURNITURE_AND_DECOR"},
        r"accent chair|armchair|arm chair|club chair|lounge chair|barrel chair",
        r"dining|office|desk|kids|youth|recliner|rocking|outdoor|patio|gaming",
    ),
    "coffee_table": (
        {"TABLE", "HOME_FURNITURE_AND_DECOR"},
        r"coffee table|cocktail table",
        r"set of|outdoor|patio",
    ),
    "side_table": (
        {"TABLE", "HOME_FURNITURE_AND_DECOR"},
        r"end table|side table|accent table",
        r"set of|outdoor|patio|nightstand",
    ),
    "nightstand": (
        {"TABLE", "CABINET", "HOME_FURNITURE_AND_DECOR", "DRESSER"},
        r"nightstand|night stand|bedside",
        r"set of 2|lamp",
    ),
    "rug": ({"RUG"}, r"rug", r"runner|round|outdoor|bath|doormat|door mat"),
    "floor_lamp": ({"LAMP"}, r"floor lamp|lampadaire|de pie|stehlampe", r"set of|bulb"),
    "table_lamp": (
        {"LAMP"},
        r"table lamp|de mesa|para mesa|desk lamp|tischlampe",
        r"set of 2|bulb|floor",
    ),
    "pendant_lamp": ({"LIGHT_FIXTURE", "LAMP"}, r"pendant|hanging|chandelier", r"plug-in|plug in"),
    "bed": (
        {"BED", "BED_FRAME"},
        r"\bbed\b|platform bed|queen|double",
        r"bunk|kids|twin|king|day ?bed|frame with st|foldable|heavy duty|mattress",
    ),
    "dresser": ({"DRESSER", "CABINET"}, r"dresser|chest of drawers", r"kids"),
    "dining_table": (
        {"TABLE"},
        r"dining table|kitchen table",
        r"set of|outdoor|patio|bar height|counter height",
    ),
    "dining_chair": (
        {"CHAIR"},
        r"dining chair|side chair|kitchen chair",
        r"outdoor|patio|bar|counter|kids",
    ),
    "desk": ({"DESK", "TABLE"}, r"\bdesk\b|writing table", r"standing|gaming|kids|corner|l-shaped"),
    "desk_chair": (
        {"CHAIR"},
        r"office chair|desk chair|task chair",
        r"gaming|kids|big and tall|executive",
    ),
    "shelf": (
        {"SHELF", "CABINET", "HOME_FURNITURE_AND_DECOR"},
        r"bookcase|bookshelf|shelving unit|etagere|shelf",
        r"wall|floating|corner|kids|ladder",
    ),
}

#: Kit: words that speak for it, in name, style, material, colour and keywords.
KITS: dict[str, list[str]] = {
    "modern-minimal": [
        "modern",
        "minimalist",
        "minimal",
        "contemporary",
        "sleek",
        "clean lines",
        "white",
        "black",
        "grey",
        "gray",
        "lacquer",
    ],
    "scandinavian": [
        "scandinavian",
        "nordic",
        "danish",
        "hygge",
        "natural",
        "oak",
        "beech",
        "ash",
        "light wood",
        "white",
        "wool",
        "felt",
    ],
    "japandi": [
        "japanese",
        "japandi",
        "zen",
        "low profile",
        "wabi",
        "natural wood",
        "solid wood",
        "walnut",
        "linen",
        "rattan",
        "paper",
        "bamboo",
        "minimal",
    ],
    "mid-century": [
        "mid-century",
        "mid century",
        "midcentury",
        "retro",
        "walnut",
        "tapered",
        "teak",
        "tufted",
        "60s",
        "eames",
    ],
    "industrial": [
        "industrial",
        "metal",
        "iron",
        "steel",
        "pipe",
        "reclaimed",
        "rustic",
        "leather",
        "factory",
        "black",
    ],
    "mediterranean": [
        "mediterranean",
        "rattan",
        "wicker",
        "terracotta",
        "jute",
        "coastal",
        "boho",
        "bohemian",
        "linen",
        "whitewash",
        "cane",
        "seagrass",
    ],
}

#: Real sizes outside these (metres: width, depth, height ranges) are not
#: what the slot means (a "sofa" 0.9 m wide is a chair).
SANE = {
    "sofa": ((1.5, 2.4), (0.75, 1.1), (0.65, 1.0)),
    "armchair": ((0.6, 1.0), (0.6, 1.0), (0.65, 1.1)),
    "coffee_table": ((0.6, 1.4), (0.4, 0.9), (0.3, 0.5)),
    "side_table": ((0.3, 0.7), (0.3, 0.7), (0.4, 0.7)),
    "nightstand": ((0.35, 0.7), (0.3, 0.55), (0.4, 0.75)),
    "rug": ((1.2, 3.0), (0.9, 2.5), (0.0, 0.05)),
    "floor_lamp": ((0.2, 0.7), (0.2, 0.7), (1.4, 1.9)),
    "table_lamp": ((0.15, 0.5), (0.15, 0.5), (0.3, 0.75)),
    "pendant_lamp": ((0.2, 0.8), (0.2, 0.8), (0.15, 1.2)),
    "bed": ((1.35, 1.85), (1.95, 2.3), (0.3, 1.4)),
    "dresser": ((0.6, 1.6), (0.35, 0.6), (0.7, 1.3)),
    "dining_table": ((0.75, 1.8), (0.75, 1.0), (0.72, 0.78)),
    "dining_chair": ((0.4, 0.65), (0.45, 0.65), (0.75, 1.05)),
    "desk": ((0.8, 1.5), (0.45, 0.8), (0.72, 0.8)),
    "desk_chair": ((0.5, 0.75), (0.5, 0.75), (0.8, 1.2)),
    "shelf": ((0.4, 1.2), (0.25, 0.45), (0.9, 2.1)),
}

PER_KIT = 6  # candidates per kit and category on the sheet


def english(values: list[dict] | None) -> str:
    for v in values or []:
        if v.get("language_tag", "").startswith("en"):
            return v["value"]
    return (values or [{}])[0].get("value", "")


def dimensions(listing: dict) -> tuple[float, float, float] | None:
    """The listing's (width, depth, height) in metres, or None."""
    d = listing.get("item_dimensions") or {}
    out = []
    for key in ("width", "length", "height"):
        v = (d.get(key) or {}).get("normalized_value") or d.get(key)
        if not v or v.get("value") is None:
            return None
        unit = v.get("unit", "inches").lower()
        if unit not in UNITS:
            return None
        out.append(float(v["value"]) * UNITS[unit])
    return out[0], out[1], out[2]


def agrees(real: tuple[float, float, float], extent: list[float], tolerance: float = 0.03) -> bool:
    """The model's box (x, y up, z, front +Z) is the listing's size, as the scale check asks."""
    w, d, h = real
    x, y, z = extent

    def near(a: float, b: float) -> bool:
        return abs(a - b) <= tolerance * max(a, b)

    return near(h, y) and near(w, x) and near(d, z)


def main() -> int:
    with gzip.open(ABO / "3dmodels.csv.gz", "rt") as f:
        models = {r["3dmodel_id"]: r for r in csv.DictReader(f)}
    with gzip.open(ABO / "images.csv.gz", "rt") as f:
        images = {r["image_id"]: r["path"] for r in csv.DictReader(f)}
    candidates: dict[str, list[dict]] = {c: [] for c in CATEGORIES}
    for path in sorted((ABO / "listings" / "metadata").glob("*.json.gz")):
        with gzip.open(path, "rt", encoding="utf-8") as f:
            listings = [json.loads(line) for line in f]
        for listing in listings:
            model = models.get(listing.get("3dmodel_id") or "")
            if not model:
                continue
            kind = [p["value"] for p in listing.get("product_type", [])]
            name = english(listing.get("item_name"))
            words = " ".join(
                [
                    name,
                    english(listing.get("style")),
                    english(listing.get("material")),
                    english(listing.get("color")),
                    *(k["value"] for k in listing.get("item_keywords", [])),
                ]
            ).lower()
            real = dimensions(listing)
            extent = [float(model[k]) for k in ("extent_x", "extent_y", "extent_z")]
            for category, (types, want, avoid) in CATEGORIES.items():
                if (
                    not (set(kind) & types)
                    or not re.search(want, name.lower())
                    or re.search(avoid, name.lower())
                ):
                    continue
                if category == "rug":  # a rug's listing gives no thickness worth checking
                    size = (max(extent[0], extent[2]), min(extent[0], extent[2]), extent[1])
                elif not real or not agrees(real, extent):
                    continue
                else:
                    size = real
                if not all(lo <= v <= hi for v, (lo, hi) in zip(size, SANE[category], strict=True)):
                    continue
                candidates[category].append(
                    {
                        "id": listing["3dmodel_id"],
                        "item": listing["item_id"],
                        "name": name,
                        "path": model["path"],
                        "faces": int(model["faces"]),
                        "sizeM": [round(v, 3) for v in size],
                        "image": images.get(listing.get("main_image_id")),
                        "scores": {kit: sum(w in words for w in kw) for kit, kw in KITS.items()},
                    }
                )
    shortlist: dict[str, dict[str, list[dict]]] = {}
    (ABO / "sheets").mkdir(exist_ok=True)
    for category, found in candidates.items():
        unique = {c["id"]: c for c in found}.values()
        shortlist[category] = {}
        for kit in KITS:
            ranked = sorted(unique, key=lambda c: (-c["scores"][kit], c["faces"]))
            shortlist[category][kit] = [c for c in ranked if c["scores"][kit] > 0][:PER_KIT]
        sheet(category, shortlist[category])
        print(f"{category:14} {len(unique):4} with matching size")
    (ABO / "shortlist.json").write_text(json.dumps(shortlist, indent=1))
    return 0


def sheet(category: str, by_kit: dict[str, list[dict]], cell: int = 160) -> None:
    """Product photos, a row per kit, each captioned with its row:column."""
    out = Image.new("RGB", (cell * PER_KIT, cell * len(KITS)), "white")
    draw = ImageDraw.Draw(out)
    for r, kit in enumerate(KITS):
        for c, cand in enumerate(by_kit[kit]):
            if cand["image"]:
                cache = ABO / "thumbs" / cand["image"].replace("/", "_")
                if not cache.exists():
                    cache.parent.mkdir(exist_ok=True)
                    cache.write_bytes(
                        urllib.request.urlopen(f"{BASE}/images/small/{cand['image']}").read()
                    )
                im = Image.open(io.BytesIO(cache.read_bytes())).convert("RGB")
                im.thumbnail((cell - 8, cell - 22))
                out.paste(im, (c * cell + 4, r * cell + 4))
            draw.text((c * cell + 4, r * cell + cell - 16), f"{r}:{c} {kit[:6]}", fill="black")
    out.save(ABO / "sheets" / f"{category}.jpg", quality=80)


if __name__ == "__main__":
    sys.exit(main())
