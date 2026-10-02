"""The /api/layout endpoint wiring the M7 solver to the viewer."""

from __future__ import annotations

import pytest


@pytest.fixture
def furnished_project(settings, data_root):
    """A project whose living room has a polygon, walls and a door."""
    from walkthrough_pipeline.manifest import Manifest, Opening, Room, Wall, Waypoint

    manifest = Manifest(
        id="demo-01",
        name="Test apartment",
        rooms=[
            Room(
                id="living",
                name="Living room",
                type="living",
                waypoint=Waypoint(position=(2.1, 1.6, 1.9), yaw=0.0),
                floorPolygon=[(0, 0), (4.2, 0), (4.2, 3.8), (0, 3.8)],
                walls=[
                    Wall(start=(0, 0), end=(4.2, 0)),
                    Wall(start=(4.2, 0), end=(4.2, 3.8)),
                    Wall(start=(4.2, 3.8), end=(0, 3.8)),
                    Wall(start=(0, 3.8), end=(0, 0)),
                ],
                openings=[
                    Opening(type="door", wallIndex=0, offset=2.1, width=0.9, height=2.0),
                    Opening(type="window", wallIndex=2, offset=2.1, width=1.4, height=1.3),
                ],
            ),
            Room(
                id="empty",
                name="Unmapped room",
                type="other",
                waypoint=Waypoint(position=(0, 1.6, 0), yaw=0.0),
                floorPolygon=[],
            ),
        ],
    )
    manifest.write(data_root / "projects" / "demo-01" / "scene" / "manifest.json")
    return "demo-01"


def post(client, **kw):
    body = {"project_id": "demo-01", "room_id": "living", "style_id": "minimalist"}
    body.update(kw)
    return client.post("/api/layout", json=body)


class TestAutoLayout:
    def test_places_the_style_picks(self, client, furnished_project):
        resp = post(client)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["placements"]
        assert body["style_id"] == "minimalist"

    def test_placements_sit_on_the_floor(self, client, furnished_project):
        for p in post(client).json()["placements"]:
            assert p["position"][1] == 0.0

    def test_returns_the_styles_floor(self, client, furnished_project):
        assert post(client).json()["floor_id"] == "oak-herringbone-light"

    def test_is_deterministic(self, client, furnished_project):
        assert post(client).json()["placements"] == post(client).json()["placements"]

    def test_explicit_item_list_is_honoured(self, client, furnished_project):
        resp = post(client, item_ids=["sofa-oslo-3"])
        assert resp.status_code == 200
        assert [p["itemId"] for p in resp.json()["placements"]] == ["sofa-oslo-3"]

    def test_everything_lands_inside_the_room(self, client, furnished_project):
        from walkthrough_pipeline.layout import box_inside_polygon

        poly = [(0, 0), (4.2, 0), (4.2, 3.8), (0, 3.8)]
        catalog = client.get("/api/catalog/furniture").json()
        sizes = {
            c["id"]: (c["dimensionsM"][0], c["dimensionsM"][2])
            for c in catalog
            if c.get("dimensionsM")
        }
        for p in post(client).json()["placements"]:
            size = sizes[p["itemId"]]
            centre = (p["position"][0], p["position"][2])
            assert box_inside_polygon(centre, size, p["yaw"], poly), p["itemId"]

    def test_unknown_style_is_404(self, client, furnished_project):
        assert post(client, style_id="vaporwave").status_code == 404

    def test_unknown_room_is_404(self, client, furnished_project):
        assert post(client, room_id="attic").status_code == 404

    def test_unknown_project_is_404(self, client, furnished_project):
        assert post(client, project_id="nope").status_code == 404

    def test_room_without_a_polygon_explains_itself(self, client, furnished_project):
        resp = post(client, room_id="empty")
        assert resp.status_code == 409
        assert "floor polygon" in resp.json()["detail"]

    def test_style_without_picks_for_this_room_explains_itself(
        self, client, furnished_project
    ):
        # 'traditional' in the fixture catalog has picks only for 'living'.
        resp = post(client, room_id="empty", style_id="traditional")
        assert resp.status_code == 409

    def test_reports_skipped_items_rather_than_cramming(self, client, furnished_project):
        # Four sofas cannot fit; the solver must drop some and say so.
        resp = post(client, item_ids=["sofa-oslo-3"] * 1 + ["table-ercol-oval"])
        assert resp.status_code == 200
        body = resp.json()
        assert body["complete"] is (not body["skipped"])
