class AnkiHelperError(Exception):
    """Base class for all add-on errors; messages are safe to show to the user."""


class NotConfiguredError(AnkiHelperError):
    """base_url, email or password is missing or invalid."""


class AuthError(AnkiHelperError):
    """Credentials rejected, or the session could not be recovered."""


class NetworkError(AnkiHelperError):
    """Timeout or connection failure."""


class ServerError(AnkiHelperError):
    """The backend returned a 5xx or an unexpected response."""


class ApiError(AnkiHelperError):
    """The backend returned a 4xx; carries the status and its `detail`."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(f"{status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail
