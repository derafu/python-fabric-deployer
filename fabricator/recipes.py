import os
import getpass
import uuid
from datetime import datetime
from fabric2 import Connection
from invoke.context import Context
from fabricator.logger import get_logger


def check_remote(c: Connection, config: dict) -> None:
    """
    Ensure the deployment path exists on the remote system and that
    all required configuration fields are provided.

    :param c: Fabric connection object.
    :param config: Site configuration dictionary.
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
        raise ValueError(f"Invalid configuration for site '{config['name']}'.")

    # For Docker runner, ensure 'docker_container' is defined
    if runner == "docker" and not config.get("docker_container"):
        logger.error("Missing 'docker_container' for Docker-based runner.")
        raise ValueError(f"Invalid configuration for Docker site '{config['name']}'.")

    # Ensure that the remote deploy path exists
    c.run(f"mkdir -p {config['deploy_path']}", warn=True)
    logger.info("Ensured deployment path exists.")


def update_code(c: Connection, config: dict) -> None:
    """
    Clone the repository into a temporary folder without overwriting
    the main deployment directory.

    :param c: Fabric connection or context object.
    :param config: Site configuration dictionary.
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
        f"! -name 'releases' ! -name '.deploy.lock' "
        f"-exec rm -rf {{}} +",
        warn=True
    )

    # Remove any previous temporary clone if exists
    logger.info("Cloning into temporary directory...")
    c.run(f"rm -rf {tmp_clone}", warn=True)

    # Perform the actual git clone into the temporary directory
    c.run(f"git clone --branch {branch} {repo} {tmp_clone}")

    # Log confirmation that clone completed
    logger.info("Code cloned into temporary folder (not moved yet).")

def shared_files(c: Connection, config: dict) -> None:
    """
    Create symlinks for shared files and directories between releases.

    Moves existing files or directories into `shared/` before linking,
    if they are not already symlinks. Ensures that each shared element
    exists and links correctly.

    :param c: Fabric connection object.
    :param config: Site configuration dictionary.
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
    c.run(f"mkdir -p {shared_dir}", warn=True)

    # Handle shared files
    for file in shared_files:
        shared_file = os.path.join(shared_dir, file)
        release_file = os.path.join(release_dir, file)

        # If a real file exists and is not a symlink, move it to shared
        if c.run(f"test -f {release_file} && [ ! -L {release_file} ]",
                 warn=True).ok:
            logger.warning(f"Moving existing file {file} to shared/")
            c.run(f"mv {release_file} {shared_file}", warn=True)

        # If the file doesn't exist in shared, create an empty one
        if c.run(f"test -f {shared_file}", warn=True).failed:
            c.run(f"touch {shared_file}", warn=True)

        # Create or update the symlink in the release directory
        c.run(f"ln -sfn {shared_file} {release_file}", warn=True)
        logger.info(f"Linked shared file: {file}")

    # Handle shared directories
    for d in shared_dirs:
        shared_subdir = os.path.join(shared_dir, d)
        release_subdir = os.path.join(release_dir, d)

        # If a real directory exists and is not a symlink, move it
        if c.run(f"test -d {release_subdir} && [ ! -L {release_subdir} ]",
                 warn=True).ok:
            logger.warning(f"Moving existing directory {d} to shared/")
            c.run(f"mv {release_subdir} {shared_subdir}", warn=True)

        # If the shared directory doesn't exist, create it
        if c.run(f"test -d {shared_subdir}", warn=True).failed:
            c.run(f"mkdir -p {shared_subdir}", warn=True)

        # Create or update the symlink in the release directory
        c.run(f"ln -sfn {shared_subdir} {release_subdir}", warn=True)
        logger.info(f"Linked shared directory: {d}")


def install_deps(c: Connection, config: dict) -> None:
    """
    Create and activate a virtual environment, then install dependencies.

    If the virtualenv doesn't exist, it is created. Then pip installs all
    requirements listed in `requirements.txt`.

    :param c: Fabric connection object.
    :param config: Site configuration dictionary.
    """
    logger = get_logger(config['name'])

    # Get virtualenv folder (default: "venv")
    venv_path = config.get("venv", "venv")

    # Get deploy path
    deploy_path = config["deploy_path"]

    # Check if virtualenv directory exists
    logger.info("Checking if virtualenv exists...")
    if c.run(f"test -d {deploy_path}/{venv_path}", warn=True).failed:
        logger.info(f"Creating virtualenv at {venv_path}")
        c.run(f"python3 -m venv {deploy_path}/{venv_path}")

    # Install Python packages from requirements.txt
    logger.info("Installing Python dependencies...")
    c.run(
        f"bash -c 'source {deploy_path}/{venv_path}/bin/activate && "
        f"cd {deploy_path} && pip install -r requirements.txt > /dev/null'",
        pty=True
    )

def migrate(c: Connection, config: dict) -> None:
    """
    Run Django database migrations after validating with `--plan`.

    This ensures that all migrations are valid before executing
    them to avoid breaking the database during deployment.

    :param c: Fabric connection object.
    :param config: Site configuration dictionary.
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

    # Abort if validation fails
    if plan_check.failed:
        logger.error("Migration plan check failed. Aborting deploy.")
        logger.error(f"Error output:\n{plan_check.stdout.strip()}")
        raise RuntimeError("Migration validation failed.")

    # Log success and continue to actual migration
    logger.info("Migration plan validated successfully.")
    logger.info("Running Django migrations...")

    try:
        # Execute real migration command
        c.run(
            f"bash -c 'source {deploy_path}/{venv_path}/bin/activate && "
            f"cd {deploy_path} && python manage.py migrate "
            f"> /dev/null 2>&1'",
            pty=True
        )
    except Exception as e:
        # Handle any exception during migration
        logger.error("Migration execution failed.")
        raise RuntimeError(f"Migration error: {str(e)}") from e


def collect_static(c: Connection, config: dict) -> None:
    """
    Collect static files using Django's `collectstatic` command.

    Runs `python manage.py collectstatic --noinput` from within
    the virtual environment and deployment path.

    :param c: Fabric connection object.
    :param config: Site configuration dictionary.
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


def restart_services(c: Connection, config: dict) -> None:
    """
    Restart Gunicorn server using background command execution.

    Skips execution if the environment is local. Attempts to detect
    the `wsgi.py` file and launch Gunicorn with socket and logs.

    :param c: Fabric connection object.
    :param config: Site configuration dictionary.
    """
    logger = get_logger(config['name'])

    # Determine if environment is local
    is_local = not isinstance(c, Connection) or getattr(
        c, "host", "localhost") in ("localhost", "127.0.0.1")

    # Skip Gunicorn startup on local
    if is_local:
        logger.info("Skipping Gunicorn startup in local environment.")
        return

    # Get site name from config
    site = config['name']

    # Define paths
    base_path = config['deploy_path']
    current_path = f"{base_path}/current"
    venv_path = config.get("venv", "venv")

    # Path to Gunicorn executable
    gunicorn_bin = f"{current_path}/{venv_path}/bin/gunicorn"

    # Define Unix socket for Gunicorn
    socket_path = f"/run/gunicorn/{site}.sock"

    # Define log file paths
    access_log = f"/var/log/gunicorn/{site}-access.log"
    error_log = f"/var/log/gunicorn/{site}-error.log"

    # Attempt to locate wsgi.py file
    result = c.run(
        f"find {current_path} -maxdepth 2 -name wsgi.py | head -n 1",
        hide=True,
        warn=True,
    )

    # Abort if wsgi.py not found
    if result.failed or not result.stdout.strip():
        logger.error("Could not find wsgi.py file.")
        return

    # Extract wsgi path and project name
    wsgi_path = result.stdout.strip()
    project_dir = os.path.dirname(wsgi_path)
    project_name = os.path.basename(project_dir)

    # Log where wsgi.py was found
    logger.info(f"Found wsgi.py in {wsgi_path}, project: {project_name}")
    logger.info(f"Running Gunicorn for project {project_name} in background...")

    # Command to launch Gunicorn in background, disowned
    cmd = (
        f"bash -c 'cd {project_dir} && "
        f"export PYTHONPATH={current_path} && "
        f"{gunicorn_bin} --workers=3 "
        f"--bind=unix:{socket_path} "
        f"{project_name}.wsgi:application "
        f"--access-logfile {access_log} "
        f"--error-logfile {error_log} "
        f"--log-level=info &' disown"
    )

    # Execute the command
    c.run(cmd, pty=False)

def set_writable_dirs(c: Connection, config: dict) -> None:
    """
    Set permissions on writable directories using chmod.

    Supports recursive mode, sudo usage, and custom chmod values
    defined in the site's configuration.

    YAML example:
      writable_dirs:
        - logs
        - media
      writable_recursive: true
      writable_use_sudo: false
      writable_chmod_mode: 775

    :param c: Fabric connection object.
    :param config: Site configuration dictionary.
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
        chmod_cmd = f"chmod {'-R ' if recursive else ''}{chmod_mode} {full_path}"

        logger.info(f"Setting writable permissions on: {directory}")

        # Use sudo or regular run based on config
        if use_sudo:
            c.sudo(chmod_cmd, warn=True)
        else:
            c.run(chmod_cmd, warn=True)


def create_backup(c: Connection | Context, config: dict) -> None:
    """
    Create a compressed backup of the project before deployment.

    Also removes older backups if the maximum limit is exceeded.

    :param c: Fabric connection or context object.
    :param config: Site configuration dictionary.
    """
    logger = get_logger(config["name"])

    # Load backup configuration from site config
    backup_path = config.get("backup_path")
    max_backups = config.get("max_backups", 5)

    # If backup path is not defined, skip this step
    if not backup_path:
        logger.warning("Skipping backup: 'backup_path' not defined in config.")
        return

    # Build backup filename and path
    deploy_path = config["deploy_path"]
    site_name = config["name"]
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    backup_file = os.path.join(backup_path, f"{site_name}_{timestamp}.tar.gz")

    logger.info(f"Creating backup for site '{site_name}' at {backup_file}")

    # Ensure backup directory exists
    try:
        c.run(f"mkdir -p {backup_path}")
    except Exception as e:
        logger.warning(f"Could not create backup folder: {e}")
        return

    # Create compressed backup using tar
    try:
        c.run(f"tar -czf {backup_file} -C {deploy_path} .")
        logger.info(f"Backup created: {backup_file}")
    except Exception as e:
        logger.warning(f"Backup creation failed: {e}")
        return

    # Delete older backups if exceeding max_backups
    try:
        result = c.run(f"ls -1t {backup_path}/{site_name}_*.tar.gz",
                       hide=True, warn=True)
        files = result.stdout.strip().splitlines()

        if len(files) > max_backups:
            old_files = files[max_backups:]
            for file in old_files:
                c.run(f"rm -f {os.path.join(backup_path, file)}")
                logger.info(f"Removed old backup: {file}")
    except Exception as e:
        logger.warning(f"Could not cleanup old backups: {e}")


def deploy_to_release_folder(c: Connection, config: dict) -> str:
    """
    Move cloned code into a timestamped release folder.

    Creates a symlink named `current` pointing to the latest release.
    Also links the `env` file into the release folder.

    :param c: Fabric connection or context object.
    :param config: Site configuration dictionary.
    :return: Full path to the release folder.
    """
    logger = get_logger(config["name"])

    site_name = config["name"]
    deploy_path = config["deploy_path"]
    releases_path = os.path.join(deploy_path, "releases")

    # Generate release folder name with timestamp (ms precision)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    release_path = os.path.join(releases_path, timestamp)

    # Temporary folder containing newly cloned code
    tmp_path = os.path.join(deploy_path, "__clone_tmp__")

    # Symlink that always points to the current release
    current_symlink = os.path.join(deploy_path, "current")

    # Ensure releases folder exists
    c.run(f"mkdir -p {releases_path}", warn=True)

    # Abort if the release folder already exists
    result = c.run(f"test -e {release_path}", warn=True, hide=True)
    if result.ok:
        logger.error(f"Release path already exists: {release_path}")
        return ""

    # Move __clone_tmp__ into the final release folder
    c.run(f"mv {tmp_path} {release_path}", warn=True)

    # Point the current symlink to this new release
    c.run(f"ln -sfn {release_path} {current_symlink}", warn=True)

    # Link the env file (used for secrets) into the new release
    env_source = os.path.join(deploy_path, "env")
    env_target = os.path.join(release_path, "env")
    c.run(f"ln -sf {env_source} {env_target}", warn=True)
    logger.info(f"env linked: {env_target} -> {env_source}")

    # Log release creation details
    logger.info(f"Release created at: {release_path}")
    logger.info(f"Symlink updated: {current_symlink} -> {release_path}")

    return release_path

def cleanup_old_releases(c: Connection, config: dict, keep: int = 5):
    """
    Delete older releases, keeping only the most recent `keep` ones.

    :param c: Fabric connection object.
    :param config: Site configuration dictionary.
    :param keep: Number of most recent releases to keep.
    """
    # Determine the path where release folders are stored
    releases_path = os.path.join(config["deploy_path"], "releases")

    # Get list of all releases sorted by modification time descending
    result = c.run(f"ls -1dt {releases_path}/*", hide=True, warn=True)
    all_releases = result.stdout.strip().splitlines()

    # If more than `keep` releases exist, delete the oldest ones
    if len(all_releases) > keep:
        to_delete = all_releases[keep:]
        for release in to_delete:
            c.run(f"rm -rf {release}", warn=True)


def rollback_to_previous_release(c: Connection, config: dict) -> None:
    """
    Roll back the `current` symlink to the previous valid release.

    This function is called when a deployment fails to restore the
    previous working state.

    :param c: Fabric connection or context object.
    :param config: Site configuration dictionary.
    """
    logger = get_logger(config['name'])

    # Path to the folder containing release versions
    releases_path = os.path.join(config['deploy_path'], "releases")

    # Symlink path that points to the current active release
    current_symlink = os.path.join(config['deploy_path'], "current")

    # List all release folders (sorted newest first)
    result = c.run(f"ls -1dt {releases_path}/*", hide=True, warn=True)
    all_releases = result.stdout.strip().splitlines()

    # Must have at least two releases to perform rollback
    if len(all_releases) < 2:
        logger.error("No previous release found. Cannot rollback.")
        return

    # Identify the failed and previous successful releases
    failed_release = all_releases[0]
    previous_release = all_releases[1]

    # Update symlink to point to previous release
    logger.warning(f"Rolling back to previous release: {previous_release}")
    c.run(f"ln -sfn {previous_release} {current_symlink}")

    # Remove the failed release folder
    logger.info(f"Removing failed release: {failed_release}")
    c.run(f"rm -rf {failed_release}", warn=True)


def acquire_lock(c: Connection, config: dict) -> str:
    """
    Create a lock file to prevent concurrent deployments.

    The lock file is named `.deploy.lock` and contains session metadata.
    If the lock file already exists, an exception is raised.

    :param c: Fabric connection or context object.
    :param config: Site configuration dictionary.
    :raises Exception: If a lock file is already present.
    :return: A unique lock ID string.
    """
    logger = get_logger(config['name'])

    # Path where the lock file will be created
    deploy_path = config['deploy_path']
    lock_file = os.path.join(deploy_path, ".deploy.lock")

    # Generate a unique ID using timestamp and UUID
    lock_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex}"

    # Detect system user (who initiated the deployment)
    user = getpass.getuser()

    # If lock already exists, abort
    result = c.run(f"test -f {lock_file}", warn=True, hide=True)
    if result.ok:
        logger.error("Deployment is already locked. Another deploy may be in progress.")
        raise Exception("Deploy locked. Use `fab2 unlock --site=...` to force unlock if needed.")

    # Compose informative lock file content
    content = (
        f"Locked at: {datetime.now().isoformat()}\n"
        f"Host: {getattr(c, 'host', 'local')}\n"
        f"User: {user}\n"
        f"Lock ID: {lock_id}\n"
    )

    # Write lock file to remote system
    c.run(f"echo '{content}' > {lock_file}")
    logger.info("Deployment lock acquired.")

    return lock_id


def release_lock(c: Connection, config: dict, lock_id: str) -> None:
    """
    Remove the deployment lock file, if this session owns it.

    The current session will only remove the lock if the stored lock ID
    matches the one originally acquired.

    :param c: Fabric connection or context object.
    :param config: Site configuration dictionary.
    :param lock_id: The lock ID associated with this session.
    """
    logger = get_logger(config['name'])

    # Path to the lock file
    deploy_path = config['deploy_path']
    lock_file = os.path.join(deploy_path, ".deploy.lock")

    # Extract lock ID from file contents
    result = c.run(f"grep '^Lock ID:' {lock_file}", warn=True, hide=True)
    current_id = result.stdout.strip().split(":", 1)[-1].strip() if result.ok else ""
    # Only delete the lock if it matches this session's ID
    if current_id == lock_id:
        c.run(f"rm -f {lock_file}", warn=True)
        logger.info("Deployment lock released.")
    else:
        logger.warning("Lock file not owned by this session. Skipping unlock.")
