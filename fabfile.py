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
from fabricator.recipes import (
    release_lock,
    restart_services,
    rollback_to_previous_release,
)
from fabricator.runners import DockerRunner
from fabricator.utils import load_sites, print_site_list


def get_connection(
    fallback_connection: Connection | DockerRunner | Context,
    config: dict | None = None
) -> Connection | DockerRunner | Context:
    """
    Resolve the appropriate connection object for the site.

    Selects the correct runner based on environment variables,
    Docker settings, or host definitions. Priority order:

    1. Environment variables: ``DEPLOYER_HOST``, ``DEPLOYER_USER``,
    ``DEPLOYER_PORT``
    2. Docker runner (if ``runner: docker`` is set in config)
    3. SSH host/port defined in config
    4. Fallback connection
    5. Default to local context

    :param fallback_connection: Default local or remote runner.
    :type fallback_connection: Union[Connection, DockerRunner, Context]

    :param config: Optional site config loaded from YAML.
    :type config: dict or None

    :return: A connection object suitable for deployment.
    :rtype: Union[Connection, DockerRunner, Context]
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

    Loads site-specific configuration, resolves the proper connection
    (local, Docker, or SSH), and runs the full deployment process.

    :param c: Fabric connection or context object.
    :type c: Union[Connection, DockerRunner, Context]

    :param site: Name of the site as defined in ``sites.yml``.
    :type site: str
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
    Deploy all sites listed in the configuration file.

    Iterates through all entries in ``sites.yml`` and runs the
    deployment pipeline for each one sequentially.

    :param c: Fabric connection or context object.
    :type c: Union[Connection, DockerRunner, Context]
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
    Display all configured sites and their repository URLs.

    Loads site data from ``sites.yml`` and prints the site name
    alongside its associated Git repository.

    :param c: Fabric connection or context object.
    :type c: Union[Connection, DockerRunner, Context]
    """
    # Print table of available site names
    print_site_list()

@task(help={"site": "Name of the site to rollback"})
def rollback(c: Connection | DockerRunner | Context, site: str) -> None:
    """
    Rollback a site to the previous release.

    Identifies the most recent backup release and reverts to it.

    :param c: Fabric connection or context object.
    :type c: Union[Connection, DockerRunner, Context]

    :param site: Name of the site to rollback.
    :type site: str
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
    Rollback all sites to their previous releases.

    Iterates through all configured sites and performs a rollback
    to their last known good deployment.

    :param c: Fabric connection or context object.
    :type c: Union[Connection, DockerRunner, Context]
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
    Force-remove the deployment lock for a specific site.

    Useful when a previous deployment was interrupted and the
    lock wasn't released.

    :param c: Fabric connection or context object.
    :type c: Union[Connection, DockerRunner, Context]

    :param site: Name of the site to unlock.
    :type site: str
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
    Remove lock files for all sites defined in ``sites.yml``.

    Useful when recovering from interrupted deploys or rollbacks.

    :param c: Fabric connection or context object.
    :type c: Union[Connection, DockerRunner, Context]
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

@task(help={"site": "Name of the site to restart"})
def restart_site(c: Connection | DockerRunner | Context, site: str) -> None:
    """
    Restart a single site defined in the configuration file.

    Loads site-specific configuration, resolves the proper connection
    (local, Docker, or SSH), and runs the full deployment process.

    :param c: Fabric connection or context object.
    :type c: Union[Connection, DockerRunner, Context]

    :param site: Name of the site as defined in ``sites.yml``.
    :type site: str
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

    restart_services(c, config)

@task
def restart_all(c: Connection | DockerRunner | Context) -> None:
    """
    Restart all sites listed in the configuration file.

    Iterates through all entries in ``sites.yml`` and runs the
    deployment pipeline for each one sequentially.

    :param c: Fabric connection or context object.
    :type c: Union[Connection, DockerRunner, Context]
    """
    # Load all site definitions
    sites = load_sites()

    # Deploy each site iteratively
    for site, config in sites.items():
        config['name'] = site
        # Use remote connection if available from environment
        c = get_connection(c, config = config)
        logger = get_logger(site)
        logger.info(f"Restarting site: {site}")
        restart_services(c, config)

# This line exposes the tasks to the Fabric CLI (`fab2 ...`)
ns = Collection(
    deploy,
    deploy_all,
    list_sites,
    rollback,
    rollback_all,
    unlock,
    unlock_all,
    restart_site,
    restart_all
)
