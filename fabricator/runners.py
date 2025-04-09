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
    Executes shell commands inside a Docker container using Fabric.

    The ``DockerRunner`` class wraps a Fabric ``Context`` or ``Connection``
    to run commands via ``docker exec``, allowing integration with both
    local and remote execution environments.

    :param container_name: Name of the Docker container where commands
                        should be run.
    :type container_name: str

    :param inner_runner: The Fabric runner (local or remote).
    :type inner_runner: Union[Context, Connection]

    :param user: User to run commands as (default is "root").
    :type user: str

    :ivar container_name: Target container name.
    :ivar inner_runner: Underlying runner used to execute commands.
    :ivar user: User to run commands as inside the container.
    """

    def __init__(
            self,
            container_name: str,
            inner_runner: Context | Connection,
            user: str = "root"
        ):
        """
        Initialize a new DockerRunner instance.

        Sets internal attributes for later use in command execution via
        ``docker exec``.

        :param container_name: Docker container name.
        :type container_name: str

        :param inner_runner: Base Fabric runner (Context or Connection).
        :type inner_runner: Union[Context, Connection]

        :param user: Optional user to execute commands as (default: "root").
        :type user: str
        """
        # Store container name for Docker exec
        self.container_name = container_name

        # Runner used to execute the actual command
        self.inner_runner = inner_runner

        # Default user is root unless otherwise specified
        self.user = user

    def run(self, command: str, **kwargs) -> Result | None:
        """
        Execute a command inside the Docker container.

        Wraps the given command with ``docker exec`` and delegates execution
        to the inner Fabric runner.

        :param command: Shell command to run inside the container.
        :type command: str

        :param kwargs: Extra arguments for the Fabric runner
        (e.g., hide, warn).
        :type kwargs: dict

        :return: Result object from the Fabric runner.
        :rtype: Result or None
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
        Run a command with ``sudo`` inside the Docker container.

        This method prepends ``sudo`` to the command and runs it via
        ``docker exec``.

        :param command: Shell command to run with elevated privileges.
        :type command: str

        :param kwargs: Extra options passed to the Fabric runner.
        :type kwargs: dict

        :return: Result object from the command execution.
        :rtype: Result or None
        """
        return self.run(f"sudo {command}", **kwargs)

    def cd(self, path: str):
        """
        Change directory within the Docker container context.

        This returns the context manager from the inner Fabric runner.

        :param path: Directory path to switch to.
        :type path: str

        :return: A context manager for changing directories.
        :rtype: ContextManager
        """
        return self.inner_runner.cd(path)
