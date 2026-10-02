"""
Security regressions found in review.

Traversal was already blocked; what was missing was any limit on *which*
in-root files `/files` would serve, and any objection to running with the
published default signing secret.
"""

from __future__ import annotations

import pytest


class TestPublicFileAllowlist:
    def test_serves_a_published_scene_file(self, client, settings, data_root):
        target = data_root / "projects" / "demo-01" / "scene" / "scene.ply"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"ply\n")
        assert client.get("/files/projects/demo-01/scene/scene.ply").status_code == 200

    def test_serves_upload_results(self, client, data_root):
        target = data_root / "uploads" / "demo-01" / "abc" / "albedo.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"png")
        assert client.get("/files/uploads/demo-01/abc/albedo.png").status_code == 200

    def test_refuses_the_raw_capture(self, client, data_root):
        """input/ holds the unmasked source footage (brief §2.5)."""
        target = data_root / "projects" / "demo-01" / "input" / "video.mov"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"RAW CAPTURE")
        assert client.get("/files/projects/demo-01/input/video.mov").status_code == 404

    def test_refuses_intermediate_work_files(self, client, data_root):
        target = data_root / "projects" / "demo-01" / "work" / "frames" / "f_00001.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"frame")
        assert (
            client.get("/files/projects/demo-01/work/frames/f_00001.png").status_code == 404
        )

    def test_refuses_a_file_at_the_data_root(self, client, data_root):
        (data_root / "walkthrough.db").write_bytes(b"SQLite format 3\x00")
        assert client.get("/files/walkthrough.db").status_code == 404

    def test_refuses_an_invalid_project_id(self, client):
        assert client.get("/files/projects/..%2F..%2Fx/scene/a.ply").status_code in (400, 404)

    def test_still_blocks_traversal(self, client):
        assert client.get("/files/../../../etc/passwd").status_code in (400, 404)

    def test_database_lives_outside_the_served_root(self, settings):
        from walkthrough_api.db import get_engine

        engine = get_engine(settings)
        url = str(engine.url)
        if url.startswith("sqlite:///"):
            from pathlib import Path

            db_path = Path(url.removeprefix("sqlite:///")).resolve()
            assert not db_path.is_relative_to(settings.data_root.resolve())


class TestInsecureDefaults:
    """
    SESSION_SECRET ships as "change-me" in .env.example. Left in place, anyone
    could mint their own editor cookie, so the app must refuse rather than
    quietly accept it.
    """

    @pytest.fixture
    def default_secret_client(self, settings, monkeypatch):
        from fastapi.testclient import TestClient
        from walkthrough_api import auth, main

        monkeypatch.setattr(settings, "session_secret", "change-me")
        auth.reset_rate_limit()
        return TestClient(main.app)

    def test_login_is_refused(self, default_secret_client):
        resp = default_secret_client.post(
            "/api/editor/login", json={"password": "test-password"}
        )
        assert resp.status_code == 503
        assert "SESSION_SECRET" in resp.json()["detail"]

    def test_protected_routes_are_refused(self, default_secret_client):
        assert default_secret_client.get("/api/editor/session").status_code == 503

    def test_a_forged_cookie_cannot_get_in(self, default_secret_client):
        from itsdangerous import URLSafeTimedSerializer

        forged = URLSafeTimedSerializer("change-me", salt="wt-editor-session").dumps(
            {"role": "editor"}
        )
        default_secret_client.cookies.set("wt_editor", forged)
        assert default_secret_client.get("/api/editor/session").status_code == 503

    def test_default_password_is_refused(self, settings, monkeypatch):
        from fastapi.testclient import TestClient
        from walkthrough_api import auth, main

        monkeypatch.setattr(settings, "editor_password", "change-me")
        auth.reset_rate_limit()
        client = TestClient(main.app)
        resp = client.post("/api/editor/login", json={"password": "change-me"})
        assert resp.status_code == 503
        assert "EDITOR_PASSWORD" in resp.json()["detail"]

    def test_a_properly_configured_app_still_works(self, client):
        assert (
            client.post("/api/editor/login", json={"password": "test-password"}).status_code
            == 200
        )


class TestUploadValidation:
    def _png(self) -> bytes:
        import numpy as np
        from walkthrough_api import textures

        return textures.encode_png(np.full((32, 32, 3), 128, dtype=np.uint8))

    def test_rejects_a_traversal_project_id(self, client):
        resp = client.post(
            "/api/uploads/floor",
            data={"project_id": "../../escape"},
            files={"file": ("x.png", self._png(), "image/png")},
        )
        assert resp.status_code == 400

    def test_rejects_a_nested_project_id(self, client):
        resp = client.post(
            "/api/uploads/floor",
            data={"project_id": "a/b/c"},
            files={"file": ("x.png", self._png(), "image/png")},
        )
        assert resp.status_code == 400

    def test_rectify_rejects_a_bad_project_id(self, client):
        resp = client.post(
            "/api/uploads/floor/rectify",
            json={
                "upload_id": "abc",
                "project_id": "../etc",
                "corners": [[0, 0], [10, 0], [10, 10], [0, 10]],
                "tile_size_cm": 60,
            },
        )
        assert resp.status_code == 400


class TestAssistantColourValidation:
    def test_rejects_a_non_hex_colour(self, catalog_root):
        import pytest as _pytest
        from walkthrough_api import assistant as asst
        from walkthrough_api.catalog import load_catalog

        catalog = load_catalog(catalog_root)
        ctx = asst.ChatContext(project_id="p", manifest_item_ids=["sofa-1"])
        with _pytest.raises(asst.ValidationFailure):
            asst.validate_action(
                "recolor_item", {"itemId": "sofa-1", "colorHex": "#zzzzzz"}, catalog, ctx
            )

    def test_accepts_a_real_hex_colour(self, catalog_root):
        from walkthrough_api import assistant as asst
        from walkthrough_api.catalog import load_catalog

        catalog = load_catalog(catalog_root)
        ctx = asst.ChatContext(project_id="p", manifest_item_ids=["sofa-1"])
        asst.validate_action(
            "recolor_item", {"itemId": "sofa-1", "colorHex": "#A1b2C3"}, catalog, ctx
        )


class TestGuardIsServerAuthoritative:
    """
    The hallucination guard used to validate rooms and manifest items against
    lists the browser supplied, and skipped the check entirely when they were
    empty — so a caller could bypass it by simply omitting them.
    """

    def test_context_body_does_not_accept_rooms_from_the_client(self):
        from walkthrough_api.routes_chat import ContextBody

        fields = set(ContextBody.model_fields)
        assert "rooms" not in fields
        assert "manifestItemIds" not in fields

    def test_unknown_room_is_rejected_even_with_no_client_context(self, catalog_root):
        import pytest as _pytest
        from walkthrough_api import assistant as asst
        from walkthrough_api.catalog import load_catalog

        catalog = load_catalog(catalog_root)
        # Empty context is what a hostile or buggy client would send.
        ctx = asst.ChatContext(project_id="demo-01")
        with _pytest.raises(asst.ValidationFailure, match="No room"):
            asst.validate_action(
                "set_floor",
                {"roomIds": ["ballroom"], "floorId": "oak-herringbone-light"},
                catalog,
                ctx,
            )

    def test_recolor_of_an_unknown_item_is_rejected_with_no_context(self, catalog_root):
        import pytest as _pytest
        from walkthrough_api import assistant as asst
        from walkthrough_api.catalog import load_catalog

        catalog = load_catalog(catalog_root)
        ctx = asst.ChatContext(project_id="demo-01")
        with _pytest.raises(asst.ValidationFailure, match="no existing item"):
            asst.validate_action(
                "recolor_item", {"itemId": "sofa-1", "colorHex": "#112233"}, catalog, ctx
            )
