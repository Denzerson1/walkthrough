import base64

# The smallest valid-looking WebP header: RIFF, a size, WEBP.
WEBP = (
    b"RIFF\x1a\x00\x00\x00WEBPVP8L\x0d\x00\x00\x00"
    b"/\x00\x00\x00\x10\x07\x10\x11\x11\x88\x88\xfe\x07\x00"
)


def body(image: bytes = WEBP) -> dict:
    return {
        "position": [0.5, 1.6, -1.0],
        "yaw": 191.0,
        "pitch": -8.0,
        "fov": 60.0,
        "image": base64.b64encode(image).decode(),
    }


class TestEntryView:
    def test_tells_a_visitor_they_are_not_an_editor_without_an_error(self, client):
        resp = client.get("/api/editor/me")
        assert resp.status_code == 200
        assert resp.json() == {"editor": False}

    def test_tells_an_editor_they_are_one(self, editor_client):
        assert editor_client.get("/api/editor/me").json() == {"editor": True}

    def test_needs_the_editor_session(self, client, seeded_project):
        assert client.put(f"/api/projects/{seeded_project}/entry", json=body()).status_code == 401

    def test_saves_the_view_and_its_still(self, editor_client, seeded_project, data_root):
        resp = editor_client.put(f"/api/projects/{seeded_project}/entry", json=body())
        assert resp.status_code == 200
        still = data_root / "projects" / seeded_project / "scene" / "hero.webp"
        assert still.read_bytes() == WEBP
        hero = editor_client.get(f"/api/projects/{seeded_project}/manifest").json()["heroView"]
        assert hero == {"position": [0.5, 1.6, -1.0], "yaw": 191.0, "pitch": -8.0,
                        "fov": 60.0, "image": "hero.webp"}

    def test_rejects_what_is_not_a_webp_still(self, editor_client, seeded_project):
        resp = editor_client.put(f"/api/projects/{seeded_project}/entry", json=body(b"\x89PNG...."))
        assert resp.status_code == 400

    def test_rejects_a_still_far_over_the_budget(self, editor_client, seeded_project):
        huge = WEBP[:12] + b"\x00" * 500_000
        resp = editor_client.put(f"/api/projects/{seeded_project}/entry", json=body(huge))
        assert resp.status_code == 413
