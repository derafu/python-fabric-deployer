#!/usr/bin/env python3
"""
Add a new site to the sites configuration file.

This script allows you to add a new site to the sites configuration file.

"""
import sys
from pathlib import Path

import yaml

# Define the path to the main sites configuration file
SITES_FILE = Path(__file__).resolve().parent / "sites.yml"


def load_sites():
    """
    Load all site configurations from the YAML file.

    If the configuration file does not exist, an empty dictionary
    is returned. Otherwise, parses and returns the YAML contents.

    :return: Dictionary mapping site domains to their configurations.
    :rtype: dict
    """
    # Return an empty dictionary if the file doesn't exist
    if not SITES_FILE.exists():
        return {}

    # Open and load the YAML file
    with open(SITES_FILE) as f:
        return yaml.safe_load(f) or {}

def save_sites(sites):
    """
    Save all site configurations to the YAML file.

    Overwrites the existing file content with the provided dictionary.
    Preserves key order and disables flow-style formatting for readability.

    :param sites: Dictionary containing all site configurations.
    :type sites: dict
    """
    # Open the YAML file in write mode and dump contents
    with open(SITES_FILE, "w") as f:
        yaml.dump(
            sites,
            f,
            default_flow_style=False,
            sort_keys=False
        )

def add_site(domain, repo_url):
    """
    Add a new site configuration entry to the YAML file.

    Creates a new site entry based on the domain and repository URL.
    Sets sensible defaults for deployment path, branch, runner, and
    virtual environment folder. Prevents overwriting existing sites.

    :param domain: Full domain name of the new site
                (e.g., ``app.example.com``).
    :type domain: str

    :param repo_url: Git repository URL for the new site.
    :type repo_url: str
    """
    # Load existing configurations
    sites = load_sites()

    # Prevent overwriting an existing entry
    if domain in sites:
        print(f"The configuration for {domain} already exists.")
        return

    # Build the basic config dictionary
    sites[domain] = {
        "repository": repo_url,
        "deploy_path": f"/srv/docker/python3.13-caddy/sites/{domain}",
        "branch": "main",
        "venv": "venv",
        "runner": "local"
    }

    # Save the updated configuration
    save_sites(sites)

    # Confirm success to user
    print(f"The configuration for {domain} has been added.")

# Handle command-line execution
if __name__ == "__main__":
    # Ensure exactly two arguments are passed (domain and repo)
    LIMIT_ARGS = 2
    if len(sys.argv) != LIMIT_ARGS:
        print("Usage: ./siteadd.py \"app.example.com\" "
              "\"git@github.com:example/www.example.com.git\"")
        sys.exit(1)

    # Parse arguments
    domain = sys.argv[1]
    repo_url = sys.argv[2]

    # Call the site add function
    add_site(domain, repo_url)
