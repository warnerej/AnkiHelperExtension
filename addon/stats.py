from datetime import date, datetime
from typing import Any

DAY_SECONDS = 86400


def today_review_count(col: Any) -> tuple[date, int]:
    """Return (study date, reviews done) for the current Anki day.

    The day boundary is the scheduler's rollover ("next day starts at"), not
    midnight. Rows with ease 0 are manual reschedules, not reviews.
    """
    cutoff: int = col.sched.day_cutoff
    # Noon of the Anki day: far from rollover, so DST shifts cannot change the date.
    study_date = datetime.fromtimestamp(cutoff - DAY_SECONDS // 2).date()
    total = col.db.scalar(
        "select count() from revlog where id > ? and ease > 0",
        (cutoff - DAY_SECONDS) * 1000,
    )
    return study_date, int(total or 0)
