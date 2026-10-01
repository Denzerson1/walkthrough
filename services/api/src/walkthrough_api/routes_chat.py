"""POST /api/chat — the assistant endpoint (M5)."""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import assistant as asst
from .catalog import Catalog, load_catalog
from .config import Settings, get_settings

router = APIRouter(prefix="/api", tags=["assistant"])

# Cost guard: request timestamps per session (brief §M5).
_CALLS: dict[str, list[float]] = defaultdict(list)
_WINDOW_SECONDS = 3600


def _rate_limit(session_id: str, limit: int) -> None:
    now = time.monotonic()
    calls = [t for t in _CALLS[session_id] if now - t < _WINDOW_SECONDS]
    if len(calls) >= limit:
        raise HTTPException(
            status_code=429,
            detail="You have reached the assistant limit for this session.",
        )
    calls.append(now)
    _CALLS[session_id] = calls


def reset_rate_limit() -> None:
    """Test hook."""
    _CALLS.clear()


class ContextBody(BaseModel):
    activeRoomId: str | None = None
    rooms: list[dict[str, Any]] = Field(default_factory=list)
    appliedFloors: dict[str, str] = Field(default_factory=dict)
    placedItems: list[dict[str, Any]] = Field(default_factory=list)
    recolors: dict[str, str] = Field(default_factory=dict)
    manifestItemIds: list[str] = Field(default_factory=list)


class ChatBody(BaseModel):
    session_id: str
    project_id: str
    message: str
    history: list[dict[str, Any]] = Field(default_factory=list)
    context: ContextBody = Field(default_factory=ContextBody)


class ChatReply(BaseModel):
    reply: str
    actions: list[dict[str, Any]]
    chips: list[str]
    usage: dict[str, int]
    model: str


def catalog_dep(settings: Settings = Depends(get_settings)) -> Catalog:
    return load_catalog(settings.catalog_root)


def _build_client(settings: Settings):
    """
    Import lazily so the rest of the API works (and tests run) without the
    anthropic package configured.
    """
    if not settings.anthropic_api_key:
        raise HTTPException(
            status_code=503,
            detail=(
                "The assistant is not configured: ANTHROPIC_API_KEY is unset. "
                "Add it to .env (see .env.example)."
            ),
        )
    import anthropic

    return anthropic.Anthropic(api_key=settings.anthropic_api_key)


def run_conversation(
    client: Any,
    settings: Settings,
    catalog: Catalog,
    ctx: asst.ChatContext,
    messages: list[dict[str, Any]],
) -> ChatReply:
    """
    Drive the tool loop. Read-only tools are executed here; mutating tools are
    collected for the client and acknowledged so the model can finish talking.
    """
    import anthropic

    actions: list[dict[str, Any]] = []
    total_in = 0
    total_out = 0
    reply_text = ""
    tools = asst.tool_definitions()

    for _turn in range(asst.MAX_TURNS):
        try:
            response = client.messages.create(
                model=settings.assistant_model,
                max_tokens=settings.assistant_max_tokens,
                system=asst.SYSTEM_PROMPT,
                tools=tools,
                messages=messages,
            )
        except anthropic.RateLimitError as exc:
            raise HTTPException(status_code=429, detail="Assistant is rate limited.") from exc
        except anthropic.AuthenticationError as exc:
            raise HTTPException(status_code=503, detail="Invalid ANTHROPIC_API_KEY.") from exc
        except anthropic.APIStatusError as exc:
            raise HTTPException(
                status_code=502, detail=f"Assistant error: {exc.message}"
            ) from exc
        except anthropic.APIConnectionError as exc:
            raise HTTPException(status_code=502, detail="Could not reach the assistant.") from exc

        total_in += response.usage.input_tokens
        total_out += response.usage.output_tokens

        text = asst.extract_text(response.content)
        if text:
            reply_text = text

        calls = asst.tool_uses(response.content)
        if not calls:
            break

        messages.append({"role": "assistant", "content": response.content})

        # All tool results for one assistant turn go back in ONE user message.
        results: list[dict[str, Any]] = []
        for call in calls:
            name = asst.block_field(call, "name")
            args = asst.block_field(call, "input") or {}
            call_id = asst.block_field(call, "id")

            if name in asst.ACTION_TOOLS:
                try:
                    asst.validate_action(name, args, catalog, ctx)
                except asst.ValidationFailure as exc:
                    results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": call_id,
                            "is_error": True,
                            "content": str(exc),
                        }
                    )
                    continue
                actions.append({"tool": name, "args": args})
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": call_id,
                        "content": asst.serialise_result(
                            {"applied": True, "note": "The viewer has applied this."}
                        ),
                    }
                )
            else:
                try:
                    payload = asst.run_read_tool(name, args, catalog, ctx)
                except asst.ValidationFailure as exc:
                    results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": call_id,
                            "is_error": True,
                            "content": str(exc),
                        }
                    )
                    continue
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": call_id,
                        "content": asst.serialise_result(payload),
                    }
                )

        messages.append({"role": "user", "content": results})

    if not reply_text:
        reply_text = (
            "Done." if actions else "I could not find anything in our catalog for that."
        )

    return ChatReply(
        reply=reply_text,
        actions=actions,
        chips=asst.suggest_chips(actions, catalog),
        usage={"input_tokens": total_in, "output_tokens": total_out},
        model=settings.assistant_model,
    )


@router.post("/chat", response_model=ChatReply)
def chat(
    body: ChatBody,
    settings: Settings = Depends(get_settings),
    catalog: Catalog = Depends(catalog_dep),
) -> ChatReply:
    _rate_limit(body.session_id, settings.assistant_rate_limit_per_hour)
    if not body.message.strip():
        raise HTTPException(status_code=400, detail="Empty message")

    client = _build_client(settings)
    ctx = asst.ChatContext(
        project_id=body.project_id,
        active_room_id=body.context.activeRoomId,
        rooms=body.context.rooms,
        applied_floors=body.context.appliedFloors,
        placed_items=body.context.placedItems,
        recolors=body.context.recolors,
        manifest_item_ids=body.context.manifestItemIds,
    )
    messages = asst.build_messages(body.history, body.message)
    return run_conversation(client, settings, catalog, ctx, messages)
