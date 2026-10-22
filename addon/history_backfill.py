"""Sequencing for the automatic review-history backfill.

Free of aqt: everything Anki-specific is injected, so it can be tested in plain Python.
The server replaces the uploaded range, so this never uploads from a collection that
has not synced yet or that has no reviews in the year.
"""

import time
from collections.abc import Callable
from concurrent.futures import Future

from .errors import AnkiHelperError
from .history_logic import has_reviews, result_message, should_auto_backfill, today_row
from .log import get_logger
from .models import HistoryResult, HistoryStatus, YearReviews
from .stats import YEAR_DAYS, TodayStats

log = get_logger()

CATCH_UP_DAYS = 7
CATCH_UP_MIN_SECONDS = 15 * 60  # stay well inside the backend's per-hour upload limit


class HistoryBackfiller:
    """Main-thread owner of the once-per-account backfill. Failures stay silent
    (logged only) and leave it pending, so it is tried again next session."""

    def __init__(
        self,
        *,
        is_logged_in: Callable[[], bool],
        uses_sync: Callable[[], bool],
        synced_this_session: Callable[[], bool],
        fetch_status: Callable[[], HistoryStatus],
        read_year: Callable[
            [int, Callable[[YearReviews], None], Callable[[Exception], None]], object
        ],
        upload: Callable[[YearReviews], HistoryResult],
        run_in_background: Callable[[Callable[[], object], Callable], object],
        notify: Callable[[str], object],
        mark_groups_stale: Callable[[], None],
        set_last_pushed: Callable[[TodayStats], object],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._is_logged_in = is_logged_in
        self._uses_sync = uses_sync  # is AnkiWeb sync set up for this profile?
        self._synced = synced_this_session
        self._fetch_status = fetch_status
        self._read_year = (
            read_year  # (days, success, failure): collection read via QueryOp
        )
        self._upload = upload
        self._run_in_background = run_in_background
        self._notify = notify
        self._mark_groups_stale = mark_groups_stale
        self._set_last_pushed = set_last_pushed
        self._clock = clock
        self.done = False  # the full-year backfill ran this session
        self._server_has_history = False  # the server's flag is set: only catch up
        self._last_upload: float | None = None
        self._inflight = False
        self._generation = 0

    def reset(self) -> None:
        """Forget this session's progress (logout, account deleted); drop anything in flight."""
        self._generation += 1
        self.done = False
        self._server_has_history = False
        self._last_upload = None
        self._inflight = False

    def maybe_run(self, *, after_sync: bool = False) -> None:
        """Backfill the year if the server has none; after a sync, otherwise catch up
        on the last few days (reviews done on other devices arrive with the sync)."""
        if self._inflight or not self._is_logged_in():
            return
        if self._server_has_history:
            if after_sync:
                self._catch_up()
            return
        if self._uses_sync() and not self._synced():
            return  # called again from the sync-finished hook
        self._inflight = True
        generation = self._generation
        self._run_in_background(
            self._fetch_status,
            lambda future: self._on_status(future, after_sync, generation),
        )

    def _on_status(
        self, future: Future[HistoryStatus], after_sync: bool, generation: int
    ) -> None:
        if generation != self._generation:
            return
        try:
            status = future.result()
        except Exception as exc:
            self._fail("history status", exc)
            return
        if not should_auto_backfill(status, self.done):
            self._inflight = False
            if status.history_synced_at is not None:
                self._server_has_history = True
                if after_sync:
                    self._catch_up()
            return
        self._read_year(
            YEAR_DAYS,
            lambda history: self._on_year(history, generation),
            lambda exc: self._fail("reading the collection", exc, generation),
        )

    def _on_year(self, history: YearReviews, generation: int) -> None:
        if generation != self._generation:
            return
        if not has_reviews(history):
            log.info("history backfill skipped: no reviews in the last year")
            self._inflight = False
            return
        self._last_upload = self._clock()
        self._run_in_background(
            lambda: self._upload(history),
            lambda future: self._on_uploaded(future, history, generation),
        )

    def _on_uploaded(
        self, future: Future[HistoryResult], history: YearReviews, generation: int
    ) -> None:
        if generation != self._generation:
            return
        try:
            result = future.result()
        except Exception as exc:
            self._fail("history upload", exc)
            return
        self._inflight = False
        self.done = True
        self._server_has_history = True
        log.info("history backfill uploaded %d days", result.days_written)
        self._notify(result_message(result))
        self._mark_groups_stale()
        row = today_row(history)
        if row is not None:
            self._set_last_pushed(row)

    def _catch_up(self) -> None:
        """Re-upload the last CATCH_UP_DAYS days. Silent, throttled, skipped when empty.

        Only runs once the server already holds history (the upload would otherwise
        mark the account as backfilled with just a week of data).
        """
        now = self._clock()
        if (
            self._last_upload is not None
            and now - self._last_upload < CATCH_UP_MIN_SECONDS
        ):
            return
        self._inflight = True
        generation = self._generation
        self._read_year(
            CATCH_UP_DAYS,
            lambda history: self._on_catch_up_year(history, generation),
            lambda exc: self._fail("reading the collection", exc, generation),
        )

    def _on_catch_up_year(self, history: YearReviews, generation: int) -> None:
        if generation != self._generation:
            return
        if not has_reviews(history):
            self._inflight = False
            return
        self._last_upload = self._clock()
        self._run_in_background(
            lambda: self._upload(history),
            lambda future: self._on_caught_up(future, history, generation),
        )

    def _on_caught_up(
        self, future: Future[HistoryResult], history: YearReviews, generation: int
    ) -> None:
        if generation != self._generation:
            return
        try:
            result = future.result()
        except Exception as exc:
            self._fail("history catch-up", exc)
            return
        self._inflight = False
        log.info("history catch-up uploaded %d days", result.days_written)
        self._mark_groups_stale()
        row = today_row(history)
        if row is not None:
            self._set_last_pushed(row)

    def _fail(self, what: str, exc: Exception, generation: int | None = None) -> None:
        if generation is not None and generation != self._generation:
            return
        self._inflight = False
        if isinstance(exc, AnkiHelperError):
            log.info("history backfill: %s failed: %s", what, exc)
        else:
            log.error("history backfill: unexpected error in %s", what, exc_info=exc)
