from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, Callable


def get_current_datetime() -> str:
    return dt.datetime.now(dt.UTC).isoformat()


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

