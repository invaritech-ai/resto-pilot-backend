from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator


class DBAction(BaseModel):
    action_id: str = Field(min_length=1)
    intent: str = Field(min_length=1)
    crud: str = Field(pattern="^(read|create|update|delete)$")
    table: str = Field(pattern="^(users|restaurants|restaurant_users|invite_codes)$")
    role: str = Field(pattern="^(owner|staff)$")
    scope: str = Field(pattern="^(self|owned_restaurant|restaurant_owner)$")

    filters: dict[str, Any] | None = None
    values: dict[str, Any] | None = None
    columns: list[str] | None = None

    needs_confirmation: bool = False
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    errors: list[str] = Field(default_factory=list)

    @field_validator("columns", mode="before")
    @classmethod
    def _normalize_columns(cls, value):  # type: ignore[no-untyped-def]
        if value is None:
            return None
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return value

    @field_validator("filters", "values", mode="before")
    @classmethod
    def _normalize_mapping(cls, value):  # type: ignore[no-untyped-def]
        if value is None:
            return None
        if isinstance(value, dict):
            return {str(k).strip(): v for k, v in value.items() if str(k).strip()}
        return value


def parse_db_action(payload: object) -> tuple[DBAction | None, list[str]]:
    if not isinstance(payload, dict):
        return None, ["payload must be an object"]
    try:
        return DBAction.model_validate(payload), []
    except ValidationError as exc:
        errors = [f"{err['loc']}: {err['msg']}" for err in exc.errors()]
        return None, errors
