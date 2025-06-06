"""
Module for deploying Python projects using Fabric.

This module provides functionality for deploying Python projects using
Fabric, including deployment steps, lock management, and rollback
mechanisms.

"""
from fabric2 import Connection
from invoke.context import Context

from fabricator.exceptions.deployer_exceptions import DeployerException
from fabricator.logger import get_logger
from fabricator.recipes import (
    acquire_lock,
    check_remote,
    cleanup_old_releases,
    collect_static,
    create_backup,
    deploy_to_release_folder,
    install_deps,
    migrate,
    release_lock,
    restart_services,
    rollback_to_previous_release,
    set_writable_dirs,
    shared_files,
    symlink_release_to_current,
    update_code,
)
from fabricator.runners import DockerRunner


def deploy_site(c: Connection | DockerRunner | Context, config: dict) -> None:
    """
    Deploy a Python web project using local, Docker, or SSH context.

    This function executes the full deployment pipeline for a Django or
    Python-based project. It handles preparation, code updates, shared
    resources, virtualenv management, database migrations, static files,
    and service restarts. It also ensures safety through locking and
    rollback mechanisms.

    Deployment steps include:

    1. Acquiring a lock to prevent concurrent deployments.
    2. Verifying and preparing the remote directory structure.
    3. Creating a compressed backup of the current version.
    4. Cloning the repository into a new timestamped release folder.
    5. Cleaning up old releases beyond `max_releases`.
    6. Linking shared files and directories to the new release.
    7. Setting permissions on writable directories.
    8. Installing dependencies in a virtual environment.
    9. Running Django database migrations.
    10. Running Django `collectstatic`.
    11. Updating the `current` symlink to point to the new release.
    12. Restarting services like Gunicorn to apply changes.
    13. Rolling back to the previous release if a failure occurs.
    14. Releasing the deployment lock at the end.

    :param c: The Fabric context or connection object. Can be a local
            `Context`, remote `Connection`, or `DockerRunner`.
    :type c: Union[Connection, DockerRunner, Context]

    :param config: Dictionary with all deployment-related settings loaded
                from `sites.yml`, including project name, paths, shared
                files, and service settings.
    :type config: dict

    :raises DeployerException: If any step in the process fails.
    :rtype: None
    """
    # Initialize logger for this site
    logger = get_logger(config['name'])

    host = getattr(c, "host", "local")
    # Log the start of deployment
    msg = f"Starting deployment for '{config['name']}' on '{host}'"
    logger.info(msg)
    lock_id = None
    release_path = None
    original_path = None
    try:
        # Step 0: Acquire lock to prevent concurrent deployments
        lock_id = acquire_lock(c, config)

        # Step 1: Ensure the remote deployment directory exists
        check_remote(c, config)

        # Step 2: Create a compressed backup of the current state
        create_backup(c, config)

        # Step 3: Clone the repository into a temporary folder
        update_code(c, config)

        # Step 4: Move code into timestamped release folder and update symlink
        original_path = config["deploy_path"]
        config["original_path"] = original_path
        release_path = deploy_to_release_folder(c, config)
        config["deploy_path"] = release_path

        # Step 5: Clean up old releases, keeping only the most recent ones
        max_releases = config.get("max_releases", 5)
        cleanup_old_releases(c, config, keep=max_releases)

        # Step 6: Switch deployment path to the new release folder
        config["deploy_path"] = release_path

        # Step 7: Symlink shared files and folders (e.g., `.env`, `media/`)
        shared_files(c, config)

        # Step 8: Set write permissions on defined folders (e.g., 775)
        set_writable_dirs(c, config)

        # Step 9: Install dependencies inside the virtual environment
        install_deps(c, config)

        # Step 10: Validate and run Django database migrations
        migrate(c, config)

        # Step 11: Run Django's collectstatic to prepare static files
        collect_static(c, config)

        # Step 12: Symlink the release to the current symlink
        symlink_release_to_current(c, config)

        # Step 13: Restart Gunicorn or other services to reflect changes
        restart_services(c, config)

        # Deployment completed successfully
        msg = f"Deployment for '{config['name']}' completed successfully."
        logger.info(msg)

    except DeployerException as e:
        # Log the deployment error
        logger.error(f"Deployment failed: {e}")
        if release_path:
            # If the release was created, rollback to previous working release
            logger.info("Rolling back to previous release...")
            rollback_to_previous_release(c, config)

    finally:
        # Always release the lock regardless of success or failure
        if lock_id:
            config["deploy_path"] = original_path if (
                original_path
            ) else config["deploy_path"]
            release_lock(c, config, lock_id)
