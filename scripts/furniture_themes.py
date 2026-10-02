"""
The furniture catalog, as data.

Each theme is a coherent set, and switching theme in the viewer re-furnishes a
room from a different one — which is only meaningful if each set hangs
together, so they are written here by hand rather than derived from tags.

Models are Poly Haven photogrammetry (CC0), **chosen by looking at them**. An
earlier pass picked by name and filled a "Modern" flat with a buttoned
chesterfield, a carved wing chair and a floral day bed. Poly Haven's furniture
library is almost entirely antique, rustic or industrial: across roughly 190
furniture assets there is no contemporary sofa and no contemporary bed at all.

So the two pieces every flat needs most are built in code instead — see
`PROCEDURAL` below and `buildProceduralItem` in `SplatScene.tsx`. They are
plain slab forms, which is what a modern sofa and platform bed actually are,
and they sit beside the photographed pieces without pretending to be photos.

`scripts/fetch_assets.py` downloads these, measures each mesh, and writes
`catalog/furniture/` and `catalog/styles/` from this table.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Item:
    """One catalog entry and the model behind it."""

    id: str
    name: str
    #: Poly Haven asset id, or None for a shape built in the viewer.
    source: str | None
    #: Drives the layout solver's wall/clearance rules.
    category: str
    #: Real-world height in metres, which the mesh is scaled to.
    #:
    #: Needed because the library is not consistently to scale: measured
    #: straight from the glTF, `steel_frame_shelves_01` comes out 21 m tall
    #: and `WoodenChair_01` 2.3 m. Fitting every piece to a stated height is
    #: the only way the flat ends up furnished at human size, and it keeps the
    #: footprint the layout solver validated honest too.
    height: float
    #: Procedural items state their own footprint, since nothing is measured.
    footprint: tuple[float, float] | None = None
    #: Which shape `buildProceduralItem` should draw, and in what colour.
    shape: str | None = None
    #: Body colour for a procedural piece.
    colour: str | None = None


@dataclass(frozen=True)
class Theme:
    id: str
    name: str
    description: str
    palette: tuple[str, ...]
    floor_ids: tuple[str, ...]
    items: tuple[Item, ...]
    #: Room type -> catalog ids to place, in priority order.
    picks: dict[str, tuple[str, ...]] = field(default_factory=dict)


def _sofa(theme: str, name: str, colour: str) -> Item:
    return Item(
        f"{theme}-sofa", name, None, "sofa", 0.78,
        footprint=(2.15, 0.92), shape="sofa", colour=colour,
    )


def _bed(theme: str, name: str, colour: str) -> Item:
    return Item(
        f"{theme}-bed", name, None, "bed", 0.95,
        footprint=(1.62, 2.08), shape="bed", colour=colour,
    )


THEMES: tuple[Theme, ...] = (
    Theme(
        id="contemporary",
        name="Contemporary",
        description=(
            "Low slab seating, pale oak and matt black. Nothing carved, "
            "nothing skirted, one plant and clear surfaces."
        ),
        palette=("#eae6df", "#9aa39b", "#2f332e", "#c2a98c"),
        floor_ids=("ash-pale-wide", "oak-plank-natural", "concrete-polished"),
        items=(
            _sofa("contemporary", "Slab three-seat sofa", "#8d9390"),
            _bed("contemporary", "Platform bed", "#9aa0a0"),
            Item("contemporary-armchair", "Leather armchair", "modern_arm_chair_01", "chair", 0.78),
            Item(
                "contemporary-coffee-table", "Coffee table",
                "modern_coffee_table_01", "coffee_table", 0.38,
            ),
            Item("contemporary-side-table", "Oak side table", "side_table_01", "sideboard", 0.52),
            Item(
                "contemporary-sideboard", "Slatted sideboard",
                "modern_wooden_cabinet", "sideboard", 0.52,
            ),
            Item(
                "contemporary-shelves", "Oak shelving",
                "wooden_display_shelves_01", "shelf", 1.75,
            ),
            Item(
                "contemporary-dining-table",
                "Dining table",
                "wooden_table_02",
                "dining_table",
                0.75,
            ),
            Item("contemporary-dining-chair", "Dining chair", "dining_chair_02", "chair", 0.88),
            Item("contemporary-desk", "Desk", "metal_office_desk", "desk", 0.75),
            Item("contemporary-plant", "Potted plant", "potted_plant_04", "plant", 0.55),
        ),
        picks={
            "living": (
                "contemporary-sofa", "contemporary-armchair", "contemporary-coffee-table",
                "contemporary-sideboard", "contemporary-plant",
            ),
            "bedroom": ("contemporary-bed", "contemporary-side-table", "contemporary-sideboard"),
            "dining": (
                "contemporary-dining-table", "contemporary-dining-chair",
                "contemporary-dining-chair",
            ),
            "office": ("contemporary-desk", "contemporary-dining-chair", "contemporary-shelves"),
            "hallway": ("contemporary-sideboard", "contemporary-plant"),
            "kitchen": ("contemporary-dining-table", "contemporary-dining-chair"),
        },
    ),
    Theme(
        id="mid-century",
        name="Mid-Century",
        description=(
            "Tan leather, turned wood and tapered legs. Warmer than the "
            "contemporary set, with rounder shapes."
        ),
        palette=("#e5dcc9", "#b8743f", "#7d8c7a", "#4a3a2a"),
        floor_ids=("oak-herringbone-light", "vintage-parquet-chevron", "oak-plank-natural"),
        items=(
            _sofa("mid-century", "Two-seat sofa", "#a8794f"),
            _bed("mid-century", "Low bed", "#b9a98f"),
            Item(
                "mid-century-lounge-chair", "Leather lounge chair",
                "mid_century_lounge_chair", "chair", 0.86,
            ),
            Item(
                "mid-century-coffee-table", "Round coffee table",
                "coffee_table_round_01", "coffee_table", 0.40,
            ),
            Item(
                "mid-century-lattice-table", "Lattice side table",
                "modern_coffee_table_02", "sideboard", 0.42,
            ),
            Item("mid-century-ottoman", "Leather ottoman", "Ottoman_01", "chair", 0.42),
            Item(
                "mid-century-sideboard", "Slatted sideboard",
                "modern_wooden_cabinet", "sideboard", 0.52,
            ),
            Item("mid-century-shelves", "Open shelving", "steel_frame_shelves_01", "shelf", 1.80),
            Item(
                "mid-century-dining-table",
                "Dining table",
                "wooden_table_02",
                "dining_table",
                0.75,
            ),
            Item("mid-century-dining-chair", "Dining chair", "dining_chair_02", "chair", 0.88),
            Item("mid-century-plant", "Potted plant", "potted_plant_02", "plant", 0.80),
        ),
        picks={
            "living": (
                "mid-century-sofa", "mid-century-lounge-chair", "mid-century-coffee-table",
                "mid-century-ottoman", "mid-century-plant",
            ),
            "bedroom": ("mid-century-bed", "mid-century-lattice-table", "mid-century-sideboard"),
            "dining": (
                "mid-century-dining-table", "mid-century-dining-chair", "mid-century-dining-chair",
            ),
            "office": (
                "mid-century-dining-table", "mid-century-dining-chair", "mid-century-shelves",
            ),
            "hallway": ("mid-century-sideboard", "mid-century-plant"),
            "kitchen": ("mid-century-dining-table", "mid-century-dining-chair"),
        },
    ),
    Theme(
        id="industrial",
        name="Industrial",
        description=(
            "Blackened steel, reclaimed timber and bare metal. "
            "Fewer, heavier pieces with space around them."
        ),
        palette=("#e8e4dd", "#6f6a62", "#26282a", "#9d7a52"),
        floor_ids=("concrete-polished", "slate-charcoal", "walnut-dark-plank"),
        items=(
            _sofa("industrial", "Slab sofa", "#5f6468"),
            _bed("industrial", "Platform bed", "#6d7174"),
            Item(
                "industrial-coffee-table", "Steel coffee table",
                "industrial_coffee_table", "coffee_table", 0.42,
            ),
            Item("industrial-armchair", "Leather armchair", "modern_arm_chair_01", "chair", 0.78),
            Item("industrial-ottoman", "Leather ottoman", "Ottoman_01", "chair", 0.42),
            Item("industrial-shelves", "Steel shelving", "steel_frame_shelves_02", "shelf", 1.85),
            Item(
                "industrial-desk",
                "Steel desk",
                "metal_office_desk",
                "desk",
                0.75,
            ),
            Item("industrial-stool", "Metal stool", "metal_stool_02", "chair", 0.62),
            Item(
                "industrial-dining-table",
                "Workbench table",
                "wooden_table_02",
                "dining_table",
                0.75,
            ),
            Item("industrial-plant", "Potted tree", "potted_plant_01", "plant", 1.20),
        ),
        picks={
            "living": (
                "industrial-sofa", "industrial-armchair", "industrial-coffee-table",
                "industrial-shelves", "industrial-plant",
            ),
            "bedroom": ("industrial-bed", "industrial-stool", "industrial-shelves"),
            "dining": ("industrial-dining-table", "industrial-stool", "industrial-stool"),
            "office": ("industrial-desk", "industrial-stool", "industrial-shelves"),
            "hallway": ("industrial-shelves", "industrial-plant"),
            "kitchen": ("industrial-dining-table", "industrial-stool"),
        },
    ),
    Theme(
        id="period",
        name="Period",
        description=(
            "Carved oak, buttoned leather and turned legs — for a flat being "
            "sold as a period property. Deliberately old."
        ),
        palette=("#ece4d6", "#6b4f33", "#2f2318", "#8a6b45"),
        floor_ids=("walnut-dark-plank", "vintage-parquet-chevron", "marble-carrara"),
        items=(
            Item("period-sofa", "Buttoned settee", "Sofa_01", "sofa", 0.85),
            Item("period-armchair", "Wing armchair", "ArmChair_01", "chair", 1.05),
            Item("period-bed", "Carved bed", "GothicBed_01", "bed", 1.50),
            Item(
                "period-coffee-table", "Carved coffee table",
                "gothic_coffee_table", "coffee_table", 0.45,
            ),
            Item("period-console", "Hall console", "ClassicConsole_01", "sideboard", 0.80),
            Item("period-nightstand", "Nightstand", "ClassicNightstand_01", "sideboard", 0.62),
            Item("period-cabinet", "Glazed cabinet", "vintage_cabinet_01", "wardrobe", 1.85),
            Item(
                "period-dining-table",
                "Pedestal table",
                "round_wooden_table_01",
                "dining_table",
                0.75,
            ),
            Item("period-rocking-chair", "Rocking chair", "Rockingchair_01", "chair", 1.05),
            Item("period-plant", "Potted plant", "potted_plant_02", "plant", 0.80),
        ),
        picks={
            "living": (
                "period-sofa", "period-armchair", "period-coffee-table",
                "period-cabinet", "period-plant",
            ),
            "bedroom": ("period-bed", "period-nightstand", "period-cabinet"),
            "dining": ("period-dining-table", "period-armchair", "period-armchair"),
            "office": ("period-dining-table", "period-rocking-chair", "period-cabinet"),
            "hallway": ("period-console", "period-plant"),
            "kitchen": ("period-dining-table", "period-rocking-chair"),
        },
    ),
)


def all_items() -> list[tuple[Theme, Item]]:
    return [(theme, item) for theme in THEMES for item in theme.items]
