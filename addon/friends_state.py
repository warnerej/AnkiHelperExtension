"""Pure decisions for lazily loading friends (kept free of aqt for testing)."""

from .errors import NetworkError, ServerError

IDLE = "idle"
LOADING = "loading"
LOADED = "loaded"
ERROR = "error"

REFRESH_SECONDS = 300
RETRY_SECONDS = 15


def action_on_open(status: str, age: float) -> str | None:
    """What to do when the Friends tab is shown: 'fetch', 'refresh' or None."""
    # LOADING counts too: a retry may have lapsed while another tab was showing.
    if status in (IDLE, ERROR, LOADING):
        return "fetch"
    if status == LOADED and age >= REFRESH_SECONDS:
        return "refresh"
    return None


def should_retry(exc: Exception | None) -> bool:
    """Keep trying while the server is down or waking up; bad config or
    credentials will not fix themselves, so those stop at once."""
    return exc is None or isinstance(exc, (NetworkError, ServerError))
