"""
Model configuration helpers.

Provides functions for getting model names for different purposes.
"""

from __future__ import annotations

from app.core.config import Settings


def _pick(settings: Settings, *fields: str, fallback: str) -> str:
    for field in fields:
        value = getattr(settings, field, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return fallback


def get_ack_model(settings: Settings) -> str:
    """Model for acknowledgments (cheap/fast)."""
    return _pick(
        settings,
        "ack_model",
        "openai_intent_model",
        "openai_gate_model",
        fallback=settings.openai_model,
    )


def get_planner_model(settings: Settings) -> str:
    """Model for planning (typically most capable)."""
    return _pick(
        settings,
        "planner_model",
        "openai_reasoning_model",
        fallback=settings.openai_model,
    )


def get_clarification_model(settings: Settings) -> str:
    """Model for clarification phrasing (optional)."""
    return _pick(
        settings,
        "clarification_model",
        fallback=settings.openai_model,
    )


def get_presenter_model(settings: Settings) -> str:
    """Model for final response composition (optional; can be a cheaper model)."""
    return _pick(
        settings,
        "presenter_model",
        "openai_response_model",
        fallback=settings.openai_model,
    )


def get_gate_model(settings: Settings) -> str:
    """Legacy alias for intent model selection (deprecated)."""
    return get_ack_model(settings)


def get_intent_model(settings: Settings) -> str:
    """Get the model for intent classification and resolution."""
    return get_ack_model(settings)


def get_file_type_model(settings: Settings) -> str:
    """Get the model for file type detection."""
    # Keep vision/file-type on text model unless you explicitly configure vision_*.
    return _pick(
        settings,
        "openai_file_type_model",
        "openai_intent_model",
        "openai_gate_model",
        fallback=settings.openai_model,
    )


def get_response_model(settings: Settings) -> str:
    """Get the model for final user-facing responses."""
    return get_presenter_model(settings)


def get_reasoning_model(settings: Settings) -> str:
    """Get the model for complex reasoning tasks."""
    return get_planner_model(settings)


def get_audio_model(settings: Settings) -> str:
    """Get the model for audio processing tasks."""
    return settings.openai_audio_model.strip() or settings.openai_model


def get_video_model(settings: Settings) -> str:
    """Get the model for video processing tasks."""
    return settings.openai_video_model.strip() or settings.openai_model


def get_item_search_parse_model(settings: Settings) -> str:
    """Get the model for item search parsing (cheap JSON-only parse step)."""
    return _pick(
        settings,
        "openai_item_search_parse_model",
        "openai_intent_model",
        "openai_gate_model",
        fallback=settings.openai_model,
    )
