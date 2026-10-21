"""Pure decisions for the review-history backfill (kept free of aqt for testing)."""

from .models import HistoryResult, HistoryStatus, YearReviews
from .stats import TodayStats

SYNC_FIRST_NOTE = "Sync with AnkiWeb first so reviews from your phone are included."
EMPTY_WARNING = (
    "This computer has no reviews in the last year. "
    "Uploading will clear your history on the server."
)


def should_auto_backfill(
    status: HistoryStatus | None, already_done_this_session: bool
) -> bool:
    """Backfill only when the server has never received history for this account."""
    if already_done_this_session or status is None:
        return False
    return status.history_synced_at is None


def has_reviews(history: YearReviews) -> bool:
    return any(day.total_reviews > 0 for day in history.days)


def manual_confirmation(history: YearReviews, synced_this_session: bool) -> str:
    """Text for the manual re-upload prompt, with warnings for risky states."""
    parts = [
        "This replaces the last year of review history on the AnkiHelper server "
        "with the data on this computer. Your groups will see the result."
    ]
    if not synced_this_session:
        parts.append(SYNC_FIRST_NOTE)
    if not has_reviews(history):
        parts.append(EMPTY_WARNING)
    return "\n\n".join(parts)


CONFIRM_WORD = "upload"


def typed_confirmation_ok(typed: str) -> bool:
    """The stronger confirm for an empty-year upload: the user types the word."""
    return typed.strip().lower() == CONFIRM_WORD


def result_message(result: HistoryResult) -> str:
    unit = "day" if result.days_written == 1 else "days"
    return f"AnkiHelper: uploaded {result.days_written} {unit} of review history."


def today_row(history: YearReviews) -> TodayStats | None:
    """The upload's row for its last day, in the shape `push_today` compares against."""
    for day in history.days:
        if day.study_date == history.end:
            return TodayStats(
                day.study_date,
                day.total_reviews,
                day.retention,
                round(day.study_minutes or 0),
            )
    return None
