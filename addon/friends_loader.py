"""Loads friends' reviews in the background and tracks the load state.

Free of aqt: everything Anki-specific is injected, so it can be tested in plain Python.
"""

import time
from collections.abc import Callable
from concurrent.futures import Future

from . import friends_state as fs
from .errors import AnkiHelperError
from .log import get_logger
from .models import ProfileReviews

log = get_logger()

FriendsResult = dict[str, ProfileReviews | None]
UNEXPECTED_ERROR = "Unexpected error. See user_files/ankihelper.log."


class FriendsLoader:
    """Main-thread owner of the friends data and its fetch/retry state machine."""

    def __init__(
        self,
        *,
        fetch: Callable[[], FriendsResult],
        run_in_background: Callable[[Callable[[], FriendsResult], Callable], object],
        single_shot: Callable[[int, Callable[[], None]], None],
        reload_config: Callable[[], None],
        on_change: Callable[[], None],
        is_active: Callable[[], bool],
        notify: Callable[[str], object],
        warn: Callable[[str], object],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._fetch = fetch
        self._run_in_background = run_in_background
        self._single_shot = single_shot
        self._reload_config = reload_config
        self._on_change = on_change  # data or status changed: redraw
        self._is_active = is_active  # is the Friends tab showing?
        self._notify = notify
        self._warn = warn
        self._clock = clock
        self.reviews: FriendsResult = {}
        self.status = fs.IDLE
        self.attempts = 0
        self._inflight = False
        self._last_fetch = 0.0

    def ensure(self) -> None:
        """Load friends if this session has none yet (or they are stale)."""
        if self._inflight:
            return
        action = fs.action_on_open(self.status, self._clock() - self._last_fetch)
        if action is None:
            return
        if action == "fetch":
            self.status = fs.LOADING
            self.attempts = 0
        self._start(manual=False)

    def refresh_manually(self) -> None:
        if self._inflight:
            return
        self.status = fs.LOADING
        self.attempts = 0
        self._start(manual=True)
        self._on_change()

    def retry_after_error(self) -> None:
        if self.status == fs.ERROR:
            self.status = fs.IDLE
            self.ensure()
            self._on_change()

    def _start(self, *, manual: bool) -> None:
        if self._inflight:
            return
        self._inflight = True
        if self.status != fs.LOADED:
            self.status = fs.LOADING
        self._reload_config()
        self._last_fetch = self._clock()
        self._run_in_background(
            self._fetch, lambda future: self._on_result(future, manual)
        )

    def _on_result(self, future: Future[FriendsResult], manual: bool) -> None:
        self._inflight = False
        try:
            result = future.result()
        except AnkiHelperError as exc:
            log.info("friends fetch failed: %s", exc)
            self._on_failed(exc, str(exc), manual)
            return
        except Exception:
            log.exception("unexpected error fetching friends")
            self._on_failed(None, UNEXPECTED_ERROR, manual)
            return
        self.reviews = result
        self.status = fs.LOADED
        found = sum(1 for v in result.values() if v is not None)
        log.info("loaded reviews for %d/%d friends", found, len(result))
        if manual:
            self._notify(f"AnkiHelper: loaded {found} of {len(result)} friends")
        self._on_change()

    def _on_failed(self, exc: Exception | None, message: str, manual: bool) -> None:
        if manual:
            self._warn(message)
        if self.status == fs.LOADED:
            return  # a background refresh failed; keep showing the old data
        self.attempts += 1
        if fs.should_retry(exc) and not manual:
            # Server down or still waking up: keep trying while Friends is open.
            self._single_shot(fs.RETRY_SECONDS * 1000, self._retry)
        else:
            self.status = fs.ERROR
        self._on_change()

    def _retry(self) -> None:
        if self._is_active() and self.status == fs.LOADING:
            self._start(manual=False)
