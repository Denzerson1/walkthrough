"""Project discovery and manifest access on top of the storage adapter."""

from __future__ import annotations

import re
from pathlib import Path

from walkthrough_pipeline.manifest import Manifest

from .config import Settings

PROJECT_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")


class ProjectNotFound(Exception):
    pass


class InvalidProjectId(Exception):
    pass


def validate_project_id(project_id: str) -> str:
    if not PROJECT_ID_RE.match(project_id):
        raise InvalidProjectId(project_id)
    return project_id


def project_dir(settings: Settings, project_id: str) -> Path:
    validate_project_id(project_id)
    return settings.data_root / "projects" / project_id


def manifest_path(settings: Settings, project_id: str) -> Path:
    return project_dir(settings, project_id) / "scene" / "manifest.json"


def load_manifest(settings: Settings, project_id: str) -> Manifest:
    path = manifest_path(settings, project_id)
    if not path.is_file():
        raise ProjectNotFound(project_id)
    return Manifest.load(path)


def save_manifest(settings: Settings, project_id: str, manifest: Manifest) -> None:
    manifest.write(manifest_path(settings, project_id))


def list_projects(settings: Settings) -> list[dict[str, str]]:
    root = settings.data_root / "projects"
    if not root.is_dir():
        return []
    out: list[dict[str, str]] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        manifest_file = child / "scene" / "manifest.json"
        if not manifest_file.is_file():
            continue
        try:
            manifest = Manifest.load(manifest_file)
        except Exception:
            # A half-written project should not break the whole listing.
            continue
        out.append({"id": manifest.id, "name": manifest.name, "rooms": str(len(manifest.rooms))})
    return out
