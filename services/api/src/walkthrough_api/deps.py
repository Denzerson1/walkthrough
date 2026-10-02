"""
Shared FastAPI dependencies.

These live in one module because FastAPI identifies dependency overrides by
function object: three identically-written `catalog_dep`s in three routers
are three different objects, so overriding one in a test silently misses the
others.
"""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends, HTTPException, Path
from sqlmodel import Session

from . import projects
from .catalog import Catalog, load_catalog
from .config import Settings, get_settings
from .db import get_session


def db_dep(settings: Settings = Depends(get_settings)) -> Iterator[Session]:
    yield from get_session(settings)


def catalog_dep(settings: Settings = Depends(get_settings)) -> Catalog:
    # Reloaded per request: the catalog is small and this keeps seeding
    # scripts visible without a restart during development.
    return load_catalog(settings.catalog_root)


def valid_project_id(project_id: str) -> str:
    """Validate a project id and translate a bad one into a 400."""
    try:
        return projects.validate_project_id(project_id)
    except projects.InvalidProjectId as exc:
        raise HTTPException(status_code=400, detail="Invalid project id") from exc


def project_id_param(project_id: str = Path(...)) -> str:
    """Path-parameter form of `valid_project_id`, for use with Depends."""
    return valid_project_id(project_id)


def load_manifest_or_404(settings: Settings, project_id: str):
    """Load a project manifest, translating the two failure modes to HTTP."""
    valid_project_id(project_id)
    try:
        return projects.load_manifest(settings, project_id)
    except projects.ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail="Project not found") from exc
