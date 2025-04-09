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
    Load the sites configuration from a YAML file.

    Parses the YAML configuration defined in the constant ``SITES_FILE``
    and returns a dictionary with deployment data for each site.

    :return: Dictionary containing all configured sites.
    :rtype: dict
    """
    # Open and parse the YAML file, returning a dictionary
    with open(SITES_FILE) as f:
        return yaml.safe_load(f)

def print_site_list() -> None:
    """
    Print a list of configured sites and their repository URLs.

    Loads the site configurations from the YAML file and logs each
    site's name along with its associated Git repository URL.

    :return: This function returns nothing.
    :rtype: None
    """
    # Load site configurations from the YAML file
    sites = load_sites()

    # Output a header line for context
    logger.info("Available sites:")

    # Iterate over each site and log its repository URL
    for site, cfg in sites.items():
        logger.info(f" - {site}: {cfg['repository']}")
