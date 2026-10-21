from datetime import date, datetime

import pytest

from addon.history_logic import (
    EMPTY_WARNING,
    SYNC_FIRST_NOTE,
    has_reviews,
    manual_confirmation,
    result_message,
    should_auto_backfill,
    today_row,
    typed_confirmation_ok,
)
from addon.models import HistoryResult, HistoryStatus, ReviewDay, YearReviews
from addon.stats import TodayStats

NEVER = HistoryStatus(None)
DONE = HistoryStatus(datetime(2026, 1, 31))


def year(*totals: int) -> YearReviews:
    days = [ReviewDay(date(2026, 1, 1 + i), n) for i, n in enumerate(totals)]
    return YearReviews("you", date(2025, 2, 1), date(2026, 1, 31), days)


@pytest.mark.parametrize(
    ("status", "done", "expected"),
    [
        (NEVER, False, True),
        (NEVER, True, False),
        (DONE, False, False),
        (DONE, True, False),
        (None, False, False),
    ],
)
def test_should_auto_backfill(status, done, expected):
    assert should_auto_backfill(status, done) is expected


@pytest.mark.parametrize(
    ("totals", "expected"),
    [((), False), ((0, 0), False), ((0, 3), True), ((5,), True)],
)
def test_has_reviews(totals, expected):
    assert has_reviews(year(*totals)) is expected


def test_manual_confirmation_basic():
    text = manual_confirmation(year(4), synced_this_session=True)
    assert "replaces the last year" in text
    assert "groups will see" in text
    assert SYNC_FIRST_NOTE not in text
    assert EMPTY_WARNING not in text


def test_manual_confirmation_asks_to_sync_first():
    text = manual_confirmation(year(4), synced_this_session=False)
    assert SYNC_FIRST_NOTE in text
    assert EMPTY_WARNING not in text


def test_manual_confirmation_warns_on_empty_year():
    text = manual_confirmation(year(0), synced_this_session=True)
    assert EMPTY_WARNING in text
    assert SYNC_FIRST_NOTE not in text


def test_manual_confirmation_both_notes():
    text = manual_confirmation(year(), synced_this_session=False)
    assert SYNC_FIRST_NOTE in text and EMPTY_WARNING in text


@pytest.mark.parametrize(("n", "unit"), [(213, "days"), (1, "day"), (0, "days")])
def test_result_message(n, unit):
    result = HistoryResult(date(2025, 2, 1), date(2026, 1, 31), n, datetime(2026, 1, 31))
    assert result_message(result) == f"AnkiHelper: uploaded {n} {unit} of review history."


def test_today_row_matches_the_end_day():
    h = YearReviews(
        "you",
        date(2026, 1, 1),
        date(2026, 1, 3),
        [ReviewDay(date(2026, 1, 2), 9), ReviewDay(date(2026, 1, 3), 4, 80.0, 6.4)],
    )
    row = today_row(h)
    assert row == TodayStats(date(2026, 1, 3), 4, 80.0, 6)


def test_today_row_none_without_a_row_for_the_end_day():
    h = YearReviews("you", date(2026, 1, 1), date(2026, 1, 3), [ReviewDay(date(2026, 1, 2), 9)])
    assert today_row(h) is None
    assert today_row(YearReviews("you", date(2026, 1, 1), date(2026, 1, 3))) is None


@pytest.mark.parametrize(
    ("typed", "ok"),
    [("upload", True), ("  Upload ", True), ("UPLOAD", True), ("", False), ("upload now", False)],
)
def test_typed_confirmation_ok(typed, ok):
    assert typed_confirmation_ok(typed) is ok
