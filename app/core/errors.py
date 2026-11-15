class UserError(Exception):
    """A problem the user can fix. The message is shown to them as written."""

    def __init__(self, message: str, status: int = 400, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.retry_after = retry_after  # seconds; sent as the Retry-After header


def busy(message: str, retry_after: int) -> UserError:
    return UserError(message, 429, retry_after)
