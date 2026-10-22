"""Per-account state the controller must forget when the account changes.

Free of aqt so it can be tested in plain Python.
"""

from collections.abc import Callable

from .errors import AnkiHelperError, NetworkError, NotLoggedInError, ServerError
from .stats import TodayStats


def restore_outcome(exc: BaseException | None) -> bool | None:
    """Whether a session restore left us logged in (None: unknown, keep the old value)."""
    if exc is None:
        return True
    if isinstance(exc, NotLoggedInError):
        return False
    if isinstance(exc, (NetworkError, ServerError)):
        return True  # a stored session exists; the backfill retries after sync
    if isinstance(exc, AnkiHelperError):
        return False  # e.g. not configured or client closed: no session
    return None


class AccountState:
    def __init__(self, on_reset: Callable[[], None]) -> None:
        self._on_reset = on_reset
        self.logged_in = False
        self.last_pushed: TodayStats | None = None
        self.generation = 0  # bumped on every reset; tags in-flight work

    def reset(self) -> None:
        """Forget everything tied to the previous account (logout, delete, new login)."""
        self.generation += 1
        self.logged_in = False
        self.last_pushed = None
        self._on_reset()

    def start_session(self) -> None:
        """A fresh login or signup: drop the old account's state, then mark logged in."""
        self.reset()
        self.logged_in = True

    def record_push(self, reading: TodayStats, generation: int | None = None) -> bool:
        """Remember what was pushed, unless the account changed since `generation`."""
        if generation is not None and generation != self.generation:
            return False
        self.last_pushed = reading
        return True
