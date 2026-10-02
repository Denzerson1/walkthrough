"""
Download the real catalog assets: floor textures and furniture meshes.

Both sources are CC0. Nothing here is committed — everything lands under
`data/assets/`, which is git-ignored, and the catalog JSON records only the
relative URL the API serves it from.

  floors     ambientCG 1K JPG materials (colour, normal, roughness)
  furniture  Poly Haven photogrammetry models, themed by
             `scripts/furniture_themes.py`, which also writes the catalog

Run:  pnpm seed:assets            (both)
      uv run python scripts/fetch_assets.py --floors
      uv run python scripts/fetch_assets.py --furniture
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

import numpy as np
from furniture_themes import THEMES, all_items

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


# ---------------------------------------------------------------------------
# Furniture — Poly Haven
# ---------------------------------------------------------------------------

PH_API = "https://api.polyhaven.com"
#: Texture resolution to pull. 1k keeps a themed set to roughly 100 MB and is
#: more than the viewer resolves at furniture distance; 2k quadruples the
#: download for detail you only see with your nose against the arm of a chair.
PH_RES = "1k"


def _node_matrix(node: dict[str, Any]) -> np.ndarray:
    """A glTF node's local transform, whether it is given as TRS or a matrix."""
    if "matrix" in node:
        # glTF stores matrices column-major.
        return np.array(node["matrix"], dtype=float).reshape(4, 4).T

    matrix = np.eye(4)
    if "scale" in node:
        matrix = np.diag([*node["scale"], 1.0]) @ matrix
    if "rotation" in node:
        x, y, z, w = node["rotation"]
        rot = np.array(
            [
                [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w), 0],
                [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w), 0],
                [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y), 0],
                [0, 0, 0, 1],
            ]
        )
        matrix = rot @ matrix
    if "translation" in node:
        trs = np.eye(4)
        trs[:3, 3] = node["translation"]
        matrix = trs @ matrix
    return matrix


def measure_gltf(doc: dict[str, Any]) -> tuple[float, float, float]:
    """
    The model's bounding size in metres.

    Read from the accessors' own min/max and the node transforms, so the
    catalog's dimensionsM is the mesh's real size rather than a guess. The
    layout solver and the viewer's collision box both key off this, so a
    guessed number means furniture that overlaps or floats.
    """
    accessors = doc.get("accessors", [])
    meshes = doc.get("meshes", [])
    nodes = doc.get("nodes", [])

    lo = np.full(3, np.inf)
    hi = np.full(3, -np.inf)

    def visit(index: int, parent: np.ndarray) -> None:
        nonlocal lo, hi
        node = nodes[index]
        world = parent @ _node_matrix(node)
        if "mesh" in node:
            for primitive in meshes[node["mesh"]].get("primitives", []):
                position = primitive.get("attributes", {}).get("POSITION")
                if position is None:
                    continue
                accessor = accessors[position]
                if "min" not in accessor or "max" not in accessor:
                    continue
                amin = np.array(accessor["min"], dtype=float)
                amax = np.array(accessor["max"], dtype=float)
                # Transform all eight corners: the extent of a rotated box is
                # not the rotation of its extent.
                for bits in range(8):
                    corner = np.array(
                        [amax[i] if bits >> i & 1 else amin[i] for i in range(3)] + [1.0]
                    )
                    point = (world @ corner)[:3]
                    lo = np.minimum(lo, point)
                    hi = np.maximum(hi, point)
        for child in node.get("children", []):
            visit(child, world)

    scene = doc.get("scenes", [{}])[doc.get("scene", 0)]
    for root in scene.get("nodes", []):
        visit(root, np.eye(4))

    if not np.isfinite(lo).all():
        raise ValueError("no positioned geometry in glTF")
    size = hi - lo
    return (round(float(size[0]), 3), round(float(size[1]), 3), round(float(size[2]), 3))


def _download_model(source: str, out_dir: Path) -> Path:
    """
    Fetch one Poly Haven model as glTF plus its .bin and textures.

    Kept as glTF with side files rather than repacked into a .glb: three.js
    loads either, and leaving them separate means the textures stay ordinary
    cacheable image requests.
    """
    files = json.loads(fetch(f"{PH_API}/files/{source}").decode("utf-8"))
    entry = files["gltf"][PH_RES]["gltf"]

    out_dir.mkdir(parents=True, exist_ok=True)
    main = out_dir / Path(entry["url"]).name
    if not main.is_file() or main.stat().st_size != entry.get("size"):
        main.write_bytes(fetch(entry["url"]))

    for relative, info in entry.get("include", {}).items():
        target = out_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        # Side files are large and never change, so an interrupted run resumes
        # rather than paying for the whole set again.
        if target.is_file() and target.stat().st_size == info.get("size"):
            continue
        target.write_bytes(fetch(info["url"]))

    return main


def fetch_furniture() -> int:
    out_root = ASSETS / "furniture"
    out_root.mkdir(parents=True, exist_ok=True)
    furniture_dir = CATALOG / "furniture"
    styles_dir = CATALOG / "styles"
    furniture_dir.mkdir(parents=True, exist_ok=True)
    styles_dir.mkdir(parents=True, exist_ok=True)

    # The catalog is written from the theme table, so entries for models no
    # longer in it have to go. Leaving them behind means the assistant and old
    # share links can still name an item that nothing will draw.
    for stale in furniture_dir.glob("*.json"):
        stale.unlink()
    for stale in styles_dir.glob("*.json"):
        stale.unlink()

    written = 0
    for theme, item in all_items():
        if item.source is None:
            # Built in the viewer, so there is nothing to download or measure:
            # the shape states its own footprint.
            width, depth = item.footprint or (1.0, 1.0)
            (furniture_dir / f"{item.id}.json").write_text(
                json.dumps(
                    {
                        "id": item.id,
                        "name": item.name,
                        "category": item.category,
                        "styles": [theme.id],
                        "colors": [item.colour or theme.palette[1]],
                        "dimensionsM": [width, item.height, depth],
                        "glb": None,
                        "shape": item.shape,
                        "thumbnail": None,
                        "assetStatus": "ready",
                        "license": "CC0-1.0",
                        "source": "Built in the viewer (buildProceduralItem)",
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            print(f"  + {item.id:28} {width:.2f} x {item.height:.2f} x {depth:.2f} m   (drawn)")
            written += 1
            continue

        out_dir = out_root / item.id
        try:
            main = _download_model(item.source, out_dir)
            doc = json.loads(main.read_text(encoding="utf-8"))
            measured = measure_gltf(doc)
        except Exception as exc:  # noqa: BLE001 - report and keep going
            print(f"  ! {item.id} ({item.source}): {exc}")
            continue

        if measured[1] <= 0:
            print(f"  ! {item.id}: mesh has no height")
            continue
        # Scale the measured box to the stated real-world height. The viewer
        # fits each mesh uniformly into dimensionsM, so writing the scaled box
        # here is what actually resizes the model on screen.
        factor = item.height / measured[1]
        dimensions = tuple(round(v * factor, 3) for v in measured)

        (furniture_dir / f"{item.id}.json").write_text(
            json.dumps(
                {
                    "id": item.id,
                    "name": item.name,
                    "category": item.category,
                    "styles": [theme.id],
                    "colors": list(theme.palette[:2]),
                    "dimensionsM": list(dimensions),
                    "glb": f"/files/assets/furniture/{item.id}/{main.name}",
                    "thumbnail": None,
                    "assetStatus": "ready",
                    "license": "CC0-1.0",
                    "source": f"https://polyhaven.com/a/{item.source}",
                    "measuredM": list(measured),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        note = "" if 0.5 <= factor <= 2.0 else f"   (mesh was {measured[1]:.1f} m tall)"
        print(
            f"  + {item.id:28} "
            f"{dimensions[0]:.2f} x {dimensions[1]:.2f} x {dimensions[2]:.2f} m{note}"
        )
        written += 1

    for theme in THEMES:
        (styles_dir / f"{theme.id}.json").write_text(
            json.dumps(
                {
                    "id": theme.id,
                    "name": theme.name,
                    "description": theme.description,
                    "palette": list(theme.palette),
                    "floorIds": list(theme.floor_ids),
                    "picks": {room: list(ids) for room, ids in theme.picks.items()},
                    "complete": True,
                    "license": "CC0-1.0",
                    "source": "https://polyhaven.com",
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    print(f"  {written} models across {len(THEMES)} themes")
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--floors", action="store_true")
    parser.add_argument("--furniture", action="store_true")
    args = parser.parse_args()

    # No flags means both.
    do_floors = args.floors or not args.furniture
    do_furniture = args.furniture or not args.floors

    if do_floors:
        print("floors (ambientCG, CC0):")
        fetch_floors()
    if do_furniture:
        print("furniture (Poly Haven, CC0):")
        fetch_furniture()

    print(f"\nassets under {ASSETS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
