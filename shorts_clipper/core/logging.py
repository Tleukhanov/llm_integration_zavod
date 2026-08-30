"""Project logging configuration."""

from __future__ import annotations

import logging
from pathlib import Path


def configure_logging(level: str = "INFO") -> None:
    """Configure system-wide logging to output to both console and outputs/app.log."""
    log_level = getattr(logging, level.upper(), logging.INFO)

    # Ensure log output directory exists
    log_dir = Path("outputs")
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "app.log"

    logging.getLogger("googleapiclient.discovery_cache").setLevel(logging.ERROR)
    logging.getLogger("googleapiclient").setLevel(logging.WARNING)

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Clear any existing handlers
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)

    formatter = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")

    # Console Stream Handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # Persistent File Handler
    file_handler = logging.FileHandler(str(log_file), encoding="utf-8")
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
