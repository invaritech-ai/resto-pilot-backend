from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.ai.deterministic.schemas import PlannerCallTool, PlannerClarify, PlannerDecision

_UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)


@dataclass(frozen=True)
class DecisionValidation:
    decision: PlannerDecision
    errors: list[str]


def _contains_uuid_like(value: Any) -> bool:
    if isinstance(value, str):
        return _UUID_RE.search(value) is not None
    if isinstance(value, dict):
        return any(_contains_uuid_like(v) for v in value.values())
    if isinstance(value, list):
        return any(_contains_uuid_like(v) for v in value)
    return False


def _is_instance_of_jsonschema_type(value: Any, type_name: str) -> bool:
    if type_name == "string":
        return isinstance(value, str)
    if type_name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if type_name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if type_name == "boolean":
        return isinstance(value, bool)
    if type_name == "array":
        return isinstance(value, list)
    if type_name == "object":
        return isinstance(value, dict)
    return True


def _validate_args_against_schema(args: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not isinstance(schema, dict):
        return ["Tool schema is not a dict."]
    if schema.get("type") != "object":
        return ["Tool schema type must be 'object'."]

    props = schema.get("properties") or {}
    required = schema.get("required") or []
    additional = schema.get("additionalProperties", True)

    if not isinstance(props, dict):
        return ["Tool schema properties must be an object."]

    for key in required:
        if key not in args:
            errors.append(f"Missing required arg: {key}")

    if additional is False:
        allowed = set(props.keys())
        for key in args.keys():
            if key not in allowed:
                errors.append(f"Unexpected arg: {key}")

    for key, value in args.items():
        prop = props.get(key)
        if not isinstance(prop, dict):
            continue
        type_name = prop.get("type")
        if isinstance(type_name, str) and not _is_instance_of_jsonschema_type(value, type_name):
            errors.append(f"Invalid type for {key}: expected {type_name}")
        enum = prop.get("enum")
        if isinstance(enum, list) and value not in enum:
            errors.append(f"Invalid value for {key}: must be one of {enum}")

    return errors


def validate_planner_decision(
    *,
    decision: PlannerDecision,
    tool_catalog: list[dict[str, Any]],
    no_ids: bool = True,
) -> DecisionValidation:
    """
    Deterministic validation gate for planner output.

    If invalid, returns a clarify decision with a generic question and includes errors for telemetry.
    """
    if isinstance(decision, PlannerClarify):
        return DecisionValidation(decision=decision, errors=[])

    if not isinstance(decision, PlannerCallTool):
        return DecisionValidation(
            decision=PlannerClarify(
                action="clarify",
                clarify_kind="validation",
                question="I couldn't figure out the exact action from that.",
                choices=None,
            ),
            errors=["Planner decision type is not recognized."],
        )

    errors: list[str] = []
    tool_index = {t.get("name"): t for t in tool_catalog if isinstance(t, dict)}
    spec = tool_index.get(decision.tool)
    if spec is None:
        errors.append(f"Unknown tool: {decision.tool}")
    else:
        params = spec.get("parameters")
        if not isinstance(decision.args, dict):
            errors.append("Tool args must be an object.")
        else:
            if isinstance(params, dict):
                errors.extend(_validate_args_against_schema(decision.args, params))
            else:
                errors.append("Tool parameters schema missing/invalid.")

    if no_ids and _contains_uuid_like(decision.args):
        errors.append("Args appear to contain an internal ID/UUID.")

    if errors:
        return DecisionValidation(
            decision=PlannerClarify(
                action="clarify",
                clarify_kind="validation",
                question="I couldn't figure out the exact action from that.",
                choices=None,
            ),
            errors=errors,
        )

    return DecisionValidation(decision=decision, errors=[])
