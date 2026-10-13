import threading

import httpx
import pytest

from addon.errors import (
    ApiError,
    AuthError,
    NetworkError,
    NotConfiguredError,
    ServerError,
)
from addon.settings import Settings
from addon.stats import TodayStats

from .conftest import tokens

ME = (200, {"id": "u1", "email": "a@b.c"})


def test_login_then_bearer_header(backend, make_client):
    backend.on("POST", "/auth/login", (200, tokens(1)))
    backend.on("GET", "/auth/me", ME)
    me = make_client().get_me()
    assert me.email == "a@b.c"
    assert backend.body(0) == {"email": "a@b.c", "password": "pw-secret"}
    assert backend.calls[1].headers["Authorization"] == "Bearer access1"


def test_bad_credentials_no_retry(backend, make_client):
    backend.on("POST", "/auth/login", (401, {"detail": "nope"}))
    with pytest.raises(AuthError, match="Invalid email or password"):
        make_client().get_me()
    assert backend.count("POST", "/auth/login") == 1


def test_token_reused_without_refresh(backend, make_client):
    backend.on("POST", "/auth/login", (200, tokens(1)))
    backend.on("GET", "/auth/me", ME)
    client = make_client()
    for _ in range(3):
        client.get_me()
    assert backend.count("POST", "/auth/login") == 1
    assert backend.count("POST", "/auth/refresh") == 0


def test_401_refreshes_once_and_retries(backend, make_client):
    backend.on("POST", "/auth/login", (200, tokens(1)))
    backend.on("POST", "/auth/refresh", (200, tokens(2)))
    backend.on("GET", "/auth/me", (401, {"detail": "expired"}), ME)
    make_client().get_me()
    assert backend.count("POST", "/auth/refresh") == 1
    assert backend.body(2) == {"refresh_token": "refresh1"}
    assert backend.calls[-1].headers["Authorization"] == "Bearer access2"


def test_refresh_rejected_falls_back_to_login(backend, make_client):
    backend.on("POST", "/auth/login", (200, tokens(1)), (200, tokens(3)))
    backend.on("POST", "/auth/refresh", (401, {"detail": "used"}))
    backend.on("GET", "/auth/me", (401, {}), ME)
    make_client().get_me()
    assert backend.count("POST", "/auth/login") == 2
    assert backend.calls[-1].headers["Authorization"] == "Bearer access3"


def test_second_401_raises_auth_error(backend, make_client):
    backend.on("POST", "/auth/login", (200, tokens(1)))
    backend.on("POST", "/auth/refresh", (200, tokens(2)))
    backend.on("GET", "/auth/me", (401, {}))
    with pytest.raises(AuthError):
        make_client().get_me()
    assert backend.count("GET", "/auth/me") == 2


def test_near_expiry_refreshes_proactively_once(backend, make_client, settings):
    backend.on(
        "POST", "/auth/login", (200, tokens(1, expires_in=30))
    )  # inside 60s margin
    backend.on("POST", "/auth/refresh", (200, tokens(2)))
    backend.on("GET", "/auth/me", ME)
    client = make_client()
    client.get_me()  # login, then immediately near expiry
    client.get_me()  # proactive refresh
    client.get_me()  # fresh token, no more refreshes
    assert backend.count("POST", "/auth/refresh") == 1


def test_concurrent_401s_trigger_one_refresh(backend, make_client):
    backend.on("POST", "/auth/login", (200, tokens(1)))
    backend.on("POST", "/auth/refresh", (200, tokens(2)))

    def handler(request: httpx.Request) -> httpx.Response:
        backend.calls.append(request)
        if request.url.path == "/auth/login":
            return httpx.Response(200, json=tokens(1))
        if request.url.path == "/auth/refresh":
            return httpx.Response(200, json=tokens(2))
        if request.headers["Authorization"] == "Bearer access1":
            return httpx.Response(401, json={})
        return httpx.Response(200, json=ME[1])

    from addon.api_client import ApiClient

    settings = Settings("http://localhost:8000", "a@b.c", "pw", 5)
    client = ApiClient(lambda: settings, transport=httpx.MockTransport(handler))
    client._ensure_session().ensure_token()  # everyone starts with access1
    errors: list[Exception] = []

    def work():
        try:
            client.get_me()
        except Exception as exc:  # noqa: BLE001  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=work) for _ in range(5)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors
    assert backend.count("POST", "/auth/refresh") == 1


def test_get_retries_twice_on_timeout(backend, make_client):
    backend.on("POST", "/auth/login", (200, tokens(1)))
    backend.on("GET", "/auth/me", httpx.ReadTimeout("slow"))
    client = make_client()
    with pytest.raises(NetworkError):
        client.get_me()
    assert backend.count("GET", "/auth/me") == 3
    assert len(make_client.sleeps) == 2


def test_get_retries_on_5xx_then_succeeds(backend, make_client):
    backend.on("POST", "/auth/login", (200, tokens(1)))
    backend.on("GET", "/auth/me", (503, {}), ME)
    assert make_client().get_me().email == "a@b.c"


def test_post_not_retried(backend, make_client):
    backend.on("POST", "/auth/login", (200, tokens(1)))
    backend.on("POST", "/things", httpx.ConnectError("down"))
    with pytest.raises(NetworkError):
        make_client().post("/things", json={})
    assert backend.count("POST", "/things") == 1


def test_login_network_failure_retried_with_the_get(backend, make_client):
    # A connection failure during login is safe to retry: no credentials were accepted.
    backend.on("POST", "/auth/login", httpx.ConnectError("down"))
    with pytest.raises(NetworkError):
        make_client().get_me()
    assert backend.count("POST", "/auth/login") == 3


def test_4xx_carries_detail(backend, make_client):
    backend.on("POST", "/auth/login", (200, tokens(1)))
    backend.on("GET", "/thing", (404, {"detail": "missing"}))
    with pytest.raises(ApiError) as info:
        make_client().get("/thing")
    assert info.value.status_code == 404 and info.value.detail == "missing"


def test_5xx_on_post_raises_server_error(backend, make_client):
    backend.on("POST", "/auth/login", (200, tokens(1)))
    backend.on("POST", "/x", (500, {}))
    with pytest.raises(ServerError):
        make_client().post("/x")


def test_logout_clears_session(backend, make_client):
    backend.on("POST", "/auth/login", (200, tokens(1)), (200, tokens(2)))
    backend.on("POST", "/auth/logout", (200, {}))
    backend.on("GET", "/auth/me", ME)
    client = make_client()
    client.get_me()
    client.logout()
    assert backend.calls[-1].headers["Authorization"] == "Bearer access1"
    client.get_me()
    assert backend.count("POST", "/auth/login") == 2


def test_logout_failure_is_swallowed(backend, make_client):
    backend.on("POST", "/auth/login", (200, tokens(1)))
    backend.on("POST", "/auth/logout", httpx.ConnectError("down"))
    backend.on("GET", "/auth/me", ME)
    client = make_client()
    client.get_me()
    client.logout()


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"base_url": "https://x.io", "email": "a"},
        {"base_url": "http://example.com", "email": "a", "password": "b"},
        {"base_url": "ftp://x", "email": "a", "password": "b"},
    ],
)
def test_settings_validation(config):
    with pytest.raises(NotConfiguredError):
        Settings.from_config(config)


def test_settings_ok_and_password_hidden():
    s = Settings.from_config(
        {
            "base_url": "http://localhost:8000/",
            "email": "a",
            "password": "hunter2",
            "timeout_seconds": "bad",
        }
    )
    assert s.base_url == "http://localhost:8000" and s.timeout_seconds == 15
    assert "hunter2" not in repr(s)


def test_put_daily_reviews_body_and_parse(backend, make_client):
    from datetime import date

    backend.on("POST", "/auth/login", (200, tokens(1)))
    backend.on(
        "PUT",
        "/reviews/daily",
        (
            200,
            {
                "study_date": "2026-01-31",
                "total_reviews": 42,
                "updated_at": "2026-01-31T21:04:11.2+00:00",
            },
        ),
    )
    saved = make_client().put_daily_reviews(TodayStats(date(2026, 1, 31), 42, 87.5, 32))
    assert saved.total_reviews == 42
    assert backend.body(1) == {
        "total_reviews": 42,
        "study_date": "2026-01-31",
        "study_minutes": 32,
        "retention": 87.5,
    }
    assert backend.calls[1].headers["Authorization"] == "Bearer access1"


def test_put_retries_on_502_but_not_422(backend, make_client):
    from datetime import date

    ok = {
        "study_date": "2026-01-31",
        "total_reviews": 1,
        "updated_at": "2026-01-31T21:04:11+00:00",
    }
    backend.on("POST", "/auth/login", (200, tokens(1)))
    backend.on("PUT", "/reviews/daily", (502, {"detail": "db"}), (200, ok))
    make_client().put_daily_reviews(TodayStats(date(2026, 1, 31), 1, None, 0))
    assert backend.count("PUT", "/reviews/daily") == 2
    assert "retention" not in backend.body(2)

    backend.calls.clear()
    backend.on("PUT", "/reviews/daily", (422, {"detail": "bad"}))
    with pytest.raises(ApiError):
        make_client().put_daily_reviews(TodayStats(date(2026, 1, 31), 1, None, 0))
    assert backend.count("PUT", "/reviews/daily") == 1
