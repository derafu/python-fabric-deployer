from typing import Protocol
from invoke import Context
from fabric2 import Connection


class Runner(Protocol):
    """
    Protocol to define a standard interface for runners.

    All runners must implement `run`, `sudo`, and `cd` methods.
    """

    def run(self, command: str, **kwargs): ...
    def sudo(self, command: str, **kwargs): ...
    def cd(self, path: str): ...

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
                 inner_runner: Context, user: str = None):
        # Store container name for Docker exec
        self.container_name = container_name

        # Runner used to execute the actual command
        self.inner_runner = inner_runner

        # Default user is root unless otherwise specified
        self.user = user or "root"

    def run(self, command: str, **kwargs):
        """
        Run a command inside the Docker container.

        :param command: Command string to execute.
        :param kwargs: Additional options for the runner.
        :return: Result of the executed command.
        """
        # Format docker exec command
        full_cmd = f"docker exec -u {self.user} {self.container_name} {command}"

        # Run the command via inner runner (Context or Connection)
        return self.inner_runner.run(full_cmd, **kwargs)

    def sudo(self, command: str, **kwargs):
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
