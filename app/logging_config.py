import logging
import sys

from app.config import settings

logger = logging.getLogger("mealieah")


def setup_logging(level: str | None = None) -> None:
    log_level = level or settings.log_level
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))
    logger.info("Logging initialized at %s level", log_level.upper())


def set_log_level(level: str) -> None:
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.info("Log level changed to %s", level.upper())
