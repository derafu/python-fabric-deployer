#!/usr/bin/env python3

"""
Fabric file for deploying Python projects.

This file defines a set of tasks that can be used to deploy Python projects
using Fabric. It includes functionality for deploying individual projects,

"""

import os

from fabric2 import Connection, task
from invoke.collection import Collection
from invoke.context import Context

from fabricator.deploy import deploy_site
from fabricator.logger import get_logger
from fabricator.recipes import release_lock, rollback_to_previous_release
from fabricator.runners import DockerRunner
from fabricator.utils import load_sites, print_site_list


def get_connection(
    fallback_connection: Connection | DockerRunner | Context,
    config: dict | None = None
) -> Connection | DockerRunner | Context:
    """
    Return a connection object based on environment variables or config.

    Priority:
    1. Environment variables: DEPLOYER_HOST, USER, PORT
    2. Docker runner if configured.
    3. Host/port from config.
    4. Fallback connection or local.

    :param fallback_connection: Local context or default connection
    :param config: Optional site configuration dictionary
    :return: Fabric Connection, DockerRunner, or Context
    """
    # 1. If environment variable is set, override all
    host = os.getenv("DEPLOYER_HOST")
    user = os.getenv("DEPLOYER_USER")
    port = os.getenv("DEPLOYER_PORT")
    if host:
        return Connection(
            host=host,
            user=user or None,
            port=int(port) if port else 22
        )

    # 2. If Docker runner is configured
    if config and config.get("runner") == "docker":
        container = config["docker_container"]
        docker_user = config.get("docker_user", "root")
        return DockerRunner(
            container_name=container,
            inner_runner=Context(),
            user=docker_user
        )

    # 3. If host is defined in config and not already remote
    if (
        config
        and "host" in config
        and getattr(fallback_connection, "host", "localhost") == "localhost"
    ):
        return Connection(
            host=config["host"],
            port=config.get("port", 22)
        )

    # 4. Fallback to local context if no remote host is detected
    if getattr(fallback_connection, "host", "localhost") == "localhost":
        return Context()

    # 5. Default fallback
    return fallback_connection

@task(help={"site": "Name of the site to deploy"})
def deploy(c: Connection | DockerRunner | Context, site: str) -> None:
    """
    Deploy a single site defined in the configuration file.

    :param c: Fabric connection object.
    :param site: Name of the site defined in sites.yml.
    """
    # Load all site definitions from the YAML config
    sites = load_sites()

    # Initialize logger for the given site
    logger = get_logger(site)

    # Abort if the requested site does not exist in config
    if site not in sites:
        logger.error(f"Site '{site}' not found in sites.yml")
        return

    # Load config and set the site name explicitly
    config = sites[site]
    config['name'] = site

    # Resolve connection using environment vars if present
    c = get_connection(c, config = config)

    deploy_site(c, config)

@task
def deploy_all(c: Connection | DockerRunner | Context) -> None:
    """
    Deploy all sites listed in sites.yml.

    :param c: Fabric connection object.
    """
    # Load all site definitions
    sites = load_sites()

    # Deploy each site iteratively
    for site, config in sites.items():
        config['name'] = site
        # Use remote connection if available from environment
        c = get_connection(c, config = config)
        logger = get_logger(site)
        logger.info(f"Deploying site: {site}")
        deploy_site(c, config)

@task
def list_sites(c: Connection | DockerRunner | Context) -> None:
    """
    Display all available site configurations.

    :param c: Fabric connection object.
    """
    # Print table of available site names
    print_site_list()

@task(help={"site": "Name of the site to rollback"})
def rollback(c: Connection | DockerRunner | Context, site: str) -> None:
    """
    Rollback a site to its previous release.

    :param c: Fabric connection object.
    :param site: Name of the site to rollback.
    """
    # Initialize logger
    logger = get_logger(site)

    # Load site definitions
    sites = load_sites()

    # Exit if the site doesn't exist
    if site not in sites:
        logger.error(f"Site '{site}' not found in sites.yml.")
        return

    # Extract config and set site name
    config = sites[site]
    config['name'] = site

    # Resolve connection using environment vars if present
    c = get_connection(c, config = config)
    # Log and execute rollback
    logger.info(f"Rolling back site: {site}")
    rollback_to_previous_release(c, config)

@task
def rollback_all(c: Connection | DockerRunner | Context) -> None:
    """
    Rollback all sites to their previous release.

    :param c: Fabric connection object.
    """
    # Load all site configs
    sites = load_sites()

    # Apply rollback to each one
    for site, config in sites.items():
        config['name'] = site
        # Resolve connection using environment vars if present
        c = get_connection(c, config = config)
        logger = get_logger(site)
        logger.info(f"Rolling back site: {site}")
        rollback_to_previous_release(c, config)

@task(help={"site": "Name of the site to unlock"})
def unlock(c: Connection | DockerRunner | Context, site: str) -> None:
    """
    Remove the lock file for a specific site.

    :param c: Fabric connection object.
    :param site: Name of the site to unlock.
    """
    # Initialize logger
    logger = get_logger(site)

    # Load available sites
    sites = load_sites()

    # Validate site presence
    if site not in sites:
        logger.error(f"Site '{site}' not found in sites.yml.")
        return

    # Set site name and perform unlock
    config = sites[site]
    config['name'] = site

    # Resolve connection using environment vars if present
    c = get_connection(c, config = config)

    logger.info(f"Unlocking site: {site}")
    release_lock(c, config, force=True)

@task
def unlock_all(c: Connection | DockerRunner | Context) -> None:
    """
    Unlock all sites by removing their lock files.

    :param c: Fabric connection object.
    """
    # Load site configurations
    sites = load_sites()

    # Unlock each one using force
    for site, config in sites.items():
        config['name'] = site
        # Resolve connection if needed
        c = get_connection(c, config = config)
        logger = get_logger(site)
        logger.info(f"Unlocking site: {site}")
        release_lock(c, config, force=True)

# This line exposes the tasks to the Fabric CLI (`fab2 ...`)
ns = Collection(
    deploy,
    deploy_all,
    list_sites,
    rollback,
    rollback_all,
    unlock,
    unlock_all
)
