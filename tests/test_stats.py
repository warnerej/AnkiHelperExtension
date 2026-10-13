from datetime import date, datetime
from types import SimpleNamespace

import pytest
from anki.collection import Collection

from addon.stats import DAY_SECONDS, today_review_count


@pytest.fixture
def col(tmp_path):
    c = Collection(str(tmp_path / "c.anki2"))
    yield c
    c.close()


def add_review(col, ms: int, ease: int = 3) -> None:
    col.db.execute(
        "insert into revlog values (?, 1, -1, ?, 1, 0, 2500, 1000, 1)", ms, ease
    )


def test_counts_only_current_day_and_real_reviews(col):
    cutoff = col.sched.day_cutoff
    start_ms = (cutoff - DAY_SECONDS) * 1000
    add_review(col, start_ms - 1000)  # previous day
    add_review(col, start_ms + 1000)
    add_review(col, start_ms + 2000)
    add_review(col, start_ms + 3000, ease=0)  # manual reschedule
    _, total = today_review_count(col)
    assert total == 2


def test_study_date_follows_rollover():
    # Next day starts 04:00 on 2026-02-02, so the current Anki day is 2026-02-01.
    cutoff = int(datetime(2026, 2, 2, 4, 0).timestamp())
    fake = SimpleNamespace(
        sched=SimpleNamespace(day_cutoff=cutoff),
        db=SimpleNamespace(scalar=lambda *_a: 0),
    )
    assert today_review_count(fake) == (date(2026, 2, 1), 0)
