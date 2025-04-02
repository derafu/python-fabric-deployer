"""Exception class for Fabricator Deployer."""

class DeployerException(Exception):

    """Exception class for Fabricator Deployer."""

    def __init__(self, message, code = None, params = None):
        """
        Initialize the DeployerException.

        :param message: The error message.
        :param code: The error code.
        :param params: The error parameters.
        """
        self.message = message
        self.code = code
        self.params = params
        super().__init__(message)

    def __str__(self):
        """
        Return a string representation of the error.

        :return: A string representation of the error.
        :rtype: str
        """
        if self.code is not None:
            return f"[Fabricator Deployer] Error {self.code}: {self.message}"
        else:
            return f"[Fabricator Deployer] {self.message}"
