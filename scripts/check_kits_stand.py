"""
Every style kit in every room of a manifest: each slot's piece placed as the
viewer places it (back to the slot's back edge), then any piece that does not
fit and any two that stand in each other. Chairs may tuck under their table by
TUCK; rugs lie under everything; a lamp stands on its table.

    uv run python scripts/check_kits_stand.py data/projects/flat-02/scene/manifest.json

Exits 1 if anything is found.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
#: How far a chair may stand under its table, metres.
TUCK = 0.12
#: Two boxes sharing less than this are touching, not overlapping.
TOUCH = 0.005
TUCKED = ({"dining_chair", "dining_table"}, {"desk_chair", "desk"})
FLAT = ("rug", "table_lamp")


def load(folder: str) -> list[dict]:
    files = sorted((ROOT / "catalog" / folder).glob("*.json"))
    return [json.loads(f.read_text(encoding="utf-8")) for f in files]


def box(slot: dict, item: dict) -> tuple[float, float, float, float] | None:
    """The piece's floor box (x0, x1, z0, z1) in its slot, or None if it does not fit."""
    w, _, d = item["dimensionsM"]
    max_w, max_d = slot["maxFootprint"]
    if w > max_w + 1e-6 or d > max_d + 1e-6:
        return None
    yaw = math.radians(slot["yaw"])
    fx, fz = -math.sin(yaw), math.cos(yaw)
    out = -max_d / 2 + d / 2
    cx, cz = slot["position"][0] + fx * out, slot["position"][2] + fz * out
    hx, hz = (abs(fz) * w + abs(fx) * d) / 2, (abs(fx) * w + abs(fz) * d) / 2
    return cx - hx, cx + hx, cz - hz, cz + hz


def problems(manifest: dict) -> list[str]:
    items = {i["id"]: i for i in load("furniture")}
    found = []
    for kit in load("styles"):
        for room in manifest["rooms"]:
            stood = []
            for slot in room.get("slots") or []:
                if slot["category"] in FLAT:
                    continue
                where = f"{kit['id']} {room['id']} {slot['id']}"
                piece = (kit["slots"].get(room["type"]) or {}).get(slot["category"])
                if not piece:
                    found.append(f"{where}: no piece")
                    continue
                placed = box(slot, items[piece])
                if placed is None:
                    found.append(f"{where}: {piece} does not fit")
                    continue
                stood.append((slot, placed))
            for i, (a, pa) in enumerate(stood):
                for b, pb in stood[i + 1 :]:
                    over_x = min(pa[1], pb[1]) - max(pa[0], pb[0])
                    over_z = min(pa[3], pb[3]) - max(pa[2], pb[2])
                    allow = TUCK if {a["category"], b["category"]} in TUCKED else 0.0
                    if min(over_x, over_z) > allow + TOUCH:
                        found.append(f"{kit['id']} {room['id']}: {a['id']} and {b['id']} "
                                     f"overlap {over_x:.2f} x {over_z:.2f} m")
    return found


if __name__ == "__main__":
    out = problems(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")))
    print("\n".join(out) or "Every kit stands in every room.")
    sys.exit(1 if out else 0)
