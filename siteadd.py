#!/usr/bin/env python3

import sys
import yaml
from pathlib import Path

# Define the path to the main sites configuration file
SITES_FILE = Path(__file__).resolve().parent / "sites.yml"


def load_sites():
    """
    Load site configurations from the YAML file.

    :return: Dictionary containing all site configurations.
    """
    # Return an empty dictionary if the file doesn't exist
    if not SITES_FILE.exists():
        return {}

    # Open and load the YAML file
    with open(SITES_FILE, "r") as f:
        return yaml.safe_load(f) or {}


def save_sites(sites):
    """
    Save the updated site configurations to the YAML file.

    :param sites: Dictionary of all site configurations.
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
    Add a new site entry to the sites configuration file.

    :param domain: Domain name of the new site (e.g., www.example.com).
    :param repo_url: Git repository URL for the site.
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
        "deploy_path": f"/var/www/sites/{domain}",
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
    if len(sys.argv) != 3:
        print("Usage: ./siteadd.py \"www.example.com\" "
              "\"git@github.com:example/www.example.com.git\"")
        sys.exit(1)

    # Parse arguments
    domain = sys.argv[1]
    repo_url = sys.argv[2]

    # Call the site add function
    add_site(domain, repo_url)
