"""
Download the floor textures: ambientCG 1K JPG materials (colour, normal,
roughness), CC0. Nothing here is committed — everything lands under
`data/assets/`, which is git-ignored, and the catalog JSON records only the
relative URL the API serves it from.

Furniture is not fetched here: each catalog piece names its source model, and
`pnpm prepare:models` (scripts/prepare-models.mjs) downloads, checks and
converts it.

Run:  pnpm seed:assets
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import urllib.request
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CATALOG = REPO / "catalog"
ASSETS = REPO / "data" / "assets"

UA = {"User-Agent": "walkthrough-asset-fetcher/0.1"}

# ---------------------------------------------------------------------------
# Floors — ambientCG
# ---------------------------------------------------------------------------

#: catalog floor id -> ambientCG asset id.
#:
#: Picked by measuring each candidate's preview, not by its ambientCG name.
#: An earlier set chosen on names alone left "ash-pale-wide" nearly black and
#: "marble-carrara" dark grey — a floor whose name promises a colour it does
#: not have is worse than no floor.
#:
#: Eleven of these already named a source in the catalog. The five that were
#: generated procedurally by seed_floors.py are mapped onto the closest real
#: scan here, because a photographed material reads as a floor and a generated
#: pattern reads as a texture swatch.
FLOOR_SOURCES: dict[str, str] = {
    "ash-pale-wide": "WoodFloor039",
    "concrete-polished": "Concrete034",
    "limestone-sand": "Rock030",
    "marble-carrara": "Marble012",
    "oak-herringbone-light": "WoodFloor041",
    "oak-plank-natural": "WoodFloor040",
    "terracotta-rustic": "Tiles074",
    "terrazzo-grey": "Terrazzo009",
    "tile-square-white": "Tiles101",
    "walnut-dark-plank": "WoodFloor048",
    # Previously procedural or placeholder:
    "painted-board-sage": "Planks010",
    "painted-board-white": "Planks010",
    "stencil-moroccan-blue": "Tiles103",
    "stencil-victorian-geometric": "Tiles100",
    "vintage-parquet-chevron": "WoodFloor037",
    "slate-charcoal": "Tiles093",
}

#: Floors whose catalog colour is a deliberate tint over a neutral scan — the
#: painted and stencilled ones. Everywhere else the scan's own colour is the
#: truth and tinting it would be a lie about the material.
TINTED: frozenset[str] = frozenset(
    {
        "painted-board-sage",
        "painted-board-white",
        "stencil-moroccan-blue",
        "stencil-victorian-geometric",
    }
)

#: Which file in the ambientCG zip becomes which map. NormalGL (not NormalDX)
#: is the OpenGL convention three.js expects; picking DX inverts the green
#: channel and lights every bump from the wrong side.
MAP_FILES = {
    "color": "_Color.jpg",
    "normal": "_NormalGL.jpg",
    "roughness": "_Roughness.jpg",
}


def fetch(url: str, timeout: int = 180) -> bytes:
    request = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def fetch_floors() -> int:
    out_root = ASSETS / "floors"
    out_root.mkdir(parents=True, exist_ok=True)
    written = 0

    for floor_id, acg_id in sorted(FLOOR_SOURCES.items()):
        catalog_file = CATALOG / "floors" / f"{floor_id}.json"
        if not catalog_file.is_file():
            print(f"  ! {floor_id}: no catalog entry, skipped")
            continue

        out_dir = out_root / floor_id
        out_dir.mkdir(parents=True, exist_ok=True)
        expected = {name: out_dir / f"{name}.jpg" for name in MAP_FILES}

        if all(path.is_file() for path in expected.values()):
            print(f"  = {floor_id} ({acg_id}) already present")
        else:
            url = f"https://ambientcg.com/get?file={acg_id}_1K-JPG.zip"
            try:
                blob = fetch(url)
            except Exception as exc:  # noqa: BLE001 - report and continue
                print(f"  ! {floor_id} ({acg_id}): download failed: {exc}")
                continue

            try:
                archive = zipfile.ZipFile(io.BytesIO(blob))
            except zipfile.BadZipFile:
                print(f"  ! {floor_id} ({acg_id}): not a zip ({len(blob)} bytes)")
                continue

            names = archive.namelist()
            for name, suffix in MAP_FILES.items():
                match = next((n for n in names if n.endswith(suffix)), None)
                if match is None:
                    print(f"  ! {floor_id}: {acg_id} has no {suffix}")
                    continue
                expected[name].write_bytes(archive.read(match))
            print(f"  + {floor_id} ({acg_id})")
            written += 1

        entry = json.loads(catalog_file.read_text(encoding="utf-8"))
        entry["maps"] = {
            "albedo": f"/files/assets/floors/{floor_id}/color.jpg",
            "normal": f"/files/assets/floors/{floor_id}/normal.jpg",
            "roughness": f"/files/assets/floors/{floor_id}/roughness.jpg",
        }
        entry["tintAlbedo"] = floor_id in TINTED
        entry["license"] = "CC0-1.0"
        entry["source"] = f"https://ambientcg.com/view?id={acg_id}"
        entry["assetStatus"] = "ready"
        entry.pop("note", None)
        catalog_file.write_text(json.dumps(entry, indent=2) + "\n", encoding="utf-8")

    return written


def main() -> int:
    argparse.ArgumentParser(description=__doc__).parse_args()
    print("floors (ambientCG, CC0):")
    fetch_floors()
    print(f"\nassets under {ASSETS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
