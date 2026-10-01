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
    type: Literal["door", "window"]
    wallIndex: int
    offset: float
    width: float
    height: float


class Room(BaseModel):
    id: str
    name: str
    type: RoomType = "other"
    waypoint: Waypoint
    floorPolygon: list[Vec2] = Field(default_factory=list)
    walls: list[Wall] = Field(default_factory=list)
    openings: list[Opening] = Field(default_factory=list)

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


class PrivacyMask(BaseModel):
    id: str
    mode: Literal["blur", "delete"] = "blur"
    box: ItemBox


class SceneAssets(BaseModel):
    scene: str | None = None
    sceneNoFloor: str | None = None
    floor: str | None = None
    floorShading: dict[str, str] = Field(default_factory=dict)


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
