# ruff: noqa: C901
"""
Module for deployment recipes.

This module provides reusable deployment recipes that can be used to
deploy Python projects using Fabric. It includes functionality for
checking remote systems, updating code, creating backups, and more.

"""
import getpass
import os
import uuid
from datetime import UTC, datetime

from fabric2 import Connection
from invoke.context import Context

from fabricator.exceptions.deployer_exceptions import DeployerException
from fabricator.logger import get_logger
from fabricator.runners import DockerRunner


def check_remote(c: Connection | DockerRunner | Context, config: dict) -> None:
    """
    Validate the configuration and runner for a deployment site.

    Ensures that mandatory keys like `repository` and `deploy_path`
    exist in the configuration. Raises an exception if required fields
    are missing or misconfigured.

    :param c: Fabric runner or connection object.
    :type c: Union[Connection, DockerRunner, Context]

    :param config: Site configuration dictionary.
    :type config: dict

    :raises DeployerException: If configuration is invalid.
    """
    logger = get_logger(config['name'])

    # List of required configuration keys for deployment
    required_keys = ['repository', 'deploy_path']

    # Detect runner type to apply extra validation
    runner = config.get("runner", "local")

    # Check for missing required config keys
    missing = [key for key in required_keys if not config.get(key)]

    # If any required keys are missing, raise an error
    if missing:
        logger.error(f"Missing required config keys: {', '.join(missing)}")
        msg_raise = f"Invalid configuration for site '{config['name']}'."
        raise DeployerException(msg_raise)

    # For Docker runner, ensure 'docker_container' is defined
    if runner == "docker" and not config.get("docker_container"):
        logger.error("Missing 'docker_container' for Docker-based runner.")
        msg_raise = f"Invalid configuration for Docker site '{config['name']}'"
        raise DeployerException(msg_raise)

def update_code(c: Connection | DockerRunner | Context, config: dict) -> None:
    """
    Clone the repository into a temporary directory.

    Removes all files in the deployment path except essential ones,
    creates a temporary folder, and performs a fresh `git clone` into it.

    :param c: Fabric runner or connection object.
    :type c: Union[Connection, DockerRunner, Context]

    :param config: Site configuration dictionary.
    :type config: dict
    """
    logger = get_logger(config['name'])

    # Use the specified branch or fall back to 'main'
    branch = config.get("branch", "main")

    # Get repository URL and deployment path
    repo = config["repository"]
    deploy_path = config["deploy_path"]

    # Define the temporary clone directory
    tmp_clone = f"{deploy_path}/__clone_tmp__"

    # Clean old files but keep essential folders (env, releases, lock)
    logger.info("Cleaning old files in deploy path (excluding env and "
                "__clone_tmp__)...")
    c.run(
        f"find {deploy_path} -mindepth 1 -maxdepth 1 "
        f"! -name 'env' ! -name '__clone_tmp__' "
        f"! -name 'releases' ! -name 'current' ! -name '.deploy.lock' "
        f"-exec rm -rf {{}} +",
        warn=True
    )

    # Remove any previous temporary clone if exists
    logger.info("Removing any previous temporary clone...")
    c.run(f"rm -rf {tmp_clone}", warn=True)

    # Create the temporary directory with sudo and assign permissions
    remote_user = c.run("whoami", hide=True)
    if remote_user:
        remote_user = remote_user.stdout.strip()
        c.sudo(f"mkdir -p {tmp_clone}")
        c.sudo(f"chown {remote_user}:{remote_user} {tmp_clone}")

    # Perform the actual git clone into the temporary directory
    c.run(f"git clone --branch {branch} {repo} {tmp_clone}")

    # Log confirmation that clone completed
    logger.info("Code cloned into temporary folder (not moved yet).")

def shared_files(
        c: Connection | DockerRunner | Context,
        config: dict
    ) -> None:
    """
    Symlink shared files and folders from the shared path.

    Moves pre-existing files into a `shared/` directory and then links
    them into the current release.

    :param c: Fabric runner or connection object.
    :type c: Union[Connection, DockerRunner, Context]

    :param config: Site configuration dictionary.
    :type config: dict
    """
    logger = get_logger(config['name'])

    # Retrieve shared files and directories from config
    shared_files = config.get('shared_files', [])
    shared_dirs = config.get('shared_dirs', [])

    # Exit early if no shared elements are defined
    if not shared_files and not shared_dirs:
        return

    # Define the shared base path and the current release path
    shared_dir = os.path.join(config['deploy_path'], 'shared')
    release_dir = config['deploy_path']

    # Ensure the shared directory exists
    remote_user = c.run("whoami", hide=True)
    if remote_user:
        remote_user = remote_user.stdout.strip()
        c.sudo(f"mkdir -p {shared_dir}")
        c.sudo(f"chown {remote_user}:{remote_user} {shared_dir}")

    # Handle shared files
    for file in shared_files:
        shared_file = os.path.join(shared_dir, file)
        release_file = os.path.join(release_dir, file)

        # If a real file exists and is not a symlink, move it to shared
        result = c.run(f"test -f {release_file} && [ ! -L {release_file} ]",
                      warn=True)
        if result and result.ok:
            logger.warning(f"Moving existing file {file} to shared/")
            c.run(f"mv {release_file} {shared_file}", warn=True)

        # If the file doesn't exist in shared, create an empty one
        result = c.run(f"test -f {shared_file}", warn=True)
        if result and result.failed:
            c.run(f"touch {shared_file}", warn=True)

        # Create or update the symlink in the release directory
        c.run(f"ln -sfn {shared_file} {release_file}", warn=True)
        logger.info(f"Linked shared file: {file}")

    # Handle shared directories
    for d in shared_dirs:
        shared_subdir = os.path.join(shared_dir, d)
        release_subdir = os.path.join(release_dir, d)

        # If a real directory exists and is not a symlink, move it
        test_cmd = (
            f"test -d {release_subdir} && "
            f"[ ! -L {release_subdir} ]"
        )
        result = c.run(test_cmd, warn=True)
        if result and result.ok:
            logger.warning(f"Moving existing directory {d} to shared/")
            c.run(f"mv {release_subdir} {shared_subdir}", warn=True)

        # If the shared directory doesn't exist, create it
        result = c.run(f"test -d {shared_subdir}", warn=True)
        if result and result.failed:
            remote_user = c.run("whoami", hide=True)
            if remote_user:
                remote_user = remote_user.stdout.strip()
                c.sudo(f"mkdir -p {shared_dir}", warn=True)
                c.sudo(f"chown {remote_user}:{remote_user} {shared_dir}")

        # Create or update the symlink in the release directory
        c.run(f"ln -sfn {shared_subdir} {release_subdir}", warn=True)
        logger.info(f"Linked shared directory: {d}")

def install_deps(c: Connection | DockerRunner | Context, config: dict) -> None:
    """
    Install Python dependencies in a virtual environment.

    Creates a `venv` folder if it does not exist and installs packages
    from `requirements.txt` using pip.

    :param c: Fabric runner or connection object.
    :type c: Union[Connection, DockerRunner, Context]

    :param config: Site configuration dictionary.
    :type config: dict
    """
    logger = get_logger(config['name'])

    venv_path = config.get("venv", "venv")
    deploy_path = config["deploy_path"]
    venv_dir = f"{deploy_path}/{venv_path}"
    requirements_file = f"{deploy_path}/requirements.txt"

    # Check if virtualenv exists
    logger.info("Checking if virtualenv exists...")
    result = c.run(f"test -d {venv_dir}", warn=True)
    if result is None or result.failed:
        logger.info(f"Creating virtualenv at {venv_dir}")
        c.run(f"python3 -m venv {venv_dir}")

    # Check for requirements.txt before installing
    result = c.run(f"test -f {requirements_file}", warn=True)
    if result is None or result.failed:
        logger.warning(
            "No requirements.txt found, skipping dependency install."
        )
        return

    logger.info("Installing Python dependencies...")
    c.run(
        f"bash -c 'source {venv_dir}/bin/activate && "
        f"cd {deploy_path} && "
        f"pip install --prefer-binary -r requirements.txt'",
        pty=True
    )

def migrate(c: Connection | DockerRunner | Context, config: dict) -> None:
    """
    Run Django migrations and optionally run `db_seed`.

    Validates the migration plan before executing it to prevent errors
    from being deployed to production.

    :param c: Fabric runner or connection object.
    :type c: Union[Connection, DockerRunner, Context]

    :param config: Site configuration dictionary.
    :type config: dict

    :raises DeployerException: If validation or execution fails.
    """
    logger = get_logger(config['name'])

    # Get virtualenv folder from config, defaulting to 'venv'
    venv_path = config.get("venv", "venv")

    # Get deployment path from config
    deploy_path = config['deploy_path']

    # Log that the migration plan will be validated
    logger.info("Validating Django migration plan...")

    # Run migration plan check inside virtualenv
    plan_check = c.run(
        f"bash -c 'source {deploy_path}/{venv_path}/bin/activate && "
        f"cd {deploy_path} && python manage.py migrate --plan'",
        pty=True,
        warn=True,
        hide=True
    )
    # Abort if validation fails or if plan_check is None
    if not plan_check or plan_check.failed:
        logger.error("Migration plan check failed. Aborting deploy.")
        output = getattr(plan_check, "stdout", "").strip()
        if output:
            logger.error(f"Error output:\n{output}")
        msg_raise = "Migration validation failed."
        raise DeployerException(msg_raise)

    # Log success and continue to actual migration
    logger.info("Migration plan validated successfully.")
    logger.info("Running Django migrations...")

    try:
        # Execute real migration command
        c.run(
            f"source {deploy_path}/{venv_path}/bin/activate && "
            f"cd {deploy_path} && python manage.py migrate",
            pty=True
        )

    except DeployerException as e:
        # Handle any exception during migration
        logger.error("Migration execution failed.")
        msg_raise = f"Migration error: {e!s}"
        raise DeployerException(msg_raise) from e

    logger.info("Running Django db_seed...")
    try:
        # Execute real migration command
        c.run(
            f"bash -c 'source {deploy_path}/{venv_path}/bin/activate && "
            f"cd {deploy_path} && python manage.py db_seed "
            f"> /dev/null 2>&1'",
            pty=True
        )
    except DeployerException as e:
        # Handle any exception during migration
        logger.error("Seed execution failed.")
        msg_raise = f"Seed error: {e!s}"
        raise DeployerException(msg_raise) from e

def collect_static(
        c: Connection | DockerRunner | Context,
        config: dict
    ) -> None:
    """
    Run Django's `collectstatic` to gather static files.

    Executes `manage.py collectstatic --noinput` inside the virtualenv.

    :param c: Fabric runner or connection object.
    :type c: Union[Connection, DockerRunner, Context]

    :param config: Site configuration dictionary.
    :type config: dict
    """
    logger = get_logger(config['name'])

    # Get virtualenv path from config
    venv_path = config.get("venv", "venv")

    # Get deployment path
    deploy_path = config['deploy_path']

    # Log that we're collecting static files
    logger.info("Collecting static files...")

    # Run collectstatic inside virtualenv, silencing output
    c.run(
        f"bash -c 'source {deploy_path}/{venv_path}/bin/activate && "
        f"cd {deploy_path} && python manage.py collectstatic "
        f"--noinput > /dev/null 2>&1'",
        pty=True
    )

# ruff: noqa: PLR0912
def restart_services(
        c: Connection | DockerRunner | Context,
        config: dict
    ) -> None:
    """
    Start or restart Gunicorn and Celery workers for the project.

    Uses the bash scripts 'start_gunicorn_supervisord.sh' and
    'start_celery_supervisord.sh' to handle the restart process.
    If either script doesn't exist, logs a warning and continues.

    :param c: Fabric runner or connection object.
    :type c: Union[Connection, DockerRunner, Context]

    :param config: Site configuration dictionary.
    :type config: dict
    """
    logger = get_logger(config['name'])

    # Get site name from config
    site = config['name']

    # Define script paths
    gunicorn_script_path = "/scripts/start_gunicorn_supervisord.sh"
    celery_script_path = "/scripts/start_celery_supervisord.sh"

    # First, restart Gunicorn services
    # Check if the Gunicorn script exists
    check_gunicorn_script = c.run(
        f"test -f {gunicorn_script_path} && echo 'exists' || echo 'not_found'",
        hide=True,
        warn=True
    )
    if check_gunicorn_script is None:
        logger.warning(
            f"Script {gunicorn_script_path} not found. "
            f"Skipping Gunicorn restart."
        )
    else:
        gunicorn_script_exists = (
            check_gunicorn_script.stdout.strip() == "exists"
        )

        if not gunicorn_script_exists:
            logger.warning(
                f"Script {gunicorn_script_path} not found. "
                f"Skipping Gunicorn restart."
            )
        else:
            logger.info(f"Restarting Gunicorn services for {site}...")

            # Determine if we should restart a specific site or all sites
            if site and site != "all":
                # Restart specific site
                result = c.run(f"{gunicorn_script_path} {site}", warn=True)
            else:
                # Restart all sites
                result = c.run(f"{gunicorn_script_path}", warn=True)

            # Check if the command executed successfully
            if result and not result.failed:
                logger.info(
                    f"Gunicorn services for {site} restarted successfully"
                )
            else:
                logger.error(f"Failed to restart Gunicorn services for {site}")
                error_details = (
                    result.stderr if result and hasattr(result, 'stderr')
                    else 'Unknown error'
                )
                logger.error(f"Error details: {error_details}")

    # Second, restart Celery workers
    # Check if the Celery script exists
    check_celery_script = c.run(
        f"test -f {celery_script_path} && echo 'exists' || echo 'not_found'",
        hide=True,
        warn=True
    )
    if check_celery_script is None:
        logger.warning(
            f"Script {celery_script_path} not found. "
            f"Skipping Celery workers restart."
        )
    else:
        celery_script_exists = check_celery_script.stdout.strip() == "exists"

        if not celery_script_exists:
            logger.warning(
                f"Script {celery_script_path} not found. "
                f"Skipping Celery workers restart."
            )
        else:
            logger.info(f"Restarting Celery workers for {site}...")

            # Determine if we should restart a specific site or all sites
            if site and site != "all":
                # Restart specific site
                result = c.run(f"{celery_script_path} {site}", warn=True)
            else:
                # Restart all sites
                result = c.run(f"{celery_script_path}", warn=True)

            # Check if the command executed successfully
            if result and not result.failed:
                logger.info(
                    f"Celery workers for {site} restarted successfully"
                )
            else:
                logger.error(f"Failed to restart Celery workers for {site}")
                error_details = (
                    result.stderr if result and hasattr(result, 'stderr')
                    else 'Unknown error'
                )
                logger.error(f"Error details: {error_details}")

def set_writable_dirs(
        c: Connection | DockerRunner | Context,
        config: dict
    ) -> None:
    """
    Set permissions on writable directories as defined in the config.

    Supports options for recursion, use of `sudo`, and custom chmod modes.

    :param c: Fabric runner or connection object.
    :type c: Union[Connection, DockerRunner, Context]

    :param config: Site configuration dictionary.
    :type config: dict
    """
    logger = get_logger(config['name'])

    # Load writable directories from config
    writable_dirs = config.get("writable_dirs", [])
    if not writable_dirs:
        return  # No directories to process

    # Load optional configuration flags
    recursive = config.get("writable_recursive", False)
    use_sudo = config.get("writable_use_sudo", False)
    chmod_mode = config.get("writable_chmod_mode", "775")
    deploy_path = config["deploy_path"]

    # Loop through each defined directory
    for directory in writable_dirs:
        # Compose full path and chmod command
        full_path = os.path.join(deploy_path, directory)
        recursive_flag = "-R " if recursive else ""
        chmod_cmd = f"chmod {recursive_flag}{chmod_mode} {full_path}"

        logger.info(f"Setting writable permissions on: {directory}")

        # Use sudo or regular run based on config
        if use_sudo:
            c.sudo(chmod_cmd, warn=True)
        else:
            c.run(chmod_cmd, warn=True)

def create_backup(
        c: Connection | DockerRunner | Context,
        config: dict
    ) -> None:
    """
    Create a compressed `.tar.gz` backup before deployment.

    Also removes older backups if the number exceeds `max_backups`.

    :param c: Fabric runner or connection object.
    :type c: Union[Connection, DockerRunner, Context]

    :param config: Site configuration dictionary.
    :type config: dict
    """
    logger = get_logger(config["name"])

    # Load backup configuration from site config
    backup_path = config.get("backup_path")
    max_backups = config.get("max_backups", 5)

    # If backup path is not defined, skip this step
    if not backup_path:
        msg = "Skipping backup: 'backup_path' not defined in config."
        logger.warning(msg)
        return

    # Build backup filename and path
    deploy_path = config["deploy_path"]
    site_name = config["name"]
    timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    backup_file = os.path.join(
        backup_path, f"{site_name}_{timestamp}.tar.gz"
    )

    # Check if current symlink exists (active deployment)
    result = c.run(
        f"test -L {deploy_path}/current && echo 'exists'",
        hide=True, warn=True
    )

    if not result or not result.ok or result.stdout.strip() != 'exists':
        msg = (
            "Skipping backup: No active deployment found "
            "(current symlink doesn't exist)."
        )
        logger.warning(msg)
        return

    logger.info(
        f"Creating backup for site '{site_name}' at {backup_file}"
    )

    # Ensure backup directory exists
    remote_user = c.run("whoami", hide=True)
    if remote_user:
        remote_user = remote_user.stdout.strip()
        c.sudo(f"mkdir -p {backup_path}")
        c.sudo(f"chown {remote_user}:{remote_user} {backup_path}")

    # Create compressed backup using tar
    try:
        # Get the current release name
        result = c.run(
            f"basename $(readlink {deploy_path}/current)",
            hide=True
        )
        release_name = result.stdout.strip() if result else ""

        # Backup only the current release
        c.run(
            f"tar -czf {backup_file} -C {deploy_path}/releases "
            f"{release_name}"
        )
        logger.info(f"Backup created for release: {release_name}")
    except DeployerException as e:
        logger.warning(f"Backup creation failed: {e}")
        return

    # Delete older backups if exceeding max_backups
    try:
        result = c.run(
            f"ls -1t {backup_path}/{site_name}_*.tar.gz",
            hide=True, warn=True
        )

        # Initialize files as an empty list by default
        files = []

        # Only assign to files if there are results
        if result and result.stdout:
            files = result.stdout.strip().splitlines()

        # Now we can safely verify len(files)
        if len(files) > max_backups:
            old_files = files[max_backups:]
            for file in old_files:
                c.run(f"rm -f {os.path.join(backup_path, file)}")
                logger.info(f"Removed old backup: {file}")
    except DeployerException as e:
        logger.warning(f"Could not cleanup old backups: {e}")

def deploy_to_release_folder(
        c: Connection | DockerRunner | Context,
        config: dict
    ) -> str:
    """
    Move code from clone folder to timestamped `releases/` directory.

    Also links `env` file and returns the path to the new release.

    :param c: Fabric runner or connection object.
    :type c: Union[Connection, DockerRunner, Context]

    :param config: Site configuration dictionary.
    :type config: dict

    :return: Absolute path to the new release directory.
    :rtype: str
    """
    logger = get_logger(config["name"])

    deploy_path = config["deploy_path"]
    releases_path = os.path.join(deploy_path, "releases")

    # Generate release folder name with timestamp (ms precision)
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")[:-3]
    release_path = os.path.join(releases_path, timestamp)

    # Temporary folder containing newly cloned code
    tmp_path = os.path.join(deploy_path, "__clone_tmp__")

    # Ensure releases folder exists
    remote_user = c.run("whoami", hide=True)
    if remote_user:
        remote_user = remote_user.stdout.strip()
        c.sudo(f"mkdir -p {releases_path}", warn=True)
        c.sudo(f"chown {remote_user}:{remote_user} {releases_path}")

    # Abort if the release folder already exists
    result = c.run(f"test -e {release_path}", warn=True, hide=True)
    if result and result.ok:
        logger.error(f"Release path already exists: {release_path}")
        return ""

    # Move __clone_tmp__ into the final release folder
    c.sudo(f"mv {tmp_path} {release_path}", warn=True)

    # Link the env file (used for secrets) into the new release
    env_source = os.path.join(deploy_path, "env")
    env_target = os.path.join(release_path, "env")
    c.sudo(f"ln -sf {env_source} {env_target}", warn=True)
    logger.info(f"env linked: {env_target} -> {env_source}")

    # Log release creation details
    logger.info(f"Release created at: {release_path}")

    return release_path

def symlink_release_to_current(
    c: Connection | DockerRunner | Context,
    config: dict
) -> None:
    """
    Update the `current` symlink to point to the latest release.

    :param c: Fabric runner or connection object.
    :type c: Union[Connection, DockerRunner, Context]

    :param config: Site configuration dictionary.
    :type config: dict
    """
    logger = get_logger(config["name"])
    original_path = config["original_path"]
    deploy_path = config["deploy_path"]

    # Symlink that always points to the current release
    current_symlink = os.path.join(original_path, "current")

    # Point the current symlink to this new release
    c.run(f"ln -sfn {deploy_path} {current_symlink}", warn=True)

    # Log release creation details
    logger.info(f"Symlink updated: {current_symlink} -> {deploy_path}")

def cleanup_old_releases(
    c: Connection | DockerRunner | Context,
    config: dict,
    keep: int = 5
) -> None:
    """
    Remove older release directories beyond a given retention count.

    :param c: Fabric runner or connection object.
    :type c: Union[Connection, DockerRunner, Context]

    :param config: Site configuration dictionary.
    :type config: dict

    :param keep: Number of recent releases to keep (default: 5).
    :type keep: int
    """
    logger = get_logger(config["name"])

    # Derive the base project path from the current release path
    current_release_path = config["deploy_path"]
    project_root = os.path.dirname(current_release_path)
    releases_path = project_root

    # List all releases sorted by most recent
    result = c.run(f"ls -1dt {releases_path}/*", hide=True, warn=True)

    if not result or not result.stdout:
        logger.warning("No releases found. Skipping cleanup.")
        return

    all_releases = result.stdout.strip().splitlines()

    if len(all_releases) > keep:
        to_delete = all_releases[keep:]
        for release in to_delete:
            logger.info(f"Removing old release: {release}")
            c.run(f"rm -rf {release}", warn=True)

def rollback_to_previous_release(
    c: Connection | DockerRunner | Context,
    config: dict
) -> None:
    """
    Revert the `current` symlink to the previous release.

    Deletes the failed release folder and restores the last known
    working version.

    :param c: Fabric runner or connection object.
    :type c: Union[Connection, DockerRunner, Context]

    :param config: Site configuration dictionary.
    :type config: dict
    """
    logger = get_logger(config['name'])

    # Normalize path: remove /releases/... if present
    deploy_path = config["deploy_path"]
    if "/releases/" in deploy_path:
        base_path = deploy_path.split("/releases/")[0]
    else:
        base_path = deploy_path

    # Path to all versioned releases
    releases_path = os.path.join(base_path, "releases")

    # Path to current symlink
    current_symlink = os.path.join(base_path, "current")

    # List all available releases
    result = c.run(f"ls -1dt {releases_path}/*", warn=True, hide=True)

    if not result or result.failed or not result.stdout.strip():
        logger.error("No releases found.")
        return

    all_releases = result.stdout.strip().splitlines()

    # Ensure there are at least two releases to rollback
    all_releases_min = 2
    if len(all_releases) < all_releases_min:
        logger.error("No previous release found. Cannot rollback.")
        return

    # Roll back to the previous release
    failed_release = all_releases[0]
    previous_release = all_releases[1]

    logger.warning(f"Rolling back to previous release: {previous_release}")
    c.run(f"ln -sfn {previous_release} {current_symlink}", warn=True)

    logger.info(f"Removing failed release: {failed_release}")
    c.run(f"rm -rf {failed_release}", warn=True)

def acquire_lock(
        c: Connection | DockerRunner | Context,
        config: dict
    ) -> str:
    """
    Create a `.deploy.lock` file to prevent concurrent deployments.

    Writes session metadata into the lock file. Aborts if already locked.

    :param c: Fabric runner or connection object.
    :type c: Union[Connection, DockerRunner, Context]

    :param config: Site configuration dictionary.
    :type config: dict

    :raises DeployerException: If a lock is already present.
    :return: Unique session lock ID.
    :rtype: str
    """
    logger = get_logger(config['name'])

    # Path where the lock file will be created
    deploy_path = config['deploy_path']

    # Check if the deploy directory exists, if not, create it
    logger.info("Ensured deployment path exists.")
    result = c.run(f"test -d {deploy_path}", warn=True, hide=True)
    if not result or not result.ok:
        # Create the temporary directory with sudo and assign permissions
        logger.info(f"Creating deploy directory: {deploy_path}")
        remote_user = c.run("whoami", hide=True)
        if remote_user:
            remote_user = remote_user.stdout.strip()
            c.sudo(f"mkdir -p {deploy_path}")
            c.sudo(f"chmod 775 {deploy_path}")
            c.sudo(f"chown {remote_user}:{remote_user} {deploy_path}")

    # Path to the lock file
    lock_file = os.path.join(deploy_path, ".deploy.lock")

    # Generate a unique ID using timestamp and UUID
    lock_id = f"{datetime.now(UTC)
                 .strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex}"

    # Detect system user (who initiated the deployment)
    user = getpass.getuser()

    # If lock already exists, abort
    result = c.run(f"test -f {lock_file}", warn=True, hide=True)
    if result and result.ok:
        msg = "Deployment is already locked. Another deploy in progress."
        logger.error(msg)
        msg = "Deploy locked. Use `fab2 unlock --site=...`"
        msg += "to force unlock if needed."
        raise DeployerException(msg)

    # Compose informative lock file content
    content = (
        f"Locked at: {datetime.now(UTC).isoformat()}\n"
        f"Host: {getattr(c, 'host', 'local')}\n"
        f"User: {user}\n"
        f"Lock ID: {lock_id}\n"
    )

    # Create the lock file explicitly and then write to it
    c.sudo(f"touch {lock_file}")
    # Set appropriate permissions 664 allows group read/write
    c.sudo(f"chmod 664 {lock_file}")

    # Write lock file to remote system
    c.run(f"sudo bash -c \"echo '{content}' > {lock_file}\"")
    logger.info("Deployment lock acquired.")

    return lock_id

def release_lock(
    c: Connection | DockerRunner | Context,
    config: dict,
    lock_id: str = "",
    force: bool = False
) -> None:
    """
    Remove the `.deploy.lock` file if owned by this session.

    If `force` is enabled, the lock is removed regardless of ownership.

    :param c: Fabric runner or connection object.
    :type c: Union[Connection, DockerRunner, Context]

    :param config: Site configuration dictionary.
    :type config: dict

    :param lock_id: Lock ID from the current session (optional).
    :type lock_id: str

    :param force: Force unlock, bypassing ID validation.
    :type force: bool
    """
    logger = get_logger(config['name'])
    deploy_path = config['deploy_path']
    lock_file = os.path.join(deploy_path, ".deploy.lock")

    if force:
        c.run(f"rm -f {lock_file}", warn=True)
        logger.info("Deployment lock forcibly released.")
        return

    result = c.run(f"grep '^Lock ID:' {lock_file}", warn=True, hide=True)
    if result and result.ok:
        current_id = result.stdout.strip().split(":", 1)[-1].strip()
    else:
        current_id = ""

    if current_id == lock_id:
        c.run(f"rm -f {lock_file}", warn=True)
        logger.info("Deployment lock released.")
    else:
        logger.warning("Lock file not owned by this session. Skipping unlock.")
