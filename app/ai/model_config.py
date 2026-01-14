"""
Model configuration helpers.

Provides functions for getting model names for different purposes.
"""

from __future__ import annotations

from app.core.config import Settings


def _pick_model(settings: Settings, *fields: str, fallback: str) -> str:
    for field in fields:
        value = getattr(settings, field, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return fallback


def get_gate_model(settings: Settings) -> str:
    """Legacy alias for intent model selection."""
    return get_intent_model(settings)


def get_intent_model(settings: Settings) -> str:
    """Get the model for intent classification and resolution."""
    return _pick_model(
        settings,
        "openai_intent_model",
        "openai_gate_model",
        fallback=settings.openai_model,
    )


def get_file_type_model(settings: Settings) -> str:
    """Get the model for file type detection."""
    return _pick_model(
        settings,
        "openai_file_type_model",
        "openai_intent_model",
        "openai_gate_model",
        fallback=settings.openai_model,
    )


def get_response_model(settings: Settings) -> str:
    """Get the model for final user-facing responses."""
    return _pick_model(settings, "openai_response_model", fallback=settings.openai_model)


def get_reasoning_model(settings: Settings) -> str:
    """Get the model for complex reasoning tasks."""
    return _pick_model(settings, "openai_reasoning_model", fallback=settings.openai_model)


def get_audio_model(settings: Settings) -> str:
    """Get the model for audio processing tasks."""
    return _pick_model(settings, "openai_audio_model", fallback=settings.openai_model)


def get_video_model(settings: Settings) -> str:
    """Get the model for video processing tasks."""
    return _pick_model(settings, "openai_video_model", fallback=settings.openai_model)
