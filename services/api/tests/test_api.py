"""API surface: health, manifests, catalog, auth, analytics, storage safety."""

from __future__ import annotations

import pytest
from walkthrough_api.storage import LocalStorage, StorageError


class TestHealth:
    def test_health_is_ok(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


class TestManifest:
    def test_serves_a_seeded_manifest(self, client, seeded_project):
        resp = client.get(f"/api/projects/{seeded_project}/manifest")
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == "demo-01"
        assert len(body["rooms"]) == 2
        assert body["units"] == "m"
        assert body["upAxis"] == "y"

    def test_unknown_project_is_404(self, client):
        assert client.get("/api/projects/nope/manifest").status_code == 404

    def test_path_traversal_project_id_is_rejected(self, client):
        resp = client.get("/api/projects/..%2F..%2Fetc/manifest")
        assert resp.status_code in (400, 404)

    def test_listing_projects(self, client, seeded_project):
        rows = client.get("/api/projects").json()
        assert any(r["id"] == "demo-01" for r in rows)

    def test_saving_requires_the_editor_session(self, client, seeded_project):
        manifest = client.get(f"/api/projects/{seeded_project}/manifest").json()
        resp = client.put(f"/api/projects/{seeded_project}/manifest", json=manifest)
        assert resp.status_code == 401

    def test_editor_can_save(self, editor_client, seeded_project):
        manifest = editor_client.get(f"/api/projects/{seeded_project}/manifest").json()
        manifest["name"] = "Renamed"
        resp = editor_client.put(f"/api/projects/{seeded_project}/manifest", json=manifest)
        assert resp.status_code == 200
        assert editor_client.get(
            f"/api/projects/{seeded_project}/manifest"
        ).json()["name"] == "Renamed"

    def test_manifest_id_must_match_the_url(self, editor_client, seeded_project):
        manifest = editor_client.get(f"/api/projects/{seeded_project}/manifest").json()
        manifest["id"] = "something-else"
        resp = editor_client.put(f"/api/projects/{seeded_project}/manifest", json=manifest)
        assert resp.status_code == 400

    def test_invalid_manifest_is_rejected(self, editor_client, seeded_project):
        resp = editor_client.put(
            f"/api/projects/{seeded_project}/manifest",
            json={"id": "demo-01", "name": "x", "transform": [[1, 0], [0, 1]]},
        )
        assert resp.status_code == 422


class TestEditorAuth:
    def test_wrong_password_is_rejected(self, client):
        assert client.post("/api/editor/login", json={"password": "wrong"}).status_code == 401

    def test_correct_password_sets_a_session(self, client):
        resp = client.post("/api/editor/login", json={"password": "test-password"})
        assert resp.status_code == 200
        assert client.get("/api/editor/session").status_code == 200

    def test_session_cookie_is_http_only(self, client):
        resp = client.post("/api/editor/login", json={"password": "test-password"})
        assert "httponly" in resp.headers["set-cookie"].lower()

    def test_logout_clears_the_session(self, client):
        client.post("/api/editor/login", json={"password": "test-password"})
        client.post("/api/editor/logout")
        assert client.get("/api/editor/session").status_code == 401

    def test_tampered_cookie_is_rejected(self, client):
        client.post("/api/editor/login", json={"password": "test-password"})
        client.cookies.set("wt_editor", "forged-value")
        assert client.get("/api/editor/session").status_code == 401

    def test_repeated_failures_are_rate_limited(self, client):
        codes = [
            client.post("/api/editor/login", json={"password": "bad"}).status_code
            for _ in range(12)
        ]
        assert 429 in codes


class TestCatalog:
    def test_lists_floors(self, client):
        rows = client.get("/api/catalog/floors").json()
        assert len(rows) == 3

    def test_unknown_kind_is_404(self, client):
        assert client.get("/api/catalog/sofas").status_code == 404

    def test_search_ranks_tag_matches_first(self, client):
        rows = client.get("/api/catalog/floors?q=tuscan").json()
        assert rows[0]["id"] == "terracotta-rustic"

    def test_search_with_no_match_is_empty(self, client):
        assert client.get("/api/catalog/floors?q=zzzzqqq").json() == []


class TestAnalytics:
    def test_records_an_event(self, client, seeded_project):
        resp = client.post(
            f"/api/projects/{seeded_project}/events",
            json={"session_id": "s1", "event": "room_viewed", "room_id": "living"},
        )
        assert resp.status_code == 200

    def test_rejects_an_unknown_event(self, client, seeded_project):
        resp = client.post(
            f"/api/projects/{seeded_project}/events",
            json={"session_id": "s1", "event": "haxx"},
        )
        assert resp.status_code == 400

    def test_summary_requires_the_editor(self, client, seeded_project):
        assert client.get(f"/api/projects/{seeded_project}/summary").status_code == 401

    def test_summary_aggregates(self, editor_client, seeded_project):
        for room in ("living", "living", "bedroom"):
            editor_client.post(
                f"/api/projects/{seeded_project}/events",
                json={"session_id": "s1", "event": "room_viewed", "room_id": room},
            )
        editor_client.post(
            f"/api/projects/{seeded_project}/events",
            json={
                "session_id": "s1", "event": "floor_tried",
                "value": "oak-herringbone-light",
            },
        )
        summary = editor_client.get(f"/api/projects/{seeded_project}/summary").json()
        assert summary["roomViews"]["living"] == 2
        assert summary["roomViews"]["bedroom"] == 1
        assert summary["floorsTried"]["oak-herringbone-light"] == 1
        assert summary["sessions"] == 1


class TestLocalStorage:
    def test_round_trips_bytes(self, tmp_path):
        storage = LocalStorage(tmp_path)
        storage.write_bytes("a/b.txt", b"hello")
        assert storage.read_bytes("a/b.txt") == b"hello"

    def test_exists_and_delete(self, tmp_path):
        storage = LocalStorage(tmp_path)
        storage.write_bytes("x.txt", b"1")
        assert storage.exists("x.txt")
        storage.delete("x.txt")
        assert not storage.exists("x.txt")

    def test_rejects_parent_traversal(self, tmp_path):
        storage = LocalStorage(tmp_path / "root")
        with pytest.raises(StorageError):
            storage.write_bytes("../escape.txt", b"nope")

    def test_rejects_backslash_traversal(self, tmp_path):
        storage = LocalStorage(tmp_path / "root")
        with pytest.raises(StorageError):
            storage.read_bytes("..\\escape.txt")

    def test_rejects_an_empty_key(self, tmp_path):
        storage = LocalStorage(tmp_path)
        with pytest.raises(StorageError):
            storage.write_bytes("/", b"x")

    def test_missing_file_raises_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            LocalStorage(tmp_path).read_bytes("missing.txt")

    def test_writes_are_atomic_leaving_no_temp_file(self, tmp_path):
        storage = LocalStorage(tmp_path)
        storage.write_bytes("f.bin", b"data")
        assert list(tmp_path.glob("*.tmp")) == []

    def test_s3_backend_refuses_rather_than_pretending(self, settings, monkeypatch):
        from walkthrough_api.storage import build_storage

        monkeypatch.setattr(settings, "storage_backend", "s3")
        with pytest.raises(StorageError, match="not implemented"):
            build_storage(settings)
