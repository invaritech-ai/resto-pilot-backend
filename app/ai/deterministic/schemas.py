from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field


class PlannerClarify(BaseModel):
    action: Literal["clarify"]
    clarify_kind: str = Field(
        ...,
        description="What is missing/ambiguous (restaurant|supplier|file_kind|validation|...).",
    )
    question: str
    choices: list[dict[str, str]] | None = None


class PlannerCallTool(BaseModel):
    action: Literal["call_tool"]
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)


PlannerDecision = PlannerClarify | PlannerCallTool


@dataclass(frozen=True)
class PlannerTelemetry:
    model: str
    latency_ms: int
    generation_id: str | None
    usage: dict[str, Any]


class ToolCatalogEntry(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any]
    is_write: bool = False

