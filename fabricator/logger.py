"""
Module for logging deployment operations.

This module provides a reusable logger that can be used to log messages
from any module. It ensures that handlers are added only once to avoid
duplicate logs.

"""
import logging


def get_logger(name: str = "deploy") -> logging.Logger:
    """
    Get or create a reusable console-based logger by name.

    This function returns a configured `logging.Logger` instance with
    an attached console handler. It prevents duplication of handlers
    if the logger is requested multiple times.

    The logger outputs messages in the format:
    `[timestamp] [LEVEL] message`.

    :param name: Name to identify the logger instance. Typically used
                to separate logs by context or project.
    :type name: str

    :return: A configured `Logger` object ready for use.
    :rtype: logging.Logger
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
