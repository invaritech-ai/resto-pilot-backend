import logging

from app.core.config import Settings


def configure_logging(settings: Settings) -> None:
    """Basic structured logging setup; expand with JSON/OTEL as needed."""
    level = logging.DEBUG if settings.debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
