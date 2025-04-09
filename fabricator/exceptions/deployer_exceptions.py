"""Exception class for Fabricator Deployer."""

class DeployerException(Exception):

    """Exception class for Fabricator Deployer."""

    def __init__(self, message, code = None, params = None):
        """
        Initialize the DeployerException.

        Stores the error message, optional code, and any additional
        parameters passed for debugging or context.

        :param message: Description of the error.
        :type message: str

        :param code: Optional error code.
        :type code: int or str, optional

        :param params: Optional error context or data.
        :type params: dict or any, optional
        """
        self.message = message
        self.code = code
        self.params = params
        super().__init__(message)

    def __str__(self):
        """
        Return a formatted string representation of the error.

        If a code is provided, it is included in the output. Otherwise,
        only the message is shown.

        :return: A string representing the error.
        :rtype: str
        """
        if self.code is not None:
            return f"[Fabricator Deployer] Error {self.code}: {self.message}"
        else:
            return f"[Fabricator Deployer] {self.message}"
