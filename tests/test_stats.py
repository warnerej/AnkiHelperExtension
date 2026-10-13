from datetime import date, datetime
from types import SimpleNamespace

import pytest
from anki.collection import Collection

from addon.stats import DAY_SECONDS, TodayStats, today_stats


@pytest.fixture
def col(tmp_path):
    c = Collection(str(tmp_path / "c.anki2"))
    yield c
    c.close()


def add_review(
    col, ms: int, ease: int = 3, type_: int = 1, time_ms: int = 1000
) -> None:
    col.db.execute(
        "insert into revlog values (?, 1, -1, ?, 1, 0, 2500, ?, ?)",
        ms,
        ease,
        time_ms,
        type_,
    )


def test_counts_only_current_day_and_real_reviews(col):
    cutoff = col.sched.day_cutoff
    start_ms = (cutoff - DAY_SECONDS) * 1000
    add_review(col, start_ms - 1000)  # previous day
    add_review(col, start_ms + 1000)
    add_review(col, start_ms + 2000)
    add_review(col, start_ms + 3000, ease=0)  # manual reschedule
    assert today_stats(col).total_reviews == 2


def test_study_date_follows_rollover():
    # Next day starts 04:00 on 2026-02-02, so the current Anki day is 2026-02-01.
    cutoff = int(datetime(2026, 2, 2, 4, 0).timestamp())
    fake = SimpleNamespace(
        sched=SimpleNamespace(day_cutoff=cutoff),
        db=SimpleNamespace(first=lambda *_a: [0, None, None, None]),
    )
    assert today_stats(fake) == TodayStats(date(2026, 2, 1), 0, None, 0)


def test_retention_counts_only_review_type_answers(col):
    start = (col.sched.day_cutoff - DAY_SECONDS) * 1000
    add_review(col, start + 1000, ease=3)
    add_review(col, start + 2000, ease=1)
    add_review(col, start + 3000, ease=4)
    add_review(col, start + 4000, ease=3)
    add_review(col, start + 5000, ease=1, type_=0)  # learning: excluded
    stats = today_stats(col)
    assert stats.total_reviews == 5
    assert stats.retention == 75.0


def test_no_review_type_answers_gives_no_retention(col):
    start = (col.sched.day_cutoff - DAY_SECONDS) * 1000
    add_review(col, start + 1000, type_=0)
    assert today_stats(col).retention is None


@pytest.mark.parametrize(("time_ms", "minutes"), [(90_000, 2), (80_000, 1), (0, 0)])
def test_study_minutes_rounded(col, time_ms, minutes):
    start = (col.sched.day_cutoff - DAY_SECONDS) * 1000
    add_review(col, start + 1000, time_ms=time_ms)
    assert today_stats(col).study_minutes == minutes
