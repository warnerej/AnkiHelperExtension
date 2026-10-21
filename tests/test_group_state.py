from addon.errors import ApiError, AuthError, NetworkError, ServerError
from addon.group_state import (
    ERROR,
    IDLE,
    LOADED,
    LOADING,
    REFRESH_SECONDS,
    action_on_open,
    should_retry,
)


def test_action_on_open():
    assert action_on_open(IDLE, 0) == "fetch"
    assert action_on_open(ERROR, 0) == "fetch"
    assert action_on_open(LOADING, 999) == "fetch"  # lapsed retry timer
    assert action_on_open(LOADED, REFRESH_SECONDS - 1) is None
    assert action_on_open(LOADED, REFRESH_SECONDS) == "refresh"


def test_should_retry():
    assert should_retry(NetworkError("x"))
    assert should_retry(ServerError("x"))
    assert not should_retry(AuthError("x"))
    assert not should_retry(ApiError(403, "x"))
