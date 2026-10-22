"""
scene/manifest.json schema (docs/BRIEF.md §4, docs/SPEC.md §4).

The pipeline writes manifests, the API serves them and the editor updates
them, so the schema lives here and both other layers import it. Keep in
sync with apps/web/src/lib/manifest.ts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

Vec2 = tuple[float, float]
Vec3 = tuple[float, float, float]
Mat4 = list[list[float]]

IDENTITY_TRANSFORM: Mat4 = [
    [1.0, 0.0, 0.0, 0.0],
    [0.0, 1.0, 0.0, 0.0],
    [0.0, 0.0, 1.0, 0.0],
    [0.0, 0.0, 0.0, 1.0],
]

#: Window sill height when a scan does not say: a common European default.
DEFAULT_SILL_M = 0.9

RoomType = Literal[
    "living", "bedroom", "kitchen", "dining", "bathroom", "hallway", "office", "other"
]


class Waypoint(BaseModel):
    position: Vec3
    yaw: float = 0.0


class Wall(BaseModel):
    start: Vec2
    end: Vec2
    height: float = 2.6


class Opening(BaseModel):
    """An opening in a wall. `offset` is the distance from the wall's start to
    the opening's CENTRE, so it spans offset +/- width/2."""

    type: Literal["door", "window"]
    wallIndex: int
    offset: float
    width: float
    height: float
    #: Height of the lower edge above the floor. None when unknown: a door
    #: then starts at the floor and a window at DEFAULT_SILL_M.
    sill: float | None = None

    @property
    def bottom(self) -> float:
        if self.sill is not None:
            return self.sill
        return 0.0 if self.type == "door" else DEFAULT_SILL_M


class Obstacle(BaseModel):
    """A piece standing on the floor, as its outline in plan; the walk keeps out of it."""

    id: str
    footprint: list[Vec2]


class Room(BaseModel):
    id: str
    name: str
    type: RoomType = "other"
    waypoint: Waypoint
    floorPolygon: list[Vec2] = Field(default_factory=list)
    walls: list[Wall] = Field(default_factory=list)
    openings: list[Opening] = Field(default_factory=list)
    #: Average colour of this room's original floor splats, as #rrggbb.
    #: Used to draw the room once floor splats are hidden for a replacement
    #: elsewhere in the flat. See docs/SPEC.md §4.
    originalFloorColor: str | None = None
    #: The ceiling as scanned, piece by piece: 3D outlines, flat or sloped.
    #: Where a slope comes down, a person cannot stand upright (canStand).
    ceilings: list[list[Vec3]] = Field(default_factory=list)
    #: The rebuilt flat's pieces standing on the floor (canStand keeps out).
    obstacles: list[Obstacle] = Field(default_factory=list)

    @field_validator("floorPolygon")
    @classmethod
    def polygon_is_empty_or_a_triangle_at_least(cls, v: list[Vec2]) -> list[Vec2]:
        if v and len(v) < 3:
            raise ValueError("floorPolygon needs at least 3 points when present")
        return v


class ItemBox(BaseModel):
    center: Vec3
    size: Vec3
    yaw: float = 0.0


class ManifestItem(BaseModel):
    id: str
    label: str
    roomId: str
    box: ItemBox
    source: Literal["roomplan", "editor"] = "editor"
    #: The piece's own 3D model (.glb under scene/models/, made from a photo
    #: of it), fitted into `box`. None: a catalog piece of the same label
    #: stands in.
    model: str | None = None


class PrivacyMask(BaseModel):
    id: str
    mode: Literal["blur", "delete"] = "blur"
    box: ItemBox


class Lighting(BaseModel):
    """The rebuilt flat's light at one time of day (rebuild bakes several)."""

    time: str  # "17:30"
    lightmap: str
    lightmapScale: float
    exposure: float = 1.0
    #: Multiplies the sky dome's colours: warm and dim towards dusk.
    skyTint: list[float] | None = None
    #: The vertex colour set holding the neighbourhood's light at this time.
    colours: str | None = None
    #: The sky at this time, an equirectangular panorama (sky.py).
    sky: str | None = None


class SunSample(BaseModel):
    """The sun at one time of day, as the viewer draws its direct light (rebuild)."""

    minutes: int  # after midnight
    #: Towards the sun, in scene space.
    direction: tuple[float, float, float]
    #: Its direct light on a surface facing it, in the light maps' units.
    light: tuple[float, float, float]


class ViewPhoto(BaseModel):
    """The view out of the windows, from the owner's photos of it (view_photo.py)."""

    model: str  # a .glb: the photo on the walls it shows, shown unlit
    time: str  # "15:38", when it was taken: the light it shows


class SceneAssets(BaseModel):
    scene: str | None = None
    sceneNoFloor: str | None = None
    floor: str | None = None
    floorShading: dict[str, str] = Field(default_factory=dict)
    #: The rebuilt flat: a glTF model with baked lighting (`pipeline rebuild`).
    #: When present the viewer shows it instead of the splat.
    model: str | None = None
    #: Its baked light: an 8-bit atlas on the model's second UV set, holding
    #: light / lightmapScale (see build_flat.save_lightmap).
    lightmap: str | None = None
    lightmapScale: float = 1.0
    #: The same at other times of day, for the viewer's daylight slider.
    lighting: list[Lighting] = Field(default_factory=list)
    #: The sun along the slider's span, every few minutes: the light maps
    #: hold all but its direct light, which the viewer draws, so it moves.
    #: Empty: the sun is in the light maps.
    sunPath: list[SunSample] = Field(default_factory=list)
    viewPhoto: ViewPhoto | None = None


class Manifest(BaseModel):
    id: str
    name: str
    units: Literal["m"] = "m"
    upAxis: Literal["y"] = "y"
    transform: Mat4 = Field(default_factory=lambda: [row[:] for row in IDENTITY_TRANSFORM])
    floor: dict[str, list[float]] = Field(default_factory=lambda: {"plane": [0.0, 1.0, 0.0, 0.0]})
    rooms: list[Room] = Field(default_factory=list)
    items: list[ManifestItem] = Field(default_factory=list)
    privacyMasks: list[PrivacyMask] = Field(default_factory=list)
    assets: SceneAssets = Field(default_factory=SceneAssets)

    @field_validator("transform")
    @classmethod
    def transform_is_4x4(cls, v: Mat4) -> Mat4:
        if len(v) != 4 or any(len(row) != 4 for row in v):
            raise ValueError("transform must be a 4x4 matrix")
        return v

    @field_validator("rooms")
    @classmethod
    def room_ids_are_unique(cls, v: list[Room]) -> list[Room]:
        ids = [r.id for r in v]
        if len(ids) != len(set(ids)):
            raise ValueError("room ids must be unique")
        return v

    def room(self, room_id: str) -> Room | None:
        return next((r for r in self.rooms if r.id == room_id), None)

    def to_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), indent=2) + "\n"

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json(), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> Manifest:
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))
