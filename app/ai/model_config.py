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
    return _pick(settings, "ack_model", fallback=settings.openai_model)


def get_planner_model(settings: Settings) -> str:
    """Model for planning (intent classification & tool selection)."""
    return _pick(settings, "planner_model", fallback=settings.openai_model)


def get_clarification_model(settings: Settings) -> str:
    """Model for clarification phrasing (optional)."""
    return _pick(settings, "clarification_model", fallback=settings.openai_model)


def get_presenter_model(settings: Settings) -> str:
    """Model for final response composition."""
    return _pick(settings, "presenter_model", fallback=settings.openai_model)


def get_file_type_model(settings: Settings) -> str:
    """Get the model for file type detection."""
    return _pick(settings, "openai_file_type_model", fallback=settings.openai_model)


def get_audio_model(settings: Settings) -> str:
    """Get the model for audio processing tasks."""
    return _pick(settings, "openai_audio_model", fallback=settings.openai_model)


def get_video_model(settings: Settings) -> str:
    """Get the model for video processing tasks."""
    return _pick(settings, "openai_video_model", fallback=settings.openai_model)
