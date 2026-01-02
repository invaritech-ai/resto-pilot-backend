from __future__ import annotations

from collections.abc import Mapping

from app.ai.model_config import get_model_prices
from app.core.config import Settings


def estimate_cost_usd(*, settings: Settings, model: str, usage: Mapping[str, int]) -> float | None:
    prices = get_model_prices(settings)
    price = prices.get(model)
    if price is None:
        # Common OpenRouter pattern: "provider/model-YYYY-MM-DD"
        if len(model) >= 11:
            suffix = model[-10:]
            if (
                suffix[0:4].isdigit()
                and suffix[4] == "-"
                and suffix[5:7].isdigit()
                and suffix[7] == "-"
                and suffix[8:10].isdigit()
                and model[-11] == "-"
            ):
                price = prices.get(model[:-11])

    if not isinstance(price, dict):
        return None

    input_per_million = price.get("input_per_million")
    output_per_million = price.get("output_per_million")
    if not isinstance(input_per_million, (int, float)) or not isinstance(
        output_per_million, (int, float)
    ):
        return None

    prompt_tokens = usage.get("prompt_tokens")
    completion_tokens = usage.get("completion_tokens")
    if not isinstance(prompt_tokens, int) or not isinstance(completion_tokens, int):
        return None
    if prompt_tokens < 0 or completion_tokens < 0:
        return None

    cost = (prompt_tokens / 1_000_000.0) * float(input_per_million) + (
        completion_tokens / 1_000_000.0
    ) * float(output_per_million)
    return float(cost)

