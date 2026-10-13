from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

DAY_SECONDS = 86400
MAX_STUDY_MINUTES = 1440
REVIEW_TYPE = 1  # revlog.type for cards in review (not learning/relearning/cram)


@dataclass(frozen=True)
class TodayStats:
    study_date: date
    total_reviews: int
    retention: float | None  # percent 0-100; None when no review-type answers yet
    study_minutes: int


def today_stats(col: Any) -> TodayStats:
    """Return running stats for the current Anki day.

    The day boundary is the scheduler's rollover ("next day starts at"), not
    midnight. Rows with ease 0 are manual reschedules, not reviews. Retention is
    Anki's "true retention": the share of review-type answers that were not Again.
    """
    cutoff: int = col.sched.day_cutoff
    # Noon of the Anki day: far from rollover, so DST shifts cannot change the date.
    study_date = datetime.fromtimestamp(cutoff - DAY_SECONDS // 2).date()
    row = col.db.first(
        "select count(), sum(time),"
        " sum(case when type = ? then 1 else 0 end),"
        " sum(case when type = ? and ease > 1 then 1 else 0 end)"
        " from revlog where id > ? and ease > 0",
        REVIEW_TYPE,
        REVIEW_TYPE,
        (cutoff - DAY_SECONDS) * 1000,
    )
    total, time_ms, review_total, review_passed = (int(v or 0) for v in row or ())
    retention = round(review_passed / review_total * 100, 1) if review_total else None
    minutes = min(round(time_ms / 60000), MAX_STUDY_MINUTES)
    return TodayStats(study_date, total, retention, minutes)
