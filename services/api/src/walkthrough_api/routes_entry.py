"""
The entry view: where a tour opens, set from the viewer by an editor.

The editor stands where the flat looks its best and presses "Set current
view as entry view"; the viewer sends the camera and a still of what it
shows. The still is what a visitor sees first, while the flat loads.
"""

from __future__ import annotations

import base64
import binascii

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from walkthrough_pipeline.manifest import HeroView, Vec3

from . import auth, projects
from .config import Settings, get_settings
from .deps import load_manifest_or_404

router = APIRouter(prefix="/api", tags=["entry"])

#: The still, beside the manifest in scene/ (where the viewer reads files).
HERO_IMAGE = "hero.webp"
#: Largest still accepted: the viewer sends under 200 KB.
MAX_IMAGE_BYTES = 400_000
WEBP_MAGIC = (b"RIFF", b"WEBP")


class EntryBody(BaseModel):
    position: Vec3
    yaw: float
    pitch: float
    fov: float
    #: The still as base64 WebP.
    image: str


@router.put("/projects/{project_id}/entry", dependencies=[Depends(auth.require_editor)])
def set_entry(
    project_id: str, body: EntryBody, settings: Settings = Depends(get_settings)
) -> dict:
    manifest = load_manifest_or_404(settings, project_id)
    try:
        image = base64.b64decode(body.image, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=400, detail="The still is not base64") from exc
    if len(image) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="The still is too large")
    if image[:4] != WEBP_MAGIC[0] or image[8:12] != WEBP_MAGIC[1]:
        raise HTTPException(status_code=400, detail="The still must be a WebP image")
    (projects.project_dir(settings, project_id) / "scene" / HERO_IMAGE).write_bytes(image)
    manifest.heroView = HeroView(
        position=body.position, yaw=body.yaw, pitch=body.pitch, fov=body.fov, image=HERO_IMAGE
    )
    projects.save_manifest(settings, project_id, manifest)
    return {"saved": True, "image": HERO_IMAGE, "bytes": len(image)}
