"""Floor-photo upload and material generation (M4)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlmodel import Session, select

from . import textures
from .config import Settings, get_settings
from .db import UploadRecord
from .deps import db_dep, valid_project_id
from .storage import build_storage, upload_key

router = APIRouter(prefix="/api/uploads", tags=["uploads"])

ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}


class ProposeReply(BaseModel):
    upload_id: str
    width: int
    height: int
    corners: list[list[float]]
    preview_url: str


@router.post("/floor", response_model=ProposeReply)
async def upload_floor_photo(
    project_id: str = Form(...),
    file: UploadFile = File(...),
    settings: Settings = Depends(get_settings),
    session: Session = Depends(db_dep),
) -> ProposeReply:
    """Step 1-2: accept a photo and propose the tile region."""
    valid_project_id(project_id)
    if file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported type {file.content_type!r}. Use JPEG, PNG or WebP.",
        )

    # Read in chunks and stop at the limit, rather than buffering a 1 GB body
    # into memory before rejecting it.
    limit = settings.max_upload_bytes
    chunks: list[bytes] = []
    total = 0
    while chunk := await file.read(256 * 1024):
        total += len(chunk)
        if total > limit:
            raise HTTPException(
                status_code=413,
                detail=f"Image is larger than {limit // 1024 // 1024} MB.",
            )
        chunks.append(chunk)
    data = b"".join(chunks)
    try:
        image = textures.strip_exif_and_decode(data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    upload_id = uuid.uuid4().hex[:16]
    storage = build_storage(settings)
    # Re-encoded as PNG, which drops EXIF including any GPS tags.
    original_key = upload_key(project_id, upload_id, "original.png")
    storage.write_bytes(original_key, textures.encode_png(image))

    session.add(
        UploadRecord(upload_id=upload_id, project_id=project_id, original_key=original_key)
    )
    session.commit()

    h, w = image.shape[:2]
    corners = textures.propose_quad(image)
    return ProposeReply(
        upload_id=upload_id,
        width=w,
        height=h,
        corners=[[x, y] for x, y in corners],
        preview_url=storage.public_url(original_key),
    )


class RectifyBody(BaseModel):
    upload_id: str
    project_id: str
    corners: list[list[float]]
    tile_size_cm: float


class RectifyReply(BaseModel):
    upload_id: str
    albedo_url: str
    normal_url: str
    roughness_url: str
    tile_size_m: list[float]
    seam_error: float
    warning: str | None = None


@router.post("/floor/rectify", response_model=RectifyReply)
def rectify_floor_photo(
    body: RectifyBody,
    settings: Settings = Depends(get_settings),
    session: Session = Depends(db_dep),
) -> RectifyReply:
    """Steps 3-6: rectify, make seamless, derive normal and roughness."""
    valid_project_id(body.project_id)
    if len(body.corners) != 4:
        raise HTTPException(status_code=400, detail="Exactly 4 corners are required.")
    if not 1.0 <= body.tile_size_cm <= 500.0:
        raise HTTPException(
            status_code=400, detail="Tile size must be between 1 and 500 cm."
        )

    storage = build_storage(settings)
    key = upload_key(body.project_id, body.upload_id, "original.png")
    try:
        raw = storage.read_bytes(key)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Upload not found.") from exc

    image = textures.strip_exif_and_decode(raw)
    corners = [(float(c[0]), float(c[1])) for c in body.corners]
    tile = textures.rectify(image, corners)
    # Judge the photo on the rectified tile: after blending, the measurement
    # is meaningless (see tiling_seam_error).
    seam = textures.tiling_seam_error(tile)
    seamless = textures.make_seamless(tile)
    normal = textures.normal_from_luminance(seamless)
    roughness = textures.default_roughness(seamless)

    albedo_key = upload_key(body.project_id, body.upload_id, "albedo.png")
    normal_key = upload_key(body.project_id, body.upload_id, "normal.png")
    rough_key = upload_key(body.project_id, body.upload_id, "roughness.png")
    storage.write_bytes(albedo_key, textures.encode_png(seamless))
    storage.write_bytes(normal_key, textures.encode_png(normal))
    storage.write_bytes(rough_key, textures.encode_png(roughness))

    # upload_id is a unique column, not the primary key, so the select is the
    # only lookup that can match.
    record = session.exec(
        select(UploadRecord).where(UploadRecord.upload_id == body.upload_id)
    ).first()
    if record is not None:
        record.albedo_key = albedo_key
        record.normal_key = normal_key
        record.tile_size_cm = body.tile_size_cm
        session.add(record)
        session.commit()

    warning: str | None = None
    if seam > 30.0:
        warning = (
            "This photo tiles with a visible seam. A flatter, evenly lit shot of a "
            "single tile works better."
        )

    size_m = body.tile_size_cm / 100.0
    return RectifyReply(
        upload_id=body.upload_id,
        albedo_url=storage.public_url(albedo_key),
        normal_url=storage.public_url(normal_key),
        roughness_url=storage.public_url(rough_key),
        tile_size_m=[size_m, size_m],
        seam_error=round(seam, 2),
        warning=warning,
    )


@router.get("/floor/{upload_id}", response_model=dict)
def get_upload(
    upload_id: str,
    session: Session = Depends(db_dep),
) -> dict[str, Any]:
    record = session.exec(
        select(UploadRecord).where(UploadRecord.upload_id == upload_id)
    ).first()
    if record is None:
        raise HTTPException(status_code=404, detail="Upload not found.")
    return record.model_dump(mode="json")
