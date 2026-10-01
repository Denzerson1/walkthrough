"""FastAPI application: manifests, catalog, uploads, chat and analytics."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import Body, Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlmodel import Session, select
from walkthrough_pipeline.manifest import Manifest

from . import auth, catalog as catalog_mod, projects
from .config import Settings, get_settings
from .db import AnalyticsEvent, get_session
from .storage import build_storage

app = FastAPI(title="walkthrough API", version="0.1.0")

_settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def settings_dep() -> Settings:
    return get_settings()


def db_dep(settings: Settings = Depends(settings_dep)):
    yield from get_session(settings)


def catalog_dep(settings: Settings = Depends(settings_dep)) -> catalog_mod.Catalog:
    # Reloaded per request: the catalog is small and this keeps seeding scripts
    # visible without a restart during development.
    return catalog_mod.load_catalog(settings.catalog_root)


# --------------------------------------------------------------------------
# Health and projects
# --------------------------------------------------------------------------


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "service": "walkthrough-api", "version": app.version}


@app.get("/api/projects")
def list_projects(settings: Settings = Depends(settings_dep)) -> list[dict[str, str]]:
    return projects.list_projects(settings)


@app.get("/api/projects/{project_id}/manifest")
def get_manifest(project_id: str, settings: Settings = Depends(settings_dep)) -> dict[str, Any]:
    try:
        manifest = projects.load_manifest(settings, project_id)
    except projects.InvalidProjectId as exc:
        raise HTTPException(status_code=400, detail="Invalid project id") from exc
    except projects.ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail="Project not found") from exc
    return manifest.model_dump(mode="json")


@app.put("/api/projects/{project_id}/manifest", dependencies=[Depends(auth.require_editor)])
def put_manifest(
    project_id: str,
    manifest: Manifest,
    settings: Settings = Depends(settings_dep),
) -> dict[str, Any]:
    """Editor save. Requires the editor session cookie."""
    try:
        projects.validate_project_id(project_id)
    except projects.InvalidProjectId as exc:
        raise HTTPException(status_code=400, detail="Invalid project id") from exc
    if manifest.id != project_id:
        raise HTTPException(status_code=400, detail="Manifest id does not match the URL")
    projects.save_manifest(settings, project_id, manifest)
    return {"saved": True, "rooms": len(manifest.rooms)}


# --------------------------------------------------------------------------
# Scene files
# --------------------------------------------------------------------------


@app.get("/files/{key:path}")
def get_file(key: str, settings: Settings = Depends(settings_dep)) -> FileResponse:
    """
    Serve project assets (splats, shading maps, GLBs) from storage.
    In production these are served from R2 instead; this keeps dev simple.
    """
    storage = build_storage(settings)
    try:
        path: Path | None = storage.local_path(key)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid path") from exc
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(path)


# --------------------------------------------------------------------------
# Catalog
# --------------------------------------------------------------------------


@app.get("/api/catalog/{kind}")
def get_catalog(
    kind: str,
    q: str = "",
    limit: int = 50,
    cat: catalog_mod.Catalog = Depends(catalog_dep),
) -> list[dict[str, Any]]:
    if kind not in catalog_mod.KINDS:
        raise HTTPException(status_code=404, detail=f"Unknown catalog kind: {kind}")
    return catalog_mod.search(cat, kind, query=q, limit=limit)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Editor auth
# --------------------------------------------------------------------------


class LoginBody(BaseModel):
    password: str


@app.post("/api/editor/login")
def editor_login(
    request: Request,
    response: Response,
    body: LoginBody,
    settings: Settings = Depends(settings_dep),
) -> dict[str, bool]:
    client_key = request.client.host if request.client else "unknown"
    auth.rate_limit_login(client_key)
    if not auth.verify_password(settings, body.password):
        raise HTTPException(status_code=401, detail="Wrong password")
    auth.issue_session(response, settings)
    return {"ok": True}


@app.post("/api/editor/logout")
def editor_logout(response: Response) -> dict[str, bool]:
    auth.clear_session(response)
    return {"ok": True}


@app.get("/api/editor/session", dependencies=[Depends(auth.require_editor)])
def editor_session() -> dict[str, bool]:
    return {"authenticated": True}


# --------------------------------------------------------------------------
# Analytics (M10)
# --------------------------------------------------------------------------


class EventBody(BaseModel):
    session_id: str
    event: str
    room_id: str | None = None
    value: str | None = None
    duration_ms: int | None = None


ALLOWED_EVENTS = {
    "room_viewed",
    "room_dwell",
    "floor_tried",
    "style_tried",
    "item_added",
    "recolor",
    "share_opened",
    "assistant_message",
}


@app.post("/api/projects/{project_id}/events")
def post_event(
    project_id: str,
    body: EventBody,
    settings: Settings = Depends(settings_dep),
    session: Session = Depends(db_dep),
) -> dict[str, bool]:
    try:
        projects.validate_project_id(project_id)
    except projects.InvalidProjectId as exc:
        raise HTTPException(status_code=400, detail="Invalid project id") from exc
    if body.event not in ALLOWED_EVENTS:
        raise HTTPException(status_code=400, detail=f"Unknown event: {body.event}")
    session.add(
        AnalyticsEvent(
            project_id=project_id,
            session_id=body.session_id[:64],
            event=body.event,
            room_id=body.room_id,
            value=body.value[:200] if body.value else None,
            duration_ms=body.duration_ms,
        )
    )
    session.commit()
    return {"ok": True}


@app.get("/api/projects/{project_id}/summary", dependencies=[Depends(auth.require_editor)])
def project_summary(
    project_id: str,
    session: Session = Depends(db_dep),
) -> dict[str, Any]:
    """Per-project aggregate for the estate-agent summary page (M10)."""
    rows = session.exec(
        select(AnalyticsEvent).where(AnalyticsEvent.project_id == project_id)
    ).all()
    by_event: dict[str, int] = {}
    by_room: dict[str, int] = {}
    dwell_by_room: dict[str, int] = {}
    floors_tried: dict[str, int] = {}
    styles_tried: dict[str, int] = {}
    sessions: set[str] = set()
    for row in rows:
        by_event[row.event] = by_event.get(row.event, 0) + 1
        sessions.add(row.session_id)
        if row.room_id:
            if row.event == "room_viewed":
                by_room[row.room_id] = by_room.get(row.room_id, 0) + 1
            if row.event == "room_dwell" and row.duration_ms:
                dwell_by_room[row.room_id] = dwell_by_room.get(row.room_id, 0) + row.duration_ms
        if row.event == "floor_tried" and row.value:
            floors_tried[row.value] = floors_tried.get(row.value, 0) + 1
        if row.event == "style_tried" and row.value:
            styles_tried[row.value] = styles_tried.get(row.value, 0) + 1
    return {
        "projectId": project_id,
        "sessions": len(sessions),
        "totalEvents": len(rows),
        "byEvent": by_event,
        "roomViews": by_room,
        "dwellMsByRoom": dwell_by_room,
        "floorsTried": floors_tried,
        "stylesTried": styles_tried,
    }


# --------------------------------------------------------------------------
# Routers added by later milestones
# --------------------------------------------------------------------------

from .routes_chat import router as chat_router  # noqa: E402
from .routes_upload import router as upload_router  # noqa: E402

app.include_router(chat_router)
app.include_router(upload_router)


@app.post("/api/_echo", include_in_schema=False)
def echo(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    """Tiny endpoint used by the web app's connectivity check in dev."""
    return {"received": payload}
