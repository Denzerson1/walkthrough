"""
The one AI assistant (M5): floors, furniture, styles and recolor.

Design, per docs/BRIEF.md §M5:

* Read-only tools (get_context, search_catalog) run HERE, server-side, because
  the catalog lives on the server and the model needs the results to choose.
* Mutating tools (set_floor, add_item, ...) are NOT applied server-side. They
  are collected as "actions" and returned to the web app, which applies them to
  the viewer state. The loop feeds the model a synthetic acknowledgement so it
  can finish with a one-sentence explanation.
* Every id the model proposes is validated against the catalog before it is
  returned, so the assistant can never invent an item that does not exist.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from .catalog import Catalog, CatalogKind, search, summarise

#: A length check alone let "#zzzzzz" through to three.js, which warns and
#: silently falls back to white.
_HEX_COLOUR = re.compile(r"#[0-9a-fA-F]{6}")

# Mutating tools the client applies to the viewer state.
ACTION_TOOLS = {
    "set_floor",
    "apply_style",
    "add_item",
    "auto_layout",
    "recolor_item",
    "reset",
}

SYSTEM_PROMPT = """\
You are the interior assistant inside a 3D apartment walkthrough. You help a \
buyer or renter restyle the apartment they are looking at.

Rules you must follow:

1. Only choose items that exist in our catalog. Always call search_catalog \
before naming a floor, furniture item or style, and only use ids that the \
search returned. Never invent an id, and never claim an item exists when it \
does not.
2. Translate vague, emotional or unusual requests into concrete attributes \
first. "Tuscan" means warm terracotta, aged wood, ochre. "2010 millennial \
optimism" means light oak, white walls, soft pastels, mid-century shapes. \
Search for those attributes, pick the closest matches, and say in one short \
sentence why you chose them.
3. If nothing in the catalog fits, say so plainly and offer the closest \
alternative. Do not pretend.
4. Keep replies to one or two short sentences. The user is on a phone looking \
at a room, not reading an article.
5. Call get_context when you need to know which room the user is in or what \
has already been changed.
6. Prefer one decisive change over asking a clarifying question. Only ask when \
the request is genuinely ambiguous about which room to change.
"""


def tool_definitions() -> list[dict[str, Any]]:
    """
    Tool schemas for the Messages API.

    strict=True is set at the top level of each tool (not on tool_choice) so
    arguments are guaranteed to validate against the schema.
    """

    def tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
        return {
            "name": name,
            "description": description,
            "strict": True,
            "input_schema": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        }

    return [
        tool(
            "get_context",
            "Get the room the user is currently in, its type and size, and "
            "which floors, furniture and recolors are already applied.",
            {},
            [],
        ),
        tool(
            "search_catalog",
            "Search our catalog of floors, furniture or styles by keywords and "
            "attributes. Always call this before naming an id.",
            {
                "kind": {
                    "type": "string",
                    "enum": ["floors", "furniture", "styles"],
                    "description": "Which catalog to search.",
                },
                "query": {
                    "type": "string",
                    "description": "Attribute keywords, e.g. 'warm oak herringbone' "
                    "or 'low linen sofa'.",
                },
                "category": {
                    "type": "string",
                    "description": "Optional category filter, e.g. 'wood', 'sofa'. "
                    "Empty string for no filter.",
                },
            },
            ["kind", "query", "category"],
        ),
        tool(
            "set_floor",
            "Change the floor of one or more rooms to a catalog floor.",
            {
                "roomIds": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Room ids, or the single value ['all'] for every room.",
                },
                "floorId": {"type": "string", "description": "Catalog floor id."},
            },
            ["roomIds", "floorId"],
        ),
        tool(
            "apply_style",
            "Apply a whole style to a room: its floor and a set of furniture.",
            {
                "roomId": {"type": "string"},
                "styleId": {"type": "string", "description": "Catalog style id."},
            },
            ["roomId", "styleId"],
        ),
        tool(
            "add_item",
            "Add one catalog furniture item to a room.",
            {
                "roomId": {"type": "string"},
                "itemId": {"type": "string", "description": "Catalog furniture id."},
            },
            ["roomId", "itemId"],
        ),
        tool(
            "auto_layout",
            "Automatically furnish a room in a style, placing items sensibly.",
            {
                "roomId": {"type": "string"},
                "styleId": {"type": "string"},
            },
            ["roomId", "styleId"],
        ),
        tool(
            "recolor_item",
            "Recolor a piece of furniture that is already in the apartment.",
            {
                "itemId": {
                    "type": "string",
                    "description": "Manifest item id of existing furniture.",
                },
                "colorHex": {
                    "type": "string",
                    "description": "Colour as #rrggbb.",
                },
            },
            ["itemId", "colorHex"],
        ),
        tool(
            "reset",
            "Undo all changes and return to the original apartment.",
            {
                "roomId": {
                    "type": "string",
                    "description": "Room id, or 'all' for the whole apartment.",
                }
            },
            ["roomId"],
        ),
    ]


@dataclass
class ChatContext:
    """What the web app tells us about the live viewer."""

    project_id: str
    active_room_id: str | None = None
    rooms: list[dict[str, Any]] = field(default_factory=list)
    applied_floors: dict[str, str] = field(default_factory=dict)
    placed_items: list[dict[str, Any]] = field(default_factory=list)
    recolors: dict[str, str] = field(default_factory=dict)
    manifest_item_ids: list[str] = field(default_factory=list)

    def describe(self) -> dict[str, Any]:
        active = next((r for r in self.rooms if r.get("id") == self.active_room_id), None)
        return {
            "activeRoom": active,
            "allRooms": [
                {"id": r.get("id"), "name": r.get("name"), "type": r.get("type")}
                for r in self.rooms
            ],
            "appliedFloors": self.applied_floors,
            "placedItems": [
                {"itemId": i.get("itemId"), "roomId": i.get("roomId")} for i in self.placed_items
            ],
            "recolors": self.recolors,
            "existingFurnitureIds": self.manifest_item_ids,
        }


class ValidationFailure(Exception):
    """Raised when the model proposes an id that is not in the catalog."""


def validate_action(name: str, args: dict[str, Any], catalog: Catalog, ctx: ChatContext) -> None:
    """
    Guard against hallucinated ids. The brief requires that the assistant can
    never claim an item exists that does not.
    """
    room_ids = {r.get("id") for r in ctx.rooms}

    def check_room(room_id: str) -> None:
        if room_id != "all" and room_ids and room_id not in room_ids:
            raise ValidationFailure(f"No room called {room_id!r} in this apartment.")

    if name == "set_floor":
        for rid in args.get("roomIds", []):
            check_room(rid)
        floor_id = args.get("floorId", "")
        if not catalog.exists("floors", floor_id):
            raise ValidationFailure(f"No floor with id {floor_id!r} in the catalog.")
    elif name in ("apply_style", "auto_layout"):
        check_room(args.get("roomId", ""))
        style_id = args.get("styleId", "")
        if not catalog.exists("styles", style_id):
            raise ValidationFailure(f"No style with id {style_id!r} in the catalog.")
    elif name == "add_item":
        check_room(args.get("roomId", ""))
        item_id = args.get("itemId", "")
        if not catalog.exists("furniture", item_id):
            raise ValidationFailure(f"No furniture with id {item_id!r} in the catalog.")
    elif name == "recolor_item":
        item_id = args.get("itemId", "")
        if ctx.manifest_item_ids and item_id not in ctx.manifest_item_ids:
            raise ValidationFailure(f"There is no existing item {item_id!r} to recolor.")
        colour = str(args.get("colorHex", ""))
        if not _HEX_COLOUR.fullmatch(colour):
            raise ValidationFailure(f"{colour!r} is not a #rrggbb colour.")
    elif name == "reset":
        check_room(args.get("roomId", "all"))


def run_read_tool(
    name: str, args: dict[str, Any], catalog: Catalog, ctx: ChatContext
) -> dict[str, Any]:
    """Execute a server-side read-only tool."""
    if name == "get_context":
        return ctx.describe()
    if name == "search_catalog":
        kind: CatalogKind = args.get("kind", "floors")  # type: ignore[assignment]
        category = args.get("category") or None
        filters = {"category": category} if category else None
        results = search(catalog, kind, query=args.get("query", ""), filters=filters, limit=8)
        return {
            "results": [summarise(r, kind) for r in results],
            "count": len(results),
            "note": "Use only the ids listed here." if results else "Nothing matched this query.",
        }
    raise ValidationFailure(f"Unknown read tool {name!r}")


def extract_text(content: list[Any]) -> str:
    parts: list[str] = []
    for block in content:
        btype = getattr(block, "type", None) or (
            block.get("type") if isinstance(block, dict) else None
        )
        if btype == "text":
            text = getattr(block, "text", None) or block.get("text", "")  # type: ignore[union-attr]
            parts.append(text)
    return " ".join(p.strip() for p in parts if p.strip()).strip()


def tool_uses(content: list[Any]) -> list[Any]:
    out = []
    for block in content:
        btype = getattr(block, "type", None) or (
            block.get("type") if isinstance(block, dict) else None
        )
        if btype == "tool_use":
            out.append(block)
    return out


def block_field(block: Any, name: str) -> Any:
    if isinstance(block, dict):
        return block.get(name)
    return getattr(block, name, None)


def suggest_chips(actions: list[dict[str, Any]], catalog: Catalog) -> list[str]:
    """Quick-reply chips shown under the assistant's reply."""
    if not actions:
        return ["Change the floor", "Furnish this room", "Show me something warmer"]
    kinds = {a["tool"] for a in actions}
    chips: list[str] = []
    if "set_floor" in kinds:
        chips += ["Something darker", "Apply to every room", "Undo"]
    elif "apply_style" in kinds or "auto_layout" in kinds:
        chips += ["Try another style", "Fewer pieces", "Undo"]
    elif "recolor_item" in kinds:
        chips += ["A bit lighter", "Back to original"]
    else:
        chips += ["Undo", "Something else"]
    if len(catalog.styles) > 1 and "Try another style" not in chips:
        chips.append("Try another style")
    return chips[:4]


MAX_TURNS = 6


def build_messages(history: list[dict[str, Any]], user_message: str) -> list[dict[str, Any]]:
    messages = [
        {"role": m["role"], "content": m["content"]}
        for m in history
        if m.get("role") in ("user", "assistant") and m.get("content")
    ]
    messages.append({"role": "user", "content": user_message})
    return messages


def serialise_result(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)
