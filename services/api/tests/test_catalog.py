"""The catalog's finishes for a flat's fittings: well formed, and served like any kind."""

from __future__ import annotations

import re
from pathlib import Path

from walkthrough_api import catalog as catalog_mod

CATALOG = Path(__file__).resolve().parents[3] / "catalog"
HEX = re.compile(r"^#[0-9a-f]{6}$")


def test_every_finish_names_a_fitting_and_what_each_surface_becomes() -> None:
    finishes = catalog_mod.load_catalog(CATALOG).finishes
    assert {f["fitting"] for f in finishes} == {"kitchen", "wardrobe"}
    assert len({f["id"] for f in finishes}) == len(finishes)
    for finish in finishes:
        assert finish["name"] and finish["swatches"], finish["id"]
        assert all(HEX.match(c) for c in finish["swatches"]), finish["id"]
        assert finish["surfaces"], finish["id"]
        for name, surface in finish["surfaces"].items():
            # The rebuilt flat's own material names (build_flat.surface_material).
            assert name.startswith("surface-"), (finish["id"], name)
            # A colour in place of the texture, or a tint over it: one, not both.
            assert len(surface) == 1 and set(surface) <= {"colour", "tint"}, (finish["id"], name)
            assert HEX.match(next(iter(surface.values()))), (finish["id"], name)


def test_finishes_are_summarised_with_their_fitting() -> None:
    finish = catalog_mod.load_catalog(CATALOG).finishes[0]
    summary = catalog_mod.summarise(finish, "finishes")
    assert summary["fitting"] == finish["fitting"]
    assert summary["swatches"] == finish["swatches"]
