# ruff: noqa: C901
"""
Module for deployment recipes.

This module provides reusable deployment recipes that can be used to
deploy Python projects using Fabric. It includes functionality for
checking remote systems, updating code, creating backups, and more.

"""
import getpass
import os
import posixpath
import shlex
import uuid
from datetime import UTC, datetime

from fabric2 import Connection
from invoke.context import Context

from fabricator.exceptions.deployer_exceptions import DeployerException
from fabricator.logger import get_logger
from fabricator.runners import DockerRunner

# Default base directory that every deploy_path and backup_path must live in.
DEFAULT_SITES_ROOT = "/var/www/sites"


def _run_sh(
        c: Connection | DockerRunner | Context,
        script: str,
        *args: str,
        **kwargs
    ):
    """
    Run a shell script on the target, passing values as positional args.

    With the Docker runner, a plain command goes through the host shell
    before ``docker exec``, so globs (``*``), pipes or redirections would
    be evaluated on the host instead of inside the container. Wrapping the
    script in ``sh -c`` makes the target's shell evaluate it, and passing
    the values as ``$1``, ``$2``... avoids any quoting issues.

    :param c: Fabric runner or connection object.
    :type c: Union[Connection, DockerRunner, Context]

    :param script: POSIX shell script; refer to the values as "$1", "$2"...
    :type script: str

    :param args: Values for the script's positional parameters.
    :type args: str

    :param kwargs: Extra options for the runner (hide, warn, ...).
    :type kwargs: dict

    :return: Result object from the runner.
    :rtype: Result or None
    """
    quoted_args = " ".join(shlex.quote(arg) for arg in args)
    return c.run(f"sh -c {shlex.quote(script)} sh {quoted_args}", **kwargs)


def _normalize_path(path: str, key: str) -> str:
    """
    Normalize an absolute POSIX path from the config.

    :param path: Path as written in ``sites.yml``.
    :type path: str

    :param key: Config key the path comes from (used in error messages).
    :type key: str

    :raises DeployerException: If the path is not absolute.
    :return: Path with ``.``, ``..`` and duplicated slashes removed.
    :rtype: str
    """
    if not posixpath.isabs(path):
        raise DeployerException(f"'{key}' must be an absolute path: {path}")
    normalized = posixpath.normpath(path)
    # normpath keeps a leading "//" (POSIX allows it); treat it as "/".
    if normalized.startswith("//"):
        normalized = "/" + normalized.lstrip("/")
    return normalized


def _resolve_remote_path(
        c: Connection | DockerRunner | Context,
        path: str
    ) -> str:
    """
    Resolve symlinks of a path on the target, even if it doesn't exist yet.

    Walks up to the deepest existing directory, resolves it with
    ``pwd -P`` and appends the missing components back, so a symlink
    anywhere in the existing part of the path is taken into account.

    :param c: Fabric runner or connection object.
    :type c: Union[Connection, DockerRunner, Context]

    :param path: Normalized absolute path.
    :type path: str

    :raises DeployerException: If the path cannot be resolved.
    :return: Physical path on the target.
    :rtype: str
    """
    script = (
        'p="$1"; rest=""; '
        'while [ ! -d "$p" ] && [ "$p" != "/" ]; do '
        'rest="/$(basename "$p")$rest"; p="$(dirname "$p")"; '
        'done; '
        'printf "%s%s\\n" "$(cd "$p" && pwd -P)" "$rest"'
    )
    result = _run_sh(c, script, path, hide=True, warn=True)
    if not result or result.failed or not result.stdout.strip():
        raise DeployerException(f"Could not resolve path on target: {path}")
    return _normalize_path(result.stdout.strip(), path)


def validate_paths(
        c: Connection | DockerRunner | Context,
        config: dict
    ) -> None:
    """
    Ensure the deploy and backup paths live inside the sites root.

    Every command of a deploy (including ``rm -rf`` on old files) runs
    inside ``deploy_path`` and ``backup_path``, so both must be strictly
    inside ``sites_root`` (default ``/var/www/sites``) once symlinks are
    resolved on the target. Otherwise the deploy is aborted before
    touching anything.

    :param c: Fabric runner or connection object.
    :type c: Union[Connection, DockerRunner, Context]

    :param config: Site configuration dictionary.
    :type config: dict

    :raises DeployerException: If a path is outside the sites root.
    """
    logger = get_logger(config['name'])

    root = _normalize_path(
        config.get("sites_root", DEFAULT_SITES_ROOT), "sites_root"
    )
    real_root = _resolve_remote_path(c, root)
    prefix = real_root.rstrip("/") + "/"

    for key in ("deploy_path", "backup_path"):
        value = config.get(key)
        if not value:
            continue
        real_path = _resolve_remote_path(c, _normalize_path(value, key))
        if not real_path.startswith(prefix):
            msg = (
                f"'{key}' ({value} -> {real_path}) is outside of "
                f"sites_root ({real_root}). Aborting."
            )
            logger.error(msg)
            raise DeployerException(msg)


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
        f"! -name 'shared' ! -name 'backup' "
        f"-exec rm -rf {{}} +",
        warn=True
    )

    # Remove any previous temporary clone if exists
    logger.info("Removing any previous temporary clone...")
    c.run(f"rm -rf {tmp_clone}", warn=True)

    # Create the temporary directory (owned by the deploying user)
    c.run(f"mkdir -p {tmp_clone}")

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

    # Define the shared base path (persistent) and the current release path.
    # original_path is the base deploy dir; deploy_path has been repointed to
    # the new release folder by the time this function is called.
    base_path = config.get('original_path', config['deploy_path'])
    shared_dir = os.path.join(base_path, 'shared')
    release_dir = config['deploy_path']

    # Ensure the shared directory exists
    c.run(f"mkdir -p {shared_dir}")

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
            c.run(f"mkdir -p {shared_subdir}", warn=True)

        # Create or update the symlink in the release directory
        c.run(f"ln -sfn {shared_subdir} {release_subdir}", warn=True)
        logger.info(f"Linked shared directory: {d}")

def venv_sees_system_site_packages(
        c: Connection | DockerRunner | Context,
        venv_dir: str
    ) -> bool:
    """
    Check whether a virtualenv can see the packages of the system.

    The answer is written in `pyvenv.cfg` when the virtualenv is
    created, and it is the only place where it lives.

    :param c: Fabric runner or connection object.
    :type c: Union[Connection, DockerRunner, Context]

    :param venv_dir: Path of the virtualenv.
    :type venv_dir: str

    :return: True if the virtualenv sees the packages of the system.
    :rtype: bool
    """
    result = c.run(
        f"grep -qx 'include-system-site-packages = true' "
        f"{venv_dir}/pyvenv.cfg",
        warn=True,
        hide=True
    )

    return result is not None and result.ok

def ensure_venv(c: Connection | DockerRunner | Context, config: dict) -> None:
    """
    Create the virtual environment of the site if it is missing.

    The interpreter is `python_bin`, so a site can pin the version it
    needs instead of taking whatever `python3` happens to be.

    With `system_site_packages` enabled the virtualenv is created with
    `--system-site-packages`. Some packages cannot come from PyPI and
    are installed in the interpreter of the system — `phpy`, which
    needs a PHP built with `--enable-embed`, is one of them — and only
    a virtualenv created that way can import them.

    A virtualenv that already exists keeps the `pyvenv.cfg` it was
    created with, so enabling the option later would change nothing.
    Running `venv` again over the same folder rewrites that file and
    leaves the packages already installed untouched.

    :param c: Fabric runner or connection object.
    :type c: Union[Connection, DockerRunner, Context]

    :param config: Site configuration dictionary.
    :type config: dict
    """
    logger = get_logger(config['name'])

    venv_path = config.get("venv", "venv")
    deploy_path = config["deploy_path"]
    venv_dir = f"{deploy_path}/{venv_path}"
    python_bin = config.get("python_bin", "python3")
    system_site = bool(config.get("system_site_packages", False))
    flags = " --system-site-packages" if system_site else ""

    # Check if virtualenv exists
    logger.info("Checking if virtualenv exists...")
    result = c.run(f"test -d {venv_dir}", warn=True)
    if result is None or result.failed:
        logger.info(
            f"Creating virtualenv at {venv_dir} with {python_bin}"
        )
        c.run(f"{python_bin} -m venv{flags} {venv_dir}")
        return

    # The virtualenv is there, but it may not be the one the config
    # asks for: only its configuration is fixed, not its packages.
    if not system_site or venv_sees_system_site_packages(c, venv_dir):
        return

    logger.info(
        f"Virtualenv at {venv_dir} cannot see the packages of the "
        f"system, updating its configuration..."
    )
    c.run(f"{python_bin} -m venv{flags} {venv_dir}")

def verify_imports(
        c: Connection | DockerRunner | Context,
        config: dict
    ) -> None:
    """
    Verify that the modules listed in `verify_imports` can be imported.

    A dependency that does not come from `requirements.txt` — one
    provided by the system and reached through `system_site_packages` —
    leaves no trace in the install: everything succeeds and the site
    only fails when a request needs the module, in production.
    Importing it here turns that into a failed deploy, which rolls back
    to the previous release.

    :param c: Fabric runner or connection object.
    :type c: Union[Connection, DockerRunner, Context]

    :param config: Site configuration dictionary.
    :type config: dict

    :raises DeployerException: If a module cannot be imported.
    """
    logger = get_logger(config['name'])

    modules = config.get("verify_imports") or []
    if not modules:
        return

    venv_path = config.get("venv", "venv")
    deploy_path = config["deploy_path"]
    venv_dir = f"{deploy_path}/{venv_path}"

    logger.info(f"Verifying imports: {', '.join(modules)}")

    missing = []
    for module in modules:
        result = c.run(
            f"bash -c 'source {venv_dir}/bin/activate && "
            f"cd {deploy_path} && "
            f"python -c \"import {module}\"'",
            pty=True,
            warn=True,
            hide=True
        )
        if result is None or result.failed:
            missing.append(module)

    if missing:
        msg_raise = (
            f"Cannot import in the virtualenv: {', '.join(missing)}. "
            f"Check 'requirements.txt' and, for the packages provided "
            f"by the system, 'system_site_packages'."
        )
        logger.error(msg_raise)
        raise DeployerException(msg_raise)

    logger.info("All the modules of 'verify_imports' are available.")

def install_deps(c: Connection | DockerRunner | Context, config: dict) -> None:
    """
    Install Python dependencies in a virtual environment.

    Creates the virtualenv if it does not exist and installs packages
    from `requirements.txt` using pip. Afterwards it verifies the
    modules of `verify_imports`, if the site declares any.

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

    ensure_venv(c, config)

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

    verify_imports(c, config)

    # Install Playwright browsers if apigateway_scraper is installed
    logger.info("Checking if apigateway_scraper is installed...")
    scraper_check = c.run(
        f"bash -c 'source {venv_dir}/bin/activate && "
        f"cd {deploy_path} && "
        f"python -c \"import apigateway_scraper; "
        f"print(\\\"apigateway_scraper installed\\\")\"'",
        pty=True,
        warn=True,
        hide=True
    )

    if scraper_check and scraper_check.ok:
        logger.info("Installing Playwright Chromium browser...")
        c.run(
            f"bash -c 'source {venv_dir}/bin/activate && "
            f"cd {deploy_path} && "
            f"PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=1 "
            f"playwright install chromium'",
            pty=True
        )

        # install-deps uses Ubuntu package names. On Debian Trixie
        # (python:3.14) it fails; Chromium libs belong in the image.
        # Do not abort the deploy if this step cannot run.
        logger.info("Installing Playwright system dependencies...")
        deps = c.run(
            f"bash -c 'source {venv_dir}/bin/activate && "
            f"cd {deploy_path} && "
            f"playwright install-deps chromium'",
            pty=True,
            warn=True,
        )
        if deps is None or deps.failed:
            logger.warning(
                "playwright install-deps failed; continuing. "
                "Ensure Chromium libraries are installed in the image."
            )
    else:
        logger.info(
            "apigateway_scraper not found in requirements, "
            "skipping Playwright browser installation."
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
            f"bash -c 'source {deploy_path}/{venv_path}/bin/activate && "
            f"cd {deploy_path} && python manage.py migrate'",
            pty=True
        )

    except DeployerException as e:
        # Handle any exception during migration
        logger.error("Migration execution failed.")
        msg_raise = f"Migration error: {e!s}"
        raise DeployerException(msg_raise) from e

    logger.info("Running Django db_seed...")
    # Execute db_seed command with visible output for debugging
    result = c.run(
        f"bash -c 'source {deploy_path}/{venv_path}/bin/activate && "
        f"cd {deploy_path} && python manage.py db_seed'",
        pty=True,
        warn=True
    )

    # Check if db_seed failed
    if result and result.failed:
        logger.error("db_seed command failed.")
        output = getattr(result, "stdout", "").strip()
        if output:
            logger.error(f"Error output:\n{output}")
        msg_raise = "db_seed execution failed."
        raise DeployerException(msg_raise)

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

def restart_services(
        c: Connection | DockerRunner | Context,
        config: dict
    ) -> None:
    """
    Start or restart services for the project using Procfile configuration.

    Uses the bash script 'start_procfile_supervisord.sh' to handle the
    restart process.
    If the script doesn't exist, logs a warning and continues.

    :param c: Fabric runner or connection object.
    :type c: Union[Connection, DockerRunner, Context]

    :param config: Site configuration dictionary.
    :type config: dict
    """
    logger = get_logger(config['name'])

    # Get site name from config
    site = config['name']

    # Define script path
    procfile_script_path = "/scripts/start_procfile_supervisord.sh"

    # Check if the Procfile script exists
    check_script = c.run(
        f"test -f {procfile_script_path} && echo 'exists' || echo 'not_found'",
        hide=True,
        warn=True
    )
    if check_script is None:
        logger.warning(
            f"Script {procfile_script_path} not found. "
            f"Skipping services restart."
        )
    else:
        script_exists = (
            check_script.stdout.strip() == "exists"
        )

        if not script_exists:
            logger.warning(
                f"Script {procfile_script_path} not found. "
                f"Skipping services restart."
            )
        else:
            logger.info(f"Restarting services for {site}...")

            # Determine if we should restart a specific site or all sites
            if site and site != "all":
                # Restart specific site
                result = c.run(f"{procfile_script_path} {site}", warn=True)
            else:
                # Restart all sites
                result = c.run(f"{procfile_script_path}", warn=True)

            # Check if the command executed successfully
            if result and not result.failed:
                logger.info(
                    f"Services for {site} restarted successfully"
                )
            else:
                logger.error(f"Failed to restart services for {site}")
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

    Supports options for recursion and custom chmod modes. Commands run
    as the deploying user, never with `sudo`.

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
    chmod_mode = config.get("writable_chmod_mode", "775")
    deploy_path = config["deploy_path"]

    # writable_use_sudo was removed: chmod as root follows symlinks that
    # the repository may contain, so it could change files outside the
    # site. Everything under deploy_path belongs to the deploying user.
    if config.get("writable_use_sudo"):
        logger.warning(
            "'writable_use_sudo' is no longer supported and is ignored."
        )

    # Loop through each defined directory
    for directory in writable_dirs:
        # Compose full path and chmod command
        full_path = os.path.join(deploy_path, directory)
        recursive_flag = "-R " if recursive else ""
        chmod_cmd = f"chmod {recursive_flag}{chmod_mode} {full_path}"

        logger.info(f"Setting writable permissions on: {directory}")

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

    # Verify that the symlink points to a valid directory
    target_check = c.run(
        f"test -d {deploy_path}/current && echo 'valid'",
        hide=True, warn=True
    )

    if (not target_check or not target_check.ok or
            target_check.stdout.strip() != 'valid'):
        msg = (
            "Skipping backup: Current symlink points to invalid directory."
        )
        logger.warning(msg)
        return

    logger.info(
        f"Creating backup for site '{site_name}' at {backup_file}"
    )

    # Ensure backup directory exists
    c.run(f"mkdir -p {backup_path}")

    # Create compressed backup using tar
    try:
        # Get the current release name
        result = c.run(
            f"readlink {deploy_path}/current | xargs basename",
            hide=True,
            warn=True
        )
        release_name = result.stdout.strip() if result and result.ok else ""

        if not release_name:
            logger.warning(
                "Could not determine current release name. "
                "Skipping backup."
            )
            return

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
        result = _run_sh(
            c, 'ls -1t -- "$1"/"$2"_*.tar.gz', backup_path, site_name,
            hide=True, warn=True
        )

        # Initialize files as an empty list by default
        files: list[str] = []

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
    c.run(f"mkdir -p {releases_path}", warn=True)

    # Abort if the release folder already exists
    result = c.run(f"test -e {release_path}", warn=True, hide=True)
    if result and result.ok:
        logger.error(f"Release path already exists: {release_path}")
        return ""

    # Move __clone_tmp__ into the final release folder
    result = c.run(f"mv {tmp_path} {release_path}", warn=True)
    if not result or result.failed:
        msg = f"Could not move {tmp_path} to {release_path}"
        logger.error(msg)
        raise DeployerException(msg)

    # Link the env file only if not managed by shared_files
    if "env" not in config.get("shared_files", []):
        env_source = os.path.join(deploy_path, "env")
        env_target = os.path.join(release_path, "env")
        c.run(f"ln -sf {env_source} {env_target}", warn=True)
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
    result = _run_sh(
        c, 'ls -1dt -- "$1"/*', releases_path, hide=True, warn=True
    )

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
    result = _run_sh(
        c, 'ls -1dt -- "$1"/*', releases_path, warn=True, hide=True
    )

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
        # Create the deploy directory as the deploying user (no sudo)
        logger.info(f"Creating deploy directory: {deploy_path}")
        result = c.run(f"mkdir -p {deploy_path}", warn=True)
        if not result or result.failed:
            msg = (
                f"Could not create {deploy_path}. Create it beforehand "
                f"and make the deploying user its owner."
            )
            logger.error(msg)
            raise DeployerException(msg)
        c.run(f"chmod 775 {deploy_path}", warn=True)

    # Path to the lock file
    lock_file = os.path.join(deploy_path, ".deploy.lock")

    # Generate a unique ID using timestamp and UUID
    lock_id = f"{datetime.now(UTC)
                 .strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex}"

    # Detect system user (who initiated the deployment)
    user = getpass.getuser()

    # Compose informative lock file content
    content = (
        f"Locked at: {datetime.now(UTC).isoformat()}\n"
        f"Host: {getattr(c, 'host', 'local')}\n"
        f"User: {user}\n"
        f"Lock ID: {lock_id}\n"
    )

    # Create and write the lock file in a single atomic step: with
    # noclobber (set -C) the redirection fails if the file already exists,
    # so two simultaneous deploys can't both acquire the lock.
    result = _run_sh(
        c, 'set -C; printf "%s" "$1" > "$2"', content, lock_file,
        warn=True, hide=True
    )
    if not result or result.failed:
        exists = c.run(f"test -f {lock_file}", warn=True, hide=True)
        if exists and exists.ok:
            msg = "Deployment is already locked. Another deploy in progress."
            logger.error(msg)
            msg = "Deploy locked. Use `fab2 unlock --site=...` "
            msg += "to force unlock if needed."
            raise DeployerException(msg)
        msg = f"Could not create lock file {lock_file}"
        logger.error(msg)
        raise DeployerException(msg)

    # Set appropriate permissions 664 allows group read/write
    c.run(f"chmod 664 {lock_file}", warn=True)
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
