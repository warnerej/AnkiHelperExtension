from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from .models import FriendDay, ProfileReviews

DAY_SECONDS = 86400
MAX_STUDY_MINUTES = 1440
YEAR_DAYS = 365
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


def year_stats(col: Any, days: int = YEAR_DAYS) -> ProfileReviews:
    """Per-day stats for the last `days` Anki days, in the shape friends use.

    Days are bucketed by the scheduler rollover, like `today_stats`.
    """
    cutoff: int = col.sched.day_cutoff
    today = datetime.fromtimestamp(cutoff - DAY_SECONDS // 2).date()
    cutoff_ms = cutoff * 1000
    rows = col.db.all(
        "select (? - id - 1) / ?, count(), sum(time),"
        " sum(case when type = ? then 1 else 0 end),"
        " sum(case when type = ? and ease > 1 then 1 else 0 end)"
        " from revlog where id > ? and id <= ? and ease > 0 group by 1",
        cutoff_ms,
        DAY_SECONDS * 1000,
        REVIEW_TYPE,
        REVIEW_TYPE,
        cutoff_ms - days * DAY_SECONDS * 1000,
        cutoff_ms,
    )
    result: list[FriendDay] = []
    for ago, total, time_ms, review_total, review_passed in rows:
        review_total, review_passed = int(review_total or 0), int(review_passed or 0)
        result.append(
            FriendDay(
                study_date=today - timedelta(days=max(int(ago), 0)),
                total_reviews=int(total),
                retention=(
                    round(review_passed / review_total * 100, 1)
                    if review_total
                    else None
                ),
                study_minutes=min(round(int(time_ms or 0) / 60000), MAX_STUDY_MINUTES),
            )
        )
    result.sort(key=lambda d: d.study_date)
    return ProfileReviews(
        username="you",
        start=today - timedelta(days=days - 1),
        end=today,
        days=result,
    )
