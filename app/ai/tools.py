from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from typing import Any, Callable

from app.conversation import responses


def get_current_datetime() -> str:
    return dt.datetime.now(dt.UTC).isoformat()


def get_help_topic(args: dict[str, Any]) -> str:
    topic_raw = args.get("topic")
    topic = topic_raw.strip().lower() if isinstance(topic_raw, str) else None
    return responses.help_topic(topic)


def get_menu_paths() -> str:
    paths = [
        {
            "section": "Profile",
            "description": "View or update your name/phone.",
            "examples": ["show my profile", "update my phone to +1 415 555 0101"],
        },
        {
            "section": "Outlets",
            "description": "Manage restaurants.",
            "examples": ["list outlets", "add outlet Mercato"],
        },
        {
            "section": "Staff",
            "description": "View or invite team members.",
            "examples": ["list staff", "invite staff to Mercato as staff"],
        },
        {
            "section": "Suppliers",
            "description": "Manage vendors and price lists.",
            "examples": [
                "list suppliers",
                "show unlinked suppliers",
                "show unlinked suppliers for outlet Mercato",
                "view supplier price list Fresh Farms",
                "link supplier Fresh Farms to outlet Mercato",
            ],
        },
        {
            "section": "Invites",
            "description": "List or move invite links.",
            "examples": ["list invites", "move invite ABC123 to Mercato"],
        },
        {
            "section": "Invoices",
            "description": "View past invoices.",
            "examples": ["list invoices", "view invoice <invoice_id>"],
        },
        {
            "section": "Inventory",
            "description": "Track stock, locations, and usage.",
            "examples": ["list inventory", "list locations"],
        },
        {
            "section": "Files",
            "description": "Upload price lists or invoices.",
            "examples": ["upload price list for Fresh Farms", "upload invoice for Mercato"],
        },
    ]
    return json.dumps(paths, indent=2)


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[dict[str, Any]], str]


def _no_args(handler: Callable[[], str]) -> Callable[[dict[str, Any]], str]:
    def _wrapped(_args: dict[str, Any]) -> str:
        return handler()

    return _wrapped


TOOLS: dict[str, Tool] = {
    "get_current_datetime": Tool(
        name="get_current_datetime",
        description="Get the current UTC date and time in ISO-8601 format.",
        parameters={"type": "object", "properties": {}, "additionalProperties": False},
        handler=_no_args(get_current_datetime),
    )
    ,
    "get_help_topic": Tool(
        name="get_help_topic",
        description="Get help text for a specific topic (profile, outlets, staff, suppliers, invoices, inventory, files, invites) or the main menu if topic is empty.",
        parameters={
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": "Optional help topic name.",
                }
            },
            "additionalProperties": False,
        },
        handler=get_help_topic,
    ),
    "get_menu_paths": Tool(
        name="get_menu_paths",
        description="List the available bot paths with short descriptions and example questions.",
        parameters={"type": "object", "properties": {}, "additionalProperties": False},
        handler=_no_args(get_menu_paths),
    ),
}


def openai_tools_schema() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            },
        }
        for tool in TOOLS.values()
    ]


def tools_to_openai_schema(tools: dict[str, Tool]) -> list[dict[str, Any]]:
    """
    Convert a dictionary of tools to OpenAI function calling schema.

    Args:
        tools: Dictionary mapping tool names to Tool objects

    Returns:
        List of tool definitions in OpenAI format
    """
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            },
        }
        for tool in tools.values()
    ]
