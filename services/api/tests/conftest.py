from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def data_root(tmp_path: Path) -> Path:
    root = tmp_path / "data"
    root.mkdir()
    return root


@pytest.fixture
def catalog_root(tmp_path: Path) -> Path:
    root = tmp_path / "catalog"
    (root / "floors").mkdir(parents=True)
    (root / "furniture").mkdir(parents=True)
    (root / "styles").mkdir(parents=True)

    floors = [
        {
            "id": "oak-herringbone-light",
            "name": "Light oak herringbone",
            "category": "wood",
            "tags": ["warm", "scandinavian", "classic", "oak"],
            "description": "Pale oak laid in a herringbone pattern.",
            "tileSizeM": [0.6, 0.6],
            "license": "CC0",
        },
        {
            "id": "terracotta-rustic",
            "name": "Rustic terracotta",
            "category": "tile",
            "tags": ["warm", "tuscan", "rustic", "earthy", "terracotta"],
            "description": "Aged terracotta tiles.",
            "tileSizeM": [0.3, 0.3],
            "license": "CC0",
        },
        {
            "id": "concrete-polished",
            "name": "Polished concrete",
            "category": "stone",
            "tags": ["cool", "industrial", "minimalist", "grey"],
            "description": "Smooth polished concrete.",
            "tileSizeM": [1.0, 1.0],
            "glossy": True,
            "license": "CC0",
        },
    ]
    for f in floors:
        (root / "floors" / f"{f['id']}.json").write_text(json.dumps(f), encoding="utf-8")

    furniture = [
        {
            "id": "sofa-oslo-3",
            "name": "Oslo 3-seat sofa",
            "category": "sofa",
            "styles": ["modern", "minimalist"],
            "colors": ["#c8c2b8"],
            "materials": ["linen", "oak"],
            "dimensionsM": [2.1, 0.8, 0.9],
            "license": "CC0",
        },
        {
            "id": "table-ercol-oval",
            "name": "Oval dining table",
            "category": "dining_table",
            "styles": ["mid-century", "traditional"],
            "colors": ["#8a6a43"],
            "materials": ["oak"],
            "dimensionsM": [1.6, 0.75, 0.9],
            "license": "CC0",
        },
    ]
    for f in furniture:
        (root / "furniture" / f"{f['id']}.json").write_text(json.dumps(f), encoding="utf-8")

    styles = [
        {
            "id": "minimalist",
            "name": "Minimalist",
            "description": "Few pieces, pale woods, calm palette.",
            "palette": ["#f4f1ec", "#c8c2b8"],
            "floorIds": ["oak-herringbone-light"],
            "picks": {"living": ["sofa-oslo-3"]},
        },
        {
            "id": "traditional",
            "name": "Traditional",
            "description": "Warm woods and classic shapes.",
            "palette": ["#6b4a2f", "#b89b74"],
            "floorIds": ["terracotta-rustic"],
            "picks": {"living": ["table-ercol-oval"]},
        },
    ]
    for s in styles:
        (root / "styles" / f"{s['id']}.json").write_text(json.dumps(s), encoding="utf-8")

    return root


@pytest.fixture
def settings(data_root: Path, catalog_root: Path, monkeypatch: pytest.MonkeyPatch):
    from walkthrough_api import config, db

    monkeypatch.setenv("STORAGE_LOCAL_ROOT", str(data_root))
    monkeypatch.setenv("EDITOR_PASSWORD", "test-password")
    monkeypatch.setenv("SESSION_SECRET", "test-secret-value")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(data_root / 'test.db').as_posix()}")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")

    config.get_settings.cache_clear()
    db.reset_engine()

    s = config.get_settings()
    # catalog_root is derived from the repo root, so point it at the fixture.
    monkeypatch.setattr(type(s), "catalog_root", property(lambda self: catalog_root))
    yield s
    config.get_settings.cache_clear()
    db.reset_engine()


@pytest.fixture
def client(settings) -> TestClient:
    from walkthrough_api import auth, main
    from walkthrough_api.routes_chat import reset_rate_limit

    auth.reset_rate_limit()
    reset_rate_limit()
    return TestClient(main.app)


@pytest.fixture
def seeded_project(settings, data_root: Path) -> str:
    """A minimal two-room project on disk."""
    from walkthrough_pipeline.manifest import Manifest, Room, Waypoint

    manifest = Manifest(
        id="demo-01",
        name="Test apartment",
        rooms=[
            Room(
                id="living", name="Living room", type="living",
                waypoint=Waypoint(position=(2.0, 1.6, 2.0), yaw=0.0),
                floorPolygon=[(0, 0), (4, 0), (4, 3), (0, 3)],
            ),
            Room(
                id="bedroom", name="Bedroom", type="bedroom",
                waypoint=Waypoint(position=(6.0, 1.6, 2.0), yaw=90.0),
                floorPolygon=[(4, 0), (7, 0), (7, 3), (4, 3)],
            ),
        ],
    )
    path = data_root / "projects" / "demo-01" / "scene" / "manifest.json"
    manifest.write(path)
    return "demo-01"


@pytest.fixture
def editor_client(client: TestClient) -> TestClient:
    resp = client.post("/api/editor/login", json={"password": "test-password"})
    assert resp.status_code == 200
    return client
