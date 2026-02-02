from __future__ import annotations

from app.ai.deterministic.schemas import PlannerCallTool, PlannerClarify
from app.ai.deterministic.tool_catalog import tool_catalog_as_planner_json
from app.ai.deterministic.validation import validate_planner_decision


def test_validation_accepts_known_tool_with_valid_args() -> None:
    decision = PlannerCallTool(action="call_tool", tool="restaurants_list", args={})
    result = validate_planner_decision(decision=decision, tool_catalog=tool_catalog_as_planner_json())
    assert isinstance(result.decision, PlannerCallTool)
    assert result.errors == []


def test_validation_rejects_unknown_tool() -> None:
    decision = PlannerCallTool(action="call_tool", tool="does_not_exist", args={})
    result = validate_planner_decision(decision=decision, tool_catalog=tool_catalog_as_planner_json())
    assert isinstance(result.decision, PlannerClarify)
    assert any("Unknown tool" in e for e in result.errors)


def test_validation_rejects_missing_required_args() -> None:
    decision = PlannerCallTool(action="call_tool", tool="restaurants_create", args={})
    result = validate_planner_decision(decision=decision, tool_catalog=tool_catalog_as_planner_json())
    assert isinstance(result.decision, PlannerClarify)
    assert any("Missing required arg: name" in e for e in result.errors)


def test_validation_rejects_unexpected_args_when_additional_properties_false() -> None:
    decision = PlannerCallTool(action="call_tool", tool="restaurants_list", args={"foo": "bar"})
    result = validate_planner_decision(decision=decision, tool_catalog=tool_catalog_as_planner_json())
    assert isinstance(result.decision, PlannerClarify)
    assert any("Unexpected arg: foo" in e for e in result.errors)


def test_validation_rejects_enum_mismatch() -> None:
    decision = PlannerCallTool(action="call_tool", tool="invite_codes_create", args={"role": "admin"})
    result = validate_planner_decision(decision=decision, tool_catalog=tool_catalog_as_planner_json())
    assert isinstance(result.decision, PlannerClarify)
    assert any("Invalid value for role" in e for e in result.errors)


def test_validation_rejects_uuid_like_args() -> None:
    decision = PlannerCallTool(
        action="call_tool",
        tool="restaurants_update",
        args={"restaurant_query": "550e8400-e29b-41d4-a716-446655440000", "name": "X"},
    )
    result = validate_planner_decision(decision=decision, tool_catalog=tool_catalog_as_planner_json())
    assert isinstance(result.decision, PlannerClarify)
    assert any("UUID" in e for e in result.errors)

