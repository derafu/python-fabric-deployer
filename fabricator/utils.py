"""
Module for utility functions.

This module provides utility functions for the fabricator project.

"""
from pathlib import Path

import yaml

from fabricator.logger import get_logger

# Create a logger for this module
logger = get_logger("utils")

# Path to the shared sites.yml configuration file
SITES_FILE = Path(__file__).resolve().parent.parent / "sites.yml"

def load_sites() -> dict:
    """
    Load the sites configuration from the YAML file.

    :return: A dictionary of site configurations.
    """
    # Open and parse the YAML file, returning a dictionary
    with open(SITES_FILE) as f:
        return yaml.safe_load(f)

def print_site_list() -> None:
    """
    Log all configured sites and their repository URLs.

    :return: None
    """
    # Load site configurations from the YAML file
    sites = load_sites()

    # Output a header line for context
    logger.info("Available sites:")

    # Iterate over each site and log its repository URL
    for site, cfg in sites.items():
        logger.info(f" - {site}: {cfg['repository']}")
