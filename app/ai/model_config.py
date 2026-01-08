"""
Model configuration helpers.

Provides functions for getting model names for different purposes.
"""

from __future__ import annotations

from app.core.config import Settings


def get_gate_model(settings: Settings) -> str:
    """Get the model for intent classification and file type detection."""
    model = getattr(settings, "openai_gate_model", None)
    if isinstance(model, str) and model.strip():
        return model.strip()
    return settings.openai_model
