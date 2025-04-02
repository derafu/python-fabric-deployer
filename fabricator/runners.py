"""
Module for different deployment runners.

This module provides different deployment runners that can be used to
deploy Python projects using Fabric. It includes functionality for
local, SSH, and Docker runners.

"""

from typing import Protocol

from fabric2 import Connection
from invoke.context import Context
from invoke.runners import Result


class Runner(Protocol):

    """
    Protocol to define a standard interface for runners.

    All runners must implement `run`, `sudo`, and `cd` methods.
    """

    def run(self, command: str, **kwargs) -> Result | None:
        """Run a shell command."""

    def sudo(self, command: str, **kwargs) -> Result | None:
        """Run a command with elevated privileges."""

    def cd(self, path: str):
        """Change the current working directory."""


class LocalRunner(Context):

    """
    Local runner based on `invoke.Context`.

    Used when running deployment commands locally.
    """

    pass

class SSHRunner(Connection):

    """
    SSH runner based on `fabric.Connection`.

    Used for remote deployment via SSH.
    """

    pass

class DockerRunner:

    """
    Docker-based runner to execute commands inside a container.

    :param container_name: Name of the Docker container.
    :param inner_runner: Fabric Context or Connection to wrap.
    :param user: User to run commands as inside the container.
    """

    def __init__(self, container_name: str,
                 inner_runner: Context | Connection, user: str = "root"):
        """
        Initialize a new DockerRunner instance.

        :param container_name: Name of the Docker container to execute
        commands in.
        :param inner_runner: The underlying runner (Context or Connection)
        to use.
        :param user: User to run commands as inside the container
        (default: root).
        """
        # Store container name for Docker exec
        self.container_name = container_name

        # Runner used to execute the actual command
        self.inner_runner = inner_runner

        # Default user is root unless otherwise specified
        self.user = user

    def run(self, command: str, **kwargs) -> Result | None:
        """
        Run a command inside the Docker container.

        :param command: Command string to execute.
        :param kwargs: Additional options for the runner.
        :return: Result of the executed command.
        """
        # Format docker exec command
        full_cmd = (
            f"docker exec -u {self.user} "
            f"{self.container_name} "
            f"{command}"
        )

        # Run the command via inner runner and return its result directly
        return self.inner_runner.run(full_cmd, **kwargs)

    def sudo(self, command: str, **kwargs) -> Result | None:
        """
        Run a command as root via `sudo` inside the container.

        :param command: Command string to execute.
        :param kwargs: Additional options for the runner.
        :return: Result of the executed command.
        """
        return self.run(f"sudo {command}", **kwargs)

    def cd(self, path: str):
        """
        Change directory inside the container context.

        :param path: Directory path.
        :return: Context manager for directory change.
        """
        return self.inner_runner.cd(path)
