"""
Assistant tool handling (M5).

The model itself is not called here — these tests cover the parts that must
hold regardless of what the model says: catalog scoring, the hallucination
guard, and the tool loop's handling of mutating vs read-only tools.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from walkthrough_api import assistant as asst
from walkthrough_api.catalog import load_catalog, score_item, search, summarise


@pytest.fixture
def catalog(catalog_root):
    return load_catalog(catalog_root)


@pytest.fixture
def ctx():
    return asst.ChatContext(
        project_id="demo-01",
        active_room_id="living",
        rooms=[
            {"id": "living", "name": "Living room", "type": "living"},
            {"id": "bedroom", "name": "Bedroom", "type": "bedroom"},
        ],
        manifest_item_ids=["sofa-1", "table-1"],
    )


class TestCatalogScoring:
    def test_exact_id_outranks_everything(self, catalog):
        results = search(catalog, "floors", "terracotta-rustic")
        assert results[0]["id"] == "terracotta-rustic"

    def test_tag_match_beats_a_description_mention(self, catalog):
        results = search(catalog, "floors", "tuscan")
        assert results[0]["id"] == "terracotta-rustic"

    def test_multi_word_query(self, catalog):
        results = search(catalog, "floors", "warm oak")
        assert results[0]["id"] == "oak-herringbone-light"

    def test_category_filter(self, catalog):
        results = search(catalog, "floors", "", filters={"category": "tile"})
        assert [r["id"] for r in results] == ["terracotta-rustic"]

    def test_style_filter_on_a_list_field(self, catalog):
        results = search(catalog, "furniture", "", filters={"styles": "minimalist"})
        assert [r["id"] for r in results] == ["sofa-oslo-3"]

    def test_empty_query_returns_everything_up_to_the_limit(self, catalog):
        assert len(search(catalog, "floors", "", limit=2)) == 2

    def test_no_match_returns_empty(self, catalog):
        assert search(catalog, "floors", "helicopter") == []

    def test_scoring_is_zero_for_an_empty_query(self, catalog):
        assert score_item(catalog.floors[0], "") == 0.0

    def test_results_are_deterministic(self, catalog):
        a = [r["id"] for r in search(catalog, "floors", "warm")]
        b = [r["id"] for r in search(catalog, "floors", "warm")]
        assert a == b

    def test_summaries_stay_compact(self, catalog):
        summary = summarise(catalog.floors[0], "floors")
        assert set(summary) == {"id", "name", "category", "tags", "tileSizeM"}
        assert "description" not in summary


class TestHallucinationGuard:
    def test_accepts_a_real_floor(self, catalog, ctx):
        asst.validate_action(
            "set_floor", {"roomIds": ["living"], "floorId": "oak-herringbone-light"},
            catalog, ctx,
        )

    def test_rejects_an_invented_floor(self, catalog, ctx):
        with pytest.raises(asst.ValidationFailure, match="No floor"):
            asst.validate_action(
                "set_floor", {"roomIds": ["living"], "floorId": "marble-of-atlantis"},
                catalog, ctx,
            )

    def test_rejects_an_invented_room(self, catalog, ctx):
        with pytest.raises(asst.ValidationFailure, match="No room"):
            asst.validate_action(
                "set_floor", {"roomIds": ["ballroom"], "floorId": "oak-herringbone-light"},
                catalog, ctx,
            )

    def test_allows_the_all_keyword(self, catalog, ctx):
        asst.validate_action(
            "set_floor", {"roomIds": ["all"], "floorId": "oak-herringbone-light"}, catalog, ctx
        )

    def test_rejects_an_invented_style(self, catalog, ctx):
        with pytest.raises(asst.ValidationFailure, match="No style"):
            asst.validate_action(
                "apply_style", {"roomId": "living", "styleId": "brutalist-dream"}, catalog, ctx
            )

    def test_rejects_invented_furniture(self, catalog, ctx):
        with pytest.raises(asst.ValidationFailure, match="No furniture"):
            asst.validate_action(
                "add_item", {"roomId": "living", "itemId": "chair-of-destiny"}, catalog, ctx
            )

    def test_rejects_recolor_of_a_nonexistent_item(self, catalog, ctx):
        with pytest.raises(asst.ValidationFailure, match="no existing item"):
            asst.validate_action(
                "recolor_item", {"itemId": "ghost-sofa", "colorHex": "#112233"}, catalog, ctx
            )

    def test_rejects_a_malformed_colour(self, catalog, ctx):
        with pytest.raises(asst.ValidationFailure, match="colour"):
            asst.validate_action(
                "recolor_item", {"itemId": "sofa-1", "colorHex": "reddish"}, catalog, ctx
            )

    def test_accepts_a_valid_recolor(self, catalog, ctx):
        asst.validate_action(
            "recolor_item", {"itemId": "sofa-1", "colorHex": "#2f4f4f"}, catalog, ctx
        )


class TestReadTools:
    def test_get_context_reports_the_active_room(self, catalog, ctx):
        out = asst.run_read_tool("get_context", {}, catalog, ctx)
        assert out["activeRoom"]["id"] == "living"
        assert len(out["allRooms"]) == 2

    def test_search_catalog_returns_summaries(self, catalog, ctx):
        out = asst.run_read_tool(
            "search_catalog", {"kind": "floors", "query": "oak", "category": ""}, catalog, ctx
        )
        assert out["count"] >= 1
        assert "oak-herringbone-light" in [r["id"] for r in out["results"]]

    def test_search_catalog_says_so_when_nothing_matches(self, catalog, ctx):
        out = asst.run_read_tool(
            "search_catalog", {"kind": "floors", "query": "spaceship", "category": ""},
            catalog, ctx,
        )
        assert out["count"] == 0
        assert "Nothing matched" in out["note"]

    def test_unknown_tool_raises(self, catalog, ctx):
        with pytest.raises(asst.ValidationFailure):
            asst.run_read_tool("drop_tables", {}, catalog, ctx)


class TestToolDefinitions:
    def test_every_action_tool_is_defined(self):
        names = {t["name"] for t in asst.tool_definitions()}
        assert names >= asst.ACTION_TOOLS

    def test_all_tools_are_strict(self):
        assert all(t["strict"] for t in asst.tool_definitions())

    def test_schemas_disallow_extra_properties(self):
        for tool in asst.tool_definitions():
            assert tool["input_schema"]["additionalProperties"] is False

    def test_required_lists_match_the_properties(self):
        for tool in asst.tool_definitions():
            schema = tool["input_schema"]
            assert set(schema["required"]) == set(schema["properties"])


# ---------------------------------------------------------------------------
# Tool loop, driven by a fake client so no API key or network is needed.
# ---------------------------------------------------------------------------


class FakeBlock:
    def __init__(self, **kw: Any) -> None:
        self.__dict__.update(kw)


class FakeUsage:
    def __init__(self, i: int = 10, o: int = 20) -> None:
        self.input_tokens = i
        self.output_tokens = o


class FakeResponse:
    def __init__(self, content: list[Any]) -> None:
        self.content = content
        self.usage = FakeUsage()
        self.stop_reason = "tool_use" if any(
            getattr(b, "type", "") == "tool_use" for b in content
        ) else "end_turn"


class FakeMessages:
    def __init__(self, scripted: list[list[Any]]) -> None:
        self.scripted = scripted
        self.calls: list[dict] = []

    def create(self, **kwargs: Any) -> FakeResponse:
        self.calls.append(kwargs)
        return FakeResponse(self.scripted[len(self.calls) - 1])


class FakeClient:
    def __init__(self, scripted: list[list[Any]]) -> None:
        self.messages = FakeMessages(scripted)


@pytest.fixture
def run_loop(settings, catalog, ctx):
    from walkthrough_api.routes_chat import run_conversation

    def _run(scripted):
        client = FakeClient(scripted)
        messages = [{"role": "user", "content": "hi"}]
        return run_conversation(client, settings, catalog, ctx, messages), client

    return _run


class TestToolLoop:
    def test_plain_reply_without_tools(self, run_loop):
        reply, _ = run_loop([[FakeBlock(type="text", text="Hello there.")]])
        assert reply.reply == "Hello there."
        assert reply.actions == []

    def test_collects_a_mutating_action_for_the_client(self, run_loop):
        reply, _ = run_loop([
            [FakeBlock(
                type="tool_use", id="t1", name="set_floor",
                input={"roomIds": ["living"], "floorId": "oak-herringbone-light"},
            )],
            [FakeBlock(type="text", text="Warm oak suits this room.")],
        ])
        assert len(reply.actions) == 1
        assert reply.actions[0]["tool"] == "set_floor"
        assert reply.actions[0]["args"]["floorId"] == "oak-herringbone-light"
        assert reply.reply == "Warm oak suits this room."

    def test_executes_a_read_tool_server_side(self, run_loop):
        reply, client = run_loop([
            [FakeBlock(
                type="tool_use", id="t1", name="search_catalog",
                input={"kind": "floors", "query": "tuscan", "category": ""},
            )],
            [FakeBlock(type="text", text="Terracotta it is.")],
        ])
        # The second request must carry the search results.
        second = client.messages.calls[1]
        tool_result = second["messages"][-1]["content"][0]
        payload = json.loads(tool_result["content"])
        assert "terracotta-rustic" in [r["id"] for r in payload["results"]]
        assert reply.reply == "Terracotta it is."

    def test_hallucinated_id_becomes_an_error_result_not_an_action(self, run_loop):
        reply, client = run_loop([
            [FakeBlock(
                type="tool_use", id="t1", name="set_floor",
                input={"roomIds": ["living"], "floorId": "unobtanium"},
            )],
            [FakeBlock(type="text", text="Sorry, we do not have that.")],
        ])
        assert reply.actions == []
        result = client.messages.calls[1]["messages"][-1]["content"][0]
        assert result["is_error"] is True
        assert "No floor" in result["content"]

    def test_parallel_tool_results_go_back_in_one_message(self, run_loop):
        _, client = run_loop([
            [
                FakeBlock(type="tool_use", id="t1", name="get_context", input={}),
                FakeBlock(
                    type="tool_use", id="t2", name="search_catalog",
                    input={"kind": "floors", "query": "oak", "category": ""},
                ),
            ],
            [FakeBlock(type="text", text="Done.")],
        ])
        last = client.messages.calls[1]["messages"][-1]
        assert last["role"] == "user"
        assert len(last["content"]) == 2

    def test_usage_is_accumulated_across_turns(self, run_loop):
        reply, _ = run_loop([
            [FakeBlock(type="tool_use", id="t1", name="get_context", input={})],
            [FakeBlock(type="text", text="Done.")],
        ])
        assert reply.usage["input_tokens"] == 20
        assert reply.usage["output_tokens"] == 40

    def test_loop_stops_at_max_turns(self, run_loop):
        forever = [
            [FakeBlock(type="tool_use", id=f"t{i}", name="get_context", input={})]
            for i in range(asst.MAX_TURNS)
        ]
        reply, client = run_loop(forever)
        assert len(client.messages.calls) == asst.MAX_TURNS
        assert reply.reply

    def test_chips_are_offered(self, run_loop):
        reply, _ = run_loop([[FakeBlock(type="text", text="Hi.")]])
        assert 1 <= len(reply.chips) <= 4


class TestChatEndpoint:
    def test_missing_api_key_gives_a_clear_503(self, client, seeded_project):
        resp = client.post(
            "/api/chat",
            json={
                "session_id": "s1",
                "project_id": seeded_project,
                "message": "make it warmer",
            },
        )
        assert resp.status_code == 503
        assert "ANTHROPIC_API_KEY" in resp.json()["detail"]

    def test_empty_message_is_rejected(self, client, seeded_project):
        resp = client.post(
            "/api/chat",
            json={"session_id": "s1", "project_id": seeded_project, "message": "   "},
        )
        assert resp.status_code in (400, 503)

    def test_rate_limit_applies(self, client, seeded_project, settings, monkeypatch):
        monkeypatch.setattr(settings, "assistant_rate_limit_per_hour", 3)
        codes = []
        for _ in range(5):
            codes.append(
                client.post(
                    "/api/chat",
                    json={"session_id": "s9", "project_id": seeded_project, "message": "hi"},
                ).status_code
            )
        assert 429 in codes
