from datetime import date

from addon.account_state import AccountState, restore_outcome
from addon.errors import (
    ApiError,
    AuthError,
    NetworkError,
    NotLoggedInError,
    ServerError,
)
from addon.stats import TodayStats


def reading(reviews: int = 5) -> TodayStats:
    return TodayStats(date(2026, 10, 22), reviews, 90.0, 12)


def make() -> tuple[AccountState, list[str]]:
    calls: list[str] = []
    return AccountState(on_reset=lambda: calls.append("reset")), calls


def test_starts_logged_out_and_empty():
    state, calls = make()
    assert not state.logged_in and state.last_pushed is None and calls == []


def test_start_session_clears_previous_account_then_logs_in():
    state, calls = make()
    state.start_session()
    state.record_push(reading())
    state.start_session()  # another user logs in without a logout in between
    assert state.logged_in and state.last_pushed is None
    assert calls == ["reset", "reset"]


def test_reset_logs_out_and_forgets_last_push():
    state, calls = make()
    state.start_session()
    state.record_push(reading())
    state.reset()
    assert not state.logged_in and state.last_pushed is None
    assert calls == ["reset", "reset"]


def test_push_started_before_a_reset_is_not_recorded():
    state, _ = make()
    state.start_session()
    generation = state.generation
    state.reset()
    state.start_session()
    assert not state.record_push(reading(), generation)
    assert state.last_pushed is None  # next push for the new account is not skipped


def test_push_from_the_current_account_is_recorded():
    state, _ = make()
    state.start_session()
    assert state.record_push(reading(7), state.generation)
    assert state.last_pushed == reading(7)


def test_record_push_without_generation_always_records():
    state, _ = make()
    assert state.record_push(reading())
    assert state.last_pushed == reading()


def test_restore_outcomes():
    assert restore_outcome(None) is True
    assert restore_outcome(NotLoggedInError()) is False
    assert restore_outcome(NetworkError("offline")) is True
    assert restore_outcome(ServerError("down")) is True
    assert restore_outcome(AuthError("bad")) is False
    assert restore_outcome(ApiError(400, "odd")) is False
    assert restore_outcome(ValueError("bug")) is None
