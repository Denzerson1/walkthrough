"""
Automatic layout endpoint (M7).

Wraps the deterministic solver in walkthrough_pipeline.layout so the viewer
and the assistant both get real placements instead of dropping everything at
the room centre.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from walkthrough_pipeline.layout import Item, Room, Wall, solve_layout

from .catalog import Catalog
from .config import Settings, get_settings
from .deps import catalog_dep, load_manifest_or_404

#: Footprint for a catalog item with no dimensions. Must match
#: DEFAULT_ITEM_SIZE in apps/web/src/components/SplatScene.tsx, or the
#: solver would validate one footprint and the viewer draw another.
DEFAULT_ITEM_SIZE = [0.6, 0.6, 0.6]

router = APIRouter(prefix="/api", tags=["layout"])


class LayoutBody(BaseModel):
    project_id: str
    room_id: str
    style_id: str
    #: Optional explicit item list. Defaults to the style's picks for the room type.
    item_ids: list[str] | None = None


class LayoutReply(BaseModel):
    placements: list[dict[str, Any]]
    skipped: list[str]
    complete: bool
    style_id: str
    floor_id: str | None


@router.post("/layout", response_model=LayoutReply)
def auto_layout(
    body: LayoutBody,
    settings: Settings = Depends(get_settings),
    catalog: Catalog = Depends(catalog_dep),
) -> LayoutReply:
    manifest = load_manifest_or_404(settings, body.project_id)

    room = manifest.room(body.room_id)
    if room is None:
        raise HTTPException(status_code=404, detail=f"No room {body.room_id!r}")
    if len(room.floorPolygon) < 3:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Room {body.room_id!r} has no floor polygon, so furniture cannot be "
                "placed. Draw one in the editor or re-run the pipeline."
            ),
        )

    style = catalog.by_id("styles", body.style_id)
    if style is None:
        raise HTTPException(status_code=404, detail=f"No style {body.style_id!r}")

    picks = body.item_ids
    if picks is None:
        picks = list((style.get("picks") or {}).get(room.type, []))
    if not picks:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Style {body.style_id!r} has no picks for a {room.type} room. "
                "Choose another style or add items by hand."
            ),
        )

    items: list[Item] = []
    missing: list[str] = []
    for item_id in picks:
        entry = catalog.by_id("furniture", item_id)
        if entry is None:
            missing.append(item_id)
            continue
        dims = entry.get("dimensionsM") or DEFAULT_ITEM_SIZE
        items.append(
            Item(
                id=item_id,
                category=str(entry.get("category", "other")),
                # Footprint on the floor is width x depth; dims is [w, h, d].
                size=(float(dims[0]), float(dims[2])),
            )
        )
    if missing:
        raise HTTPException(
            status_code=409,
            detail=f"Style references furniture not in the catalog: {', '.join(missing)}",
        )

    # Which walls carry doors and windows, so the solver can honour the rules.
    walls: list[Wall] = []
    for index, wall in enumerate(room.walls):
        openings = [
            (float(o.offset), float(o.width))
            for o in room.openings
            if o.wallIndex == index
        ]
        walls.append(
            Wall(
                start=(float(wall.start[0]), float(wall.start[1])),
                end=(float(wall.end[0]), float(wall.end[1])),
                openings=openings,
                has_door=any(o.type == "door" for o in room.openings if o.wallIndex == index),
                has_window=any(
                    o.type == "window" for o in room.openings if o.wallIndex == index
                ),
            )
        )

    solver_room = Room(
        id=room.id,
        type=room.type,
        polygon=[(float(p[0]), float(p[1])) for p in room.floorPolygon],
        walls=walls,
    )
    result = solve_layout(solver_room, items)

    floor_ids = style.get("floorIds") or []
    return LayoutReply(
        placements=result.as_dicts(),
        skipped=result.skipped,
        complete=result.complete,
        style_id=body.style_id,
        floor_id=floor_ids[0] if floor_ids else None,
    )
