from __future__ import annotations

import json
from typing import Any

from app.core.config import Settings


def get_model_prices(settings: Settings) -> dict[str, dict[str, float]]:
    """
    Returns mapping: model -> {"input_per_million": float, "output_per_million": float}

    Reads from APP_MODEL_PRICES_JSON (a JSON object).
    """
    raw = getattr(settings, "model_prices_json", "")
    if not isinstance(raw, str) or not raw.strip():
        return {}

    try:
        parsed: Any = json.loads(raw)
    except json.JSONDecodeError:
        return {}

    if not isinstance(parsed, dict):
        return {}

    out: dict[str, dict[str, float]] = {}
    for model, cfg in parsed.items():
        if not isinstance(model, str) or not isinstance(cfg, dict):
            continue
        inp = cfg.get("input_per_million")
        outp = cfg.get("output_per_million")
        if not isinstance(inp, (int, float)) or not isinstance(outp, (int, float)):
            continue
        if inp < 0 or outp < 0:
            continue
        out[model] = {"input_per_million": float(inp), "output_per_million": float(outp)}
    return out


def get_ack_model(settings: Settings) -> str:
    model = getattr(settings, "openai_ack_model", None)
    if isinstance(model, str) and model.strip():
        return model.strip()
    return settings.openai_model


def get_gate_model(settings: Settings) -> str:
    model = getattr(settings, "openai_gate_model", None)
    if isinstance(model, str) and model.strip():
        return model.strip()
    return settings.openai_model
