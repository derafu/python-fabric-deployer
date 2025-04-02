"""
Module for logging deployment operations.

This module provides a reusable logger that can be used to log messages
from any module. It ensures that handlers are added only once to avoid
duplicate logs.

"""
import logging


def get_logger(name: str = "deploy") -> logging.Logger:
    """
    Return a reusable logger configured to output to the console.

    Ensures that handlers are added only once to avoid duplicate logs.

    :param name: Name to identify the logger (e.g., the site name).
    :return: Configured Logger instance.
    """
    logger = logging.getLogger(name)

    # Avoid adding multiple handlers on repeated calls
    if logger.hasHandlers():
        return logger

    logger.setLevel(logging.INFO)

    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    # Console handler only
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    return logger
