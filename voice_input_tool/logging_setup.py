"""Idempotent application logging setup."""

import logging
import os

from voice_input_tool.app_paths import LOG_DIR

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
ERROR_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


def configure_logging():
    os.makedirs(LOG_DIR, exist_ok=True)
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    main_path = os.path.join(LOG_DIR, "voice-input.log")
    error_path = os.path.join(LOG_DIR, "voice-input-error.log")
    _ensure_file_handler(root_logger, main_path, logging.INFO, LOG_FORMAT)
    _ensure_file_handler(root_logger, error_path, logging.ERROR, ERROR_LOG_FORMAT)


def _ensure_file_handler(logger, path, level, formatter):
    absolute_path = os.path.abspath(path)
    for handler in logger.handlers:
        if isinstance(handler, logging.FileHandler) and handler.baseFilename == absolute_path:
            return

    handler = logging.FileHandler(absolute_path, encoding="utf-8")
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(formatter))
    logger.addHandler(handler)
