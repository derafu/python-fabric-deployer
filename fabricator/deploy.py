from fabric2 import Connection
from invoke import Context
from fabricator.runners import DockerRunner
from fabricator.logger import get_logger
from fabricator.recipes import (
    check_remote,
    update_code,
    install_deps,
    migrate,
    collect_static,
    restart_services,
    shared_files,
    set_writable_dirs,
    create_backup,
    deploy_to_release_folder,
    cleanup_old_releases,
    rollback_to_previous_release,
    acquire_lock,
    release_lock
)


def deploy_site(c: Connection, config: dict) -> None:
    """
    Deploy a Django site using the appropriate runner (local, docker, or ssh).

    This function orchestrates the full deployment pipeline:
    - Acquires a deployment lock to prevent concurrent deploys.
    - Verifies remote directory structure.
    - Creates a compressed backup of the current version.
    - Clones the repository and creates a timestamped release folder.
    - Updates the `current` symlink to the new release.
    - Symlinks shared files and writable folders.
    - Installs dependencies, runs migrations, collectstatic, and restarts services.
    - Performs automatic rollback in case of failure.
    - Releases the lock regardless of success or failure.

    :param c: Fabric connection or context object (local, ssh, or Docker).
    :param config: Site configuration dictionary loaded from sites.yml.
    """
    # Initialize logger for this site
    logger = get_logger(config['name'])

    # Detect runner type: "local", "docker", or "ssh" (default is local)
    runner_type = config.get("runner", "local")
    if runner_type == "docker":
        # If using Docker, wrap the context with a DockerRunner
        container = config["docker_container"]
        docker_user = config.get("docker_user")
        c = DockerRunner(container_name=container, inner_runner=Context(), user=docker_user)
        host = f"docker:{container}"
    else:
        # Fallback to local or SSH connection
        host = getattr(c, "host", "local")

    # Log the start of deployment
    logger.info(f"Starting deployment for '{config['name']}' on '{host}'")
    lock_id = None
    release_path = None

    try:
        # Step 0: Acquire lock to prevent concurrent deployments
        lock_id = acquire_lock(c, config)

        # Step 1: Ensure the remote deployment directory exists
        check_remote(c, config)

        # Step 2: Create a compressed backup of the current state
        create_backup(c, config)

        # Step 3: Clone the repository into a temporary folder
        update_code(c, config)

        # Step 4: Move code into a timestamped release folder and update `current` symlink
        original_path = config["deploy_path"]
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

        # Step 12: Restart Gunicorn or other services to reflect changes
        restart_services(c, config)

        # Deployment completed successfully
        logger.info(f"Deployment for '{config['name']}' completed successfully.")

    except Exception as e:
        # Log the deployment error
        logger.error(f"Deployment failed: {e}")
        if release_path:
            # If the release was created, rollback to previous working release
            logger.info("Rolling back to previous release...")
            rollback_to_previous_release(c, config)

    finally:
        # Always release the lock regardless of success or failure
        if lock_id:
            config["deploy_path"] = original_path
            release_lock(c, config, lock_id)
