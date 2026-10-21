from concurrent.futures import Future
from datetime import date, datetime

from addon.errors import FeatureUnavailableError, NetworkError, ServerError
from addon.history_backfill import (
    CATCH_UP_DAYS,
    CATCH_UP_MIN_SECONDS,
    HistoryBackfiller,
)
from addon.models import HistoryResult, HistoryStatus, ReviewDay, YearReviews
from addon.stats import TodayStats

TODAY = date(2026, 1, 31)
NEVER = HistoryStatus(None)
DONE = HistoryStatus(datetime(2026, 1, 1))
RESULT = HistoryResult(date(2025, 2, 1), TODAY, 2, datetime(2026, 1, 31))
FULL = YearReviews(
    "you", date(2025, 2, 1), TODAY, [ReviewDay(date(2026, 1, 30), 5), ReviewDay(TODAY, 3, 90.0, 4)]
)
EMPTY = YearReviews("you", date(2025, 2, 1), TODAY)


class Harness:
    def __init__(
        self,
        *,
        logged_in=True,
        uses_sync=False,
        synced=False,
        status: HistoryStatus | Exception = NEVER,
        year: YearReviews | Exception = FULL,
        upload_result: HistoryResult | Exception = RESULT,
    ) -> None:
        self.logged_in = logged_in
        self.uses_sync = uses_sync
        self.synced = synced
        self.status = status  # a HistoryStatus, or an exception to raise
        self.year = year
        self.upload_result = upload_result
        self.jobs: list = []
        self.uploads: list[YearReviews] = []
        self.status_calls = 0
        self.notes: list[str] = []
        self.stale = 0
        self.pushed: list[TodayStats] = []
        self.read_days: list[int] = []
        self.now = 1000.0
        self.backfill = HistoryBackfiller(
            is_logged_in=lambda: self.logged_in,
            uses_sync=lambda: self.uses_sync,
            synced_this_session=lambda: self.synced,
            fetch_status=self._fetch_status,
            read_year=self._read_year,
            upload=self._upload,
            run_in_background=lambda task, done: self.jobs.append((task, done)),
            notify=self.notes.append,
            mark_groups_stale=self._stale,
            set_last_pushed=self.pushed.append,
            clock=lambda: self.now,
        )

    def _fetch_status(self):
        self.status_calls += 1
        if isinstance(self.status, Exception):
            raise self.status
        return self.status

    def _read_year(self, days, success, failure):
        self.read_days.append(days)
        if isinstance(self.year, Exception):
            failure(self.year)
        else:
            success(self.year)

    def _upload(self, history):
        self.uploads.append(history)
        if isinstance(self.upload_result, Exception):
            raise self.upload_result
        return self.upload_result

    def _stale(self):
        self.stale += 1

    def run_jobs(self) -> None:
        while self.jobs:
            task, done = self.jobs.pop(0)
            future: Future = Future()
            try:
                future.set_result(task())
            except Exception as exc:
                future.set_exception(exc)
            done(future)


def test_uploads_once_and_updates_everything():
    h = Harness()
    h.backfill.maybe_run()
    h.run_jobs()
    assert h.uploads == [FULL]
    assert h.backfill.done
    assert h.notes == ["AnkiHelper: uploaded 2 days of review history."]
    assert h.stale == 1
    assert h.pushed == [TodayStats(TODAY, 3, 90.0, 4)]
    h.backfill.maybe_run()
    h.run_jobs()
    assert h.status_calls == 1  # done: nothing more this session


def test_waits_for_sync_when_the_profile_uses_it():
    h = Harness(uses_sync=True)
    h.backfill.maybe_run()
    assert h.jobs == [] and h.status_calls == 0
    h.synced = True
    h.backfill.maybe_run()
    h.run_jobs()
    assert h.uploads == [FULL]


def test_runs_immediately_without_ankiweb_sync():
    h = Harness(uses_sync=False, synced=False)
    h.backfill.maybe_run()
    h.run_jobs()
    assert h.uploads == [FULL]


def test_does_nothing_when_logged_out():
    h = Harness(logged_in=False)
    h.backfill.maybe_run()
    assert h.jobs == []


def test_non_null_flag_skips_the_upload_and_stays_quiet():
    h = Harness(status=DONE)
    h.backfill.maybe_run()
    h.run_jobs()
    assert h.uploads == [] and h.notes == []
    assert not h.backfill.done  # flag is server-side; re-checked next session


def test_empty_year_is_not_uploaded_and_stays_pending():
    h = Harness(year=EMPTY)
    h.backfill.maybe_run()
    h.run_jobs()
    assert h.uploads == [] and h.notes == [] and h.stale == 0
    assert not h.backfill.done
    # Not marked done, so a later call (e.g. after a sync) tries again.
    h.year = FULL
    h.backfill.maybe_run()
    h.run_jobs()
    assert h.uploads == [FULL]


def test_errors_are_silent_and_leave_it_pending():
    for exc in (FeatureUnavailableError("old"), NetworkError("x"), ServerError("y"), ValueError("z")):
        h = Harness(status=exc)
        h.backfill.maybe_run()
        h.run_jobs()
        assert h.notes == [] and not h.backfill.done
        h.status = NEVER  # recovered: retried on the next call
        h.backfill.maybe_run()
        h.run_jobs()
        assert h.backfill.done
    h = Harness(upload_result=NetworkError("down"))
    h.backfill.maybe_run()
    h.run_jobs()
    assert h.notes == [] and h.stale == 0 and not h.backfill.done
    h = Harness(year=RuntimeError("no collection"))
    h.backfill.maybe_run()
    h.run_jobs()
    assert h.uploads == [] and not h.backfill.done


def test_second_call_while_in_flight_does_nothing():
    h = Harness()
    h.backfill.maybe_run()
    h.backfill.maybe_run()
    assert len(h.jobs) == 1
    h.run_jobs()
    assert h.status_calls == 1 and len(h.uploads) == 1


def test_reset_ignores_work_in_flight_and_allows_a_rerun():
    h = Harness()
    h.backfill.maybe_run()
    h.backfill.reset()  # e.g. logged out while the status call was running
    h.run_jobs()
    assert h.uploads == [] and not h.backfill.done
    h.backfill.maybe_run()
    h.run_jobs()
    assert h.uploads == [FULL]


def after_sync_run(h: Harness) -> None:
    h.backfill.maybe_run(after_sync=True)
    h.run_jobs()


def test_catch_up_after_sync_when_the_server_already_has_history():
    h = Harness(status=DONE, uses_sync=True, synced=True)
    after_sync_run(h)
    assert h.read_days == [CATCH_UP_DAYS]
    assert h.uploads == [FULL]
    assert h.notes == []  # silent
    assert h.stale == 1
    assert h.pushed == [TodayStats(TODAY, 3, 90.0, 4)]


def test_no_catch_up_without_a_sync_trigger():
    h = Harness(status=DONE)
    h.backfill.maybe_run()
    h.run_jobs()
    assert h.read_days == [] and h.uploads == []


def test_catch_up_never_runs_before_the_full_backfill():
    h = Harness(status=NEVER, uses_sync=True, synced=True)
    after_sync_run(h)
    assert h.read_days == [365]  # the year, not the week
    assert len(h.uploads) == 1


def test_catch_up_skipped_when_backfill_just_ran_then_allowed_later():
    h = Harness(uses_sync=True, synced=True)
    after_sync_run(h)
    assert h.read_days == [365] and len(h.uploads) == 1
    after_sync_run(h)  # a minute-ish later: throttled
    assert h.read_days == [365] and len(h.uploads) == 1
    h.now += CATCH_UP_MIN_SECONDS
    after_sync_run(h)
    assert h.read_days == [365, CATCH_UP_DAYS] and len(h.uploads) == 2


def test_catch_up_is_throttled_to_one_per_15_minutes():
    h = Harness(status=DONE)
    after_sync_run(h)
    h.now += CATCH_UP_MIN_SECONDS - 1
    after_sync_run(h)
    assert len(h.uploads) == 1
    h.now += 1
    after_sync_run(h)
    assert len(h.uploads) == 2
    assert h.status_calls == 1  # the flag is remembered; no repeat lookups


def test_catch_up_with_no_recent_reviews_uploads_nothing():
    h = Harness(status=DONE, year=EMPTY)
    after_sync_run(h)
    assert h.uploads == [] and h.notes == []
    after_sync_run(h)  # not throttled: nothing was sent
    assert h.read_days == [CATCH_UP_DAYS, CATCH_UP_DAYS]


def test_catch_up_errors_are_silent_and_retried_later():
    h = Harness(status=DONE, upload_result=NetworkError("down"))
    after_sync_run(h)
    assert h.notes == [] and h.stale == 0
    h.upload_result = RESULT
    h.now += CATCH_UP_MIN_SECONDS
    after_sync_run(h)
    assert h.stale == 1
    h2 = Harness(status=DONE, year=RuntimeError("no collection"))
    after_sync_run(h2)
    assert h2.uploads == []


def test_reset_forgets_that_the_server_has_history():
    h = Harness(status=DONE)
    after_sync_run(h)
    h.backfill.reset()
    h.status = NEVER  # a different account
    after_sync_run(h)
    assert h.read_days[-1] == 365
