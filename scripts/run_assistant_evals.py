"""
Run the assistant eval set and report the pass rate (M5).

The brief's bar is 85%. Each case asserts on the *tool calls* the assistant
produces, not on its prose, because the tool calls are what change the
viewer.

Needs ANTHROPIC_API_KEY. Costs real money: 32 prompts, a few turns each.
Run with --dry-run to validate the eval file and print the plan without
calling the API.

Usage:
    pnpm evals:assistant
    uv run python scripts/run_assistant_evals.py --dry-run
    uv run python scripts/run_assistant_evals.py --model claude-opus-5
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "services" / "api" / "src"))
sys.path.insert(0, str(REPO / "pipeline" / "src"))

from walkthrough_api import assistant as asst  # noqa: E402
from walkthrough_api.catalog import load_catalog  # noqa: E402
from walkthrough_api.config import get_settings  # noqa: E402

EVALS = REPO / "docs" / "assistant-evals.jsonl"

ROOMS = [
    {"id": "living", "name": "Living room", "type": "living"},
    {"id": "bedroom", "name": "Bedroom", "type": "bedroom"},
]
MANIFEST_ITEMS = ["sofa-1", "table-1", "bed-1"]


def load_cases() -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in EVALS.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def is_dark(hex_colour: str) -> bool:
    try:
        r = int(hex_colour[1:3], 16)
        g = int(hex_colour[3:5], 16)
        b = int(hex_colour[5:7], 16)
    except (ValueError, IndexError):
        return False
    return (0.299 * r + 0.587 * g + 0.114 * b) < 110


def check(case: dict[str, Any], actions: list[dict[str, Any]], reply: str) -> tuple[bool, str]:
    """Grade one case. Returns (passed, reason)."""
    expect = case["expect"]
    tools = [a["tool"] for a in actions]

    if expect.get("must_refuse"):
        if actions:
            return False, f"should have refused but called {tools}"
        if not reply.strip():
            return False, "refused silently with no explanation"
        return True, "refused and explained"

    wanted_tool = expect.get("tool")
    any_of = expect["args"].get("any_of_tools")
    if wanted_tool and wanted_tool not in tools:
        return False, f"expected {wanted_tool}, got {tools or 'no tool call'}"
    if any_of and not any(t in tools for t in any_of):
        return False, f"expected one of {any_of}, got {tools or 'no tool call'}"
    if not wanted_tool and not any_of and not actions:
        return False, "no tool call at all"

    args_spec = expect["args"]
    merged: dict[str, Any] = {}
    for action in actions:
        merged.update(action["args"])

    if "floorId_in" in args_spec:
        got = merged.get("floorId")
        if got not in args_spec["floorId_in"]:
            return False, f"floorId {got!r} not in {args_spec['floorId_in']}"
    if "styleId_in" in args_spec:
        got = merged.get("styleId")
        if got not in args_spec["styleId_in"]:
            return False, f"styleId {got!r} not in {args_spec['styleId_in']}"
    if "itemId_in" in args_spec:
        got = merged.get("itemId")
        if got not in args_spec["itemId_in"]:
            return False, f"itemId {got!r} not in {args_spec['itemId_in']}"
    if "roomIds_contains" in args_spec:
        got = merged.get("roomIds", [])
        if args_spec["roomIds_contains"] not in got:
            return False, f"roomIds {got} missing {args_spec['roomIds_contains']!r}"
    if "roomId_in" in args_spec:
        got = merged.get("roomId")
        if got not in args_spec["roomId_in"]:
            return False, f"roomId {got!r} not in {args_spec['roomId_in']}"
    if args_spec.get("colorHex_dark") and not is_dark(str(merged.get("colorHex", ""))):
        return False, f"colour {merged.get('colorHex')!r} is not dark"
    if "itemId_category" in args_spec:
        catalog = load_catalog(get_settings().catalog_root)
        item = catalog.by_id("furniture", str(merged.get("itemId", "")))
        if not item or item.get("category") != args_spec["itemId_category"]:
            return False, (
                f"itemId {merged.get('itemId')!r} is not a "
                f"{args_spec['itemId_category']}"
            )

    return True, "ok"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--model", default=None)
    parser.add_argument("--only", default=None, help="Filter by kind: normal|vague|impossible")
    args = parser.parse_args()

    cases = load_cases()
    if args.only:
        cases = [c for c in cases if c["kind"] == args.only]

    settings = get_settings()
    model = args.model or settings.assistant_model
    catalog = load_catalog(settings.catalog_root)

    print(f"{len(cases)} cases · model {model}")
    print(
        f"catalog: {len(catalog.floors)} floors, {len(catalog.furniture)} furniture, "
        f"{len(catalog.styles)} styles"
    )

    if not catalog.floors:
        print("\nNo catalog on disk. Run `pnpm seed:floors` and `pnpm seed:assets` first.")
        return 2

    if args.dry_run:
        print("\nDry run — no API calls. Case breakdown:")
        from collections import Counter

        for kind, n in Counter(c["kind"] for c in cases).items():
            print(f"  {kind:<11} {n}")
        print("\nSet ANTHROPIC_API_KEY and drop --dry-run to measure the pass rate.")
        return 0

    if not settings.anthropic_api_key:
        print("\nANTHROPIC_API_KEY is not set. Add it to .env (see .env.example).")
        return 2

    import anthropic
    from walkthrough_api.routes_chat import run_conversation

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    class EvalSettings:
        assistant_model = model
        assistant_max_tokens = settings.assistant_max_tokens

    passed = 0
    failures: list[tuple[str, str, str]] = []
    total_in = total_out = 0
    started = time.time()

    for case in cases:
        ctx = asst.ChatContext(
            project_id="demo-01",
            active_room_id=case["activeRoomId"],
            rooms=ROOMS,
            manifest_item_ids=MANIFEST_ITEMS,
        )
        messages = [{"role": "user", "content": case["prompt"]}]
        try:
            reply = run_conversation(client, EvalSettings(), catalog, ctx, messages)  # type: ignore[arg-type]
        except Exception as exc:  # noqa: BLE001 - one bad case must not stop the run
            failures.append((case["id"], case["prompt"], f"error: {exc}"))
            print(f"  {case['id']} ERROR {exc}")
            continue

        total_in += reply.usage["input_tokens"]
        total_out += reply.usage["output_tokens"]
        ok, reason = check(case, reply.actions, reply.reply)
        if ok:
            passed += 1
            print(f"  {case['id']} pass  {case['prompt'][:46]}")
        else:
            failures.append((case["id"], case["prompt"], reason))
            print(f"  {case['id']} FAIL  {case['prompt'][:46]} — {reason}")

    rate = passed / len(cases) if cases else 0.0
    elapsed = time.time() - started

    print(f"\nPass rate {rate:.0%} ({passed}/{len(cases)}) in {elapsed:.0f}s")
    print(f"Tokens: {total_in:,} in, {total_out:,} out")

    if failures:
        print("\nFailures:")
        for case_id, prompt, reason in failures:
            print(f"  {case_id} {prompt}\n      {reason}")

    bar = 0.85
    if rate >= bar:
        print(f"\nMeets the brief's {bar:.0%} bar.")
        return 0
    print(
        f"\nBelow the brief's {bar:.0%} bar. Work the prompt and tool schemas first; "
        "if it still misses, switch ASSISTANT_MODEL to claude-opus-5 "
        "(docs/SPEC.md §2) and record both numbers."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
