#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys

from app.ai.deterministic.planner import plan_next_action
from app.ai.deterministic.tool_catalog import tool_catalog_as_planner_json
from app.core.config import Settings


def _maybe_json(value: str | None) -> dict | list | None:
    if not value:
        return None
    return json.loads(value)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the deterministic PlannerLLM locally and print its decision.")
    parser.add_argument("--text", required=True, help="User message text.")
    parser.add_argument(
        "--context-json",
        default="{}",
        help="Context JSON to pass to planner (active outlet/supplier, pending_selection, etc.).",
    )
    parser.add_argument(
        "--recent-turns-json",
        default="[]",
        help="Recent turns JSON array, e.g. [{'role':'user','content':'hi'},{'role':'assistant','content':'...'}].",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Use live LLM (requires APP_OPENAI_API_KEY). If omitted, prints a warning but still attempts live call if key is set.",
    )
    args = parser.parse_args()

    settings = Settings()
    if not settings.openai_api_key:
        if args.live:
            print("Error: APP_OPENAI_API_KEY is not set.", file=sys.stderr)
            return 2
        print("Warning: APP_OPENAI_API_KEY not set; planner call will fail.", file=sys.stderr)

    context = _maybe_json(args.context_json) or {}
    recent_turns = _maybe_json(args.recent_turns_json) or []

    decision, telemetry = plan_next_action(
        settings=settings,
        message_text=args.text,
        recent_turns=recent_turns if isinstance(recent_turns, list) else [],
        context=context if isinstance(context, dict) else {},
        available_tools=tool_catalog_as_planner_json(),
    )

    print(json.dumps({"decision": decision.model_dump(), "telemetry": telemetry.__dict__ if telemetry else None}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
