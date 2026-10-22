import json
import threading
from datetime import date
from pathlib import Path

import httpx
import pytest

from addon.errors import (
    ApiError,
    AuthError,
    ClientClosedError,
    FeatureUnavailableError,
    NetworkError,
    NotConfiguredError,
    NotLoggedInError,
    ServerError,
)
from addon.models import ReviewDay, YearReviews
from addon.settings import LEGACY_KEYS, Settings, strip_legacy_credentials
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
        make_client()
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


def test_refresh_rejected_clears_store_and_needs_login(backend, make_client, store):
    backend.on("POST", "/auth/login", (200, tokens(1)))
    backend.on("POST", "/auth/refresh", (401, {"detail": "used"}))
    backend.on("GET", "/auth/me", (401, {}), ME)
    with pytest.raises(NotLoggedInError):
        make_client().get_me()
    assert backend.count("POST", "/auth/login") == 1  # no silent re-login
    assert store.load("http://localhost:8000") is None


def test_refresh_sends_newer_token_from_disk(backend, make_client, store):
    backend.on("POST", "/auth/login", (200, tokens(1)))
    backend.on("POST", "/auth/refresh", (200, tokens(3)))
    backend.on("GET", "/auth/me", (401, {}), ME)
    client = make_client()
    store.save("refresh-other", "http://localhost:8000")  # rotated by another process
    client.get_me()
    assert backend.count("POST", "/auth/refresh") == 1
    assert backend.body(2) == {"refresh_token": "refresh-other"}
    assert store.load("http://localhost:8000") == "refresh3"


def test_refresh_uses_memory_token_after_failed_save(backend, make_client, store, monkeypatch):
    backend.on("POST", "/auth/login", (200, tokens(1)))
    backend.on("POST", "/auth/refresh", (200, tokens(3)))
    backend.on("GET", "/auth/me", (401, {}), ME)
    real_save = store.save
    monkeypatch.setattr(store, "save", lambda *a: False)
    client = make_client()  # login's save "fails": disk has no token
    real_save("spent", "http://localhost:8000")  # stale file from an earlier rotation
    client.get_me()
    assert backend.body(2) == {"refresh_token": "refresh1"}
    assert store.load("http://localhost:8000") == "spent"  # rotated token never reached disk


def test_refresh_5xx_keeps_stored_session(backend, make_client, store):
    backend.on("POST", "/auth/login", (200, tokens(1)))
    backend.on("POST", "/auth/refresh", (502, {"detail": "down"}))
    backend.on("GET", "/auth/me", (401, {}))
    with pytest.raises(ServerError):
        make_client().get_me()
    assert store.load("http://localhost:8000") == "refresh1"


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


def test_concurrent_401s_trigger_one_refresh(backend, make_client, store):
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

    settings = Settings("http://localhost:8000", 5)
    client = ApiClient(
        lambda: settings, store, transport=httpx.MockTransport(handler)
    )
    client.login("a@b.c", "pw")  # everyone starts with access1
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


def test_login_network_failure_raises_without_retry(backend, make_client):
    backend.on("POST", "/auth/login", httpx.ConnectError("down"))
    with pytest.raises(NetworkError):
        make_client()
    assert backend.count("POST", "/auth/login") == 1


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


def test_logout_ends_session_and_deletes_token(backend, make_client, store):
    backend.on("POST", "/auth/login", (200, tokens(1)))
    backend.on("POST", "/auth/logout", (204, None))
    backend.on("GET", "/auth/me", ME)
    client = make_client()
    client.get_me()
    assert store.load("http://localhost:8000") == "refresh1"
    client.logout()
    assert backend.calls[-1].headers["Authorization"] == "Bearer access1"
    assert store.load("http://localhost:8000") is None
    with pytest.raises(NotLoggedInError):
        client.get_me()
    assert backend.count("POST", "/auth/login") == 1


def test_logout_failure_still_deletes_token(backend, make_client, store):
    backend.on("POST", "/auth/login", (200, tokens(1)))
    backend.on("POST", "/auth/logout", httpx.ConnectError("down"))
    client = make_client()
    client.logout()
    assert store.load("http://localhost:8000") is None


def test_logout_refreshes_an_expired_access_token_first(backend, make_client, store):
    backend.on("POST", "/auth/login", (200, tokens(1, expires_in=30)))
    backend.on("POST", "/auth/refresh", (200, tokens(2)))
    backend.on("POST", "/auth/logout", (204, None))
    make_client().logout()
    assert backend.calls[-1].headers["Authorization"] == "Bearer access2"
    assert store.load("http://localhost:8000") is None


def test_close_keeps_stored_session(backend, make_client, store):
    backend.on("POST", "/auth/login", (200, tokens(1)))
    make_client().close()
    assert backend.count("POST", "/auth/logout") == 0
    assert store.load("http://localhost:8000") == "refresh1"


def test_closed_client_makes_no_requests(backend, make_client, store):
    backend.on("POST", "/auth/login", (200, tokens(1)))
    client = make_client()
    client.close()
    with pytest.raises(ClientClosedError):
        client.put_daily_reviews(TodayStats(date(2026, 1, 31), 1, None, 0))
    assert backend.count("POST", "/auth/refresh") == 0
    assert store.load("http://localhost:8000") == "refresh1"


def test_reopen_restores_from_stored_token(backend, make_client):
    backend.on("POST", "/auth/login", (200, tokens(1)))
    backend.on("POST", "/auth/refresh", (200, tokens(2)))
    backend.on("GET", "/auth/me", ME)
    client = make_client()
    client.close()
    client.reopen()
    client.get_me()
    assert backend.count("POST", "/auth/refresh") == 1
    refresh_index = next(
        i for i, c in enumerate(backend.calls) if c.url.path == "/auth/refresh"
    )
    assert backend.body(refresh_index)["refresh_token"] == "refresh1"
    assert backend.calls[-1].headers["Authorization"] == "Bearer access2"


def test_has_fresh_token(backend, make_client):
    backend.on("POST", "/auth/login", (200, tokens(1)))
    assert make_client(login=False).has_fresh_token() is False
    client = make_client()
    assert client.has_fresh_token() is True
    client.close()
    assert client.has_fresh_token() is False


def test_has_fresh_token_false_inside_refresh_margin(backend, make_client):
    backend.on("POST", "/auth/login", (200, tokens(1, expires_in=30)))
    assert make_client().has_fresh_token() is False


def test_config_example_has_no_retired_keys():
    path = Path(__file__).parent.parent / "addon" / "config.example.json"
    config = json.loads(path.read_text())
    assert not set(config) & {*LEGACY_KEYS, "friends"}


def test_login_persists_only_refresh_token(backend, make_client, store):
    backend.on("POST", "/auth/login", (200, tokens(1)))
    make_client()
    text = store._path.read_text()
    assert "pw-secret" not in text and "access1" not in text
    assert "refresh1" in text


def test_each_refresh_rewrites_rotated_token(backend, make_client, store):
    backend.on("POST", "/auth/login", (200, tokens(1, expires_in=30)))
    backend.on("POST", "/auth/refresh", (200, tokens(2, expires_in=30)), (200, tokens(3)))
    backend.on("GET", "/auth/me", ME)
    client = make_client()
    client.get_me()
    assert store.load("http://localhost:8000") == "refresh2"
    client.get_me()
    assert store.load("http://localhost:8000") == "refresh3"
    assert backend.body(1) == {"refresh_token": "refresh1"}
    assert backend.body(3) == {"refresh_token": "refresh2"}


def test_restore_from_stored_token(backend, make_client, store):
    store.save("saved", "http://localhost:8000")
    backend.on("POST", "/auth/refresh", (200, tokens(2)))
    backend.on("GET", "/auth/me", ME)
    client = make_client()
    client.restore()
    client.get_me()
    assert backend.body(0) == {"refresh_token": "saved"}
    assert backend.count("POST", "/auth/refresh") == 1
    assert backend.count("POST", "/auth/login") == 0
    assert store.load("http://localhost:8000") == "refresh2"


def test_restore_rejected_clears_file(backend, make_client, store):
    store.save("stale", "http://localhost:8000")
    backend.on("POST", "/auth/refresh", (401, {"detail": "used"}))
    with pytest.raises(NotLoggedInError, match="expired"):
        make_client().restore()
    assert store.load("http://localhost:8000") is None


def test_restore_offline_keeps_file(backend, make_client, store):
    store.save("saved", "http://localhost:8000")
    backend.on("POST", "/auth/refresh", httpx.ConnectError("down"), (200, tokens(2)))
    client = make_client()
    with pytest.raises(NetworkError):
        client.restore()
    assert store.load("http://localhost:8000") == "saved"
    client.restore()  # next attempt works
    assert store.load("http://localhost:8000") == "refresh2"


def test_restore_without_token_is_not_logged_in(backend, make_client):
    with pytest.raises(NotLoggedInError):
        make_client().restore()
    assert not backend.calls


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"base_url": "http://example.com"},
        {"base_url": "ftp://x"},
    ],
)
def test_settings_validation(config):
    with pytest.raises(NotConfiguredError):
        Settings.from_config(config)


def test_settings_ok_needs_only_base_url():
    s = Settings.from_config(
        {"base_url": "http://localhost:8000/", "timeout_seconds": "bad"}
    )
    assert s.base_url == "http://localhost:8000" and s.timeout_seconds == 15


def test_strip_legacy_credentials():
    config = {"base_url": "https://x.io", "email": "a", "password": "p", "username": "u"}
    cleaned, had_password = strip_legacy_credentials(config)
    assert cleaned == {"base_url": "https://x.io"} and had_password
    assert config["password"] == "p"  # input untouched
    cleaned, had_password = strip_legacy_credentials({"base_url": "https://x.io"})
    assert cleaned == {"base_url": "https://x.io"} and not had_password
    assert strip_legacy_credentials({"email": "a", "password": ""}) == ({}, False)


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


SIGNUP_BODY = {"email": "a@b.c", "password": "pw-secret", "username": "bob"}


def test_signup_adopts_tokens(backend, make_client):
    backend.on(
        "POST",
        "/auth/signup",
        (
            201,
            {
                "user": {"id": "u1", "email": "a@b.c"},
                "tokens": tokens(1),
                "confirmation_required": False,
            },
        ),
    )
    backend.on("GET", "/auth/me", ME)
    client = make_client()
    result = client.signup("a@b.c", "pw-secret", "bob")
    assert result.tokens is not None
    assert backend.body(0) == SIGNUP_BODY
    assert "Authorization" not in backend.calls[0].headers
    client.get_me()
    assert backend.count("POST", "/auth/login") == 0
    assert backend.calls[-1].headers["Authorization"] == "Bearer access1"


def test_signup_confirmation_required(backend, make_client):
    backend.on(
        "POST",
        "/auth/signup",
        (
            201,
            {
                "user": {"id": "u1", "email": "a@b.c"},
                "tokens": None,
                "confirmation_required": True,
            },
        ),
    )
    assert make_client().signup("a@b.c", "pw-secret", "bob").confirmation_required


def test_signup_409_and_422_not_retried(backend, make_client):
    backend.on("POST", "/auth/signup", (409, {"detail": "taken"}))
    with pytest.raises(ApiError, match="already taken"):
        make_client().signup("a@b.c", "pw-secret", "bob")
    backend.on("POST", "/auth/signup", (422, {"detail": "bad"}))
    with pytest.raises(ApiError) as info:
        make_client().signup("a@b.c", "x", "bob")
    assert info.value.status_code == 422
    assert backend.count("POST", "/auth/signup") == 2


def test_signup_network_failure_not_retried(backend, make_client):
    backend.on("POST", "/auth/signup", httpx.ConnectError("down"))
    with pytest.raises(NetworkError):
        make_client().signup("a@b.c", "pw-secret", "bob")
    assert backend.count("POST", "/auth/signup") == 1


def test_strip_friends():
    from addon.settings import strip_friends

    cleaned, had = strip_friends({"base_url": "x", "friends": ["a"]})
    assert cleaned == {"base_url": "x"} and had
    assert strip_friends({"base_url": "x", "friends": []}) == ({"base_url": "x"}, False)
    assert strip_friends({"base_url": "x"}) == ({"base_url": "x"}, False)


GROUP = {
    "id": "g1",
    "name": "Study",
    "owner": "bob",
    "members": [{"username": "bob", "role": "owner", "joined_at": "2026-01-01T00:00:00Z"}],
}
OWNER_GROUP = {**GROUP, "invite_code": "ABC123", "allowed": ["bob", "amy"]}


def logged_in(backend):
    backend.on("POST", "/auth/login", (200, tokens(1)))


def test_list_groups(backend, make_client):
    logged_in(backend)
    backend.on(
        "GET",
        "/groups",
        (200, [{"id": "g1", "name": "Study", "role": "owner", "member_count": 3}]),
    )
    groups = make_client().list_groups()
    assert [(g.id, g.role, g.member_count) for g in groups] == [("g1", "owner", 3)]
    assert backend.calls[1].headers["Authorization"] == "Bearer access1"


def test_get_group_owner_vs_member(backend, make_client):
    logged_in(backend)
    backend.on("GET", "/groups/g1", (200, OWNER_GROUP), (200, GROUP))
    client = make_client()
    owner_view = client.get_group("g1")
    assert owner_view.invite_code == "ABC123" and owner_view.allowed == ["bob", "amy"]
    member_view = client.get_group("g1")
    assert member_view.invite_code is None and member_view.allowed is None


def test_create_rename_delete_group(backend, make_client):
    logged_in(backend)
    backend.on("POST", "/groups", (201, OWNER_GROUP))
    backend.on("PATCH", "/groups/g1", (200, {**GROUP, "name": "New"}))
    backend.on("DELETE", "/groups/g1", (204, None))
    client = make_client()
    assert client.create_group("Study").invite_code == "ABC123"
    assert backend.body(1) == {"name": "Study"}
    assert client.rename_group("g1", "New").name == "New"
    assert backend.body(2) == {"name": "New"}
    client.delete_group("g1")
    assert backend.calls[3].method == "DELETE"


def test_join_group_maps_404_and_429(backend, make_client):
    logged_in(backend)
    backend.on("POST", "/groups/join", (404, {"detail": "x"}), (429, {}), (200, GROUP))
    client = make_client()
    with pytest.raises(ApiError, match="Code not found") as exc:
        client.join_group("bad")
    assert exc.value.status_code == 404
    with pytest.raises(ApiError, match="Too many") as exc:
        client.join_group("bad")
    assert exc.value.status_code == 429
    assert client.join_group("ABC123").id == "g1"
    assert backend.body(3) == {"invite_code": "ABC123"}
    assert backend.count("POST", "/groups/join") == 3


def test_leave_group_maps_409(backend, make_client):
    logged_in(backend)
    backend.on("POST", "/groups/g1/leave", (409, {"detail": "owner"}), (204, None))
    client = make_client()
    with pytest.raises(ApiError, match="Owners can't leave") as exc:
        client.leave_group("g1")
    assert exc.value.status_code == 409
    client.leave_group("g1")


def test_allowed_invite_and_transfer(backend, make_client):
    logged_in(backend)
    backend.on("POST", "/groups/g1/allowed", (201, {}))
    backend.on("DELETE", "/groups/g1/allowed/a b", (204, None))
    backend.on("POST", "/groups/g1/invite-code", (200, {"invite_code": "NEW999"}))
    backend.on("POST", "/groups/g1/transfer", (200, {}))
    client = make_client()
    client.allow_username("g1", "amy")
    assert backend.body(1) == {"username": "amy"}
    client.disallow_username("g1", "a b")
    assert backend.calls[2].url.raw_path == b"/groups/g1/allowed/a%20b"
    assert client.regenerate_invite_code("g1") == "NEW999"
    client.transfer_ownership("g1", "amy")
    assert backend.body(4) == {"username": "amy"}


def test_get_group_reviews(backend, make_client):
    logged_in(backend)
    backend.on(
        "GET",
        "/groups/g1/reviews",
        (
            200,
            {
                "group_id": "g1",
                "start": "2026-01-01",
                "end": "2026-01-02",
                "members": [
                    {"username": "bob", "days": [{"study_date": "2026-01-02", "total_reviews": 5}]},
                    {"username": "amy", "days": []},
                ],
            },
        ),
    )
    result = make_client().get_group_reviews("g1")
    assert backend.calls[1].url.params["days"] == "365"
    assert [(m.username, len(m.days)) for m in result.members] == [("bob", 1), ("amy", 0)]


def test_group_get_and_delete_retry_but_post_patch_do_not(backend, make_client):
    logged_in(backend)
    backend.on("GET", "/groups", (502, {}))
    backend.on("DELETE", "/groups/g1", (502, {}))
    backend.on("PATCH", "/groups/g1", (502, {}))
    backend.on("POST", "/groups", (502, {}))
    client = make_client()
    with pytest.raises(ServerError):
        client.list_groups()
    with pytest.raises(ServerError):
        client.delete_group("g1")
    with pytest.raises(ServerError):
        client.rename_group("g1", "n")
    with pytest.raises(ServerError):
        client.create_group("n")
    assert backend.count("GET", "/groups") == 3
    assert backend.count("DELETE", "/groups/g1") == 3
    assert backend.count("PATCH", "/groups/g1") == 1
    assert backend.count("POST", "/groups") == 1


def test_group_call_refreshes_on_401(backend, make_client):
    logged_in(backend)
    backend.on("POST", "/auth/refresh", (200, tokens(2)))
    backend.on("GET", "/groups", (401, {}), (200, []))
    assert make_client().list_groups() == []
    assert backend.count("POST", "/auth/refresh") == 1


def test_malformed_group_body_is_server_error(backend, make_client):
    logged_in(backend)
    backend.on("GET", "/groups", (200, {"oops": 1}))
    with pytest.raises(ServerError):
        make_client().list_groups()


def test_delete_account_sends_password_and_clears_local_session(
    backend, make_client, store
):
    backend.on("POST", "/auth/login", (200, tokens(1)))
    backend.on("DELETE", "/auth/me", (204, None))
    client = make_client()
    client.delete_account("pw-secret")
    call = backend.calls[-1]
    assert call.method == "DELETE" and call.url.path == "/auth/me"
    assert call.headers["Authorization"] == "Bearer access1"
    assert backend.body(len(backend.calls) - 1) == {"password": "pw-secret"}
    assert backend.count("POST", "/auth/logout") == 0
    assert store.load("http://localhost:8000") is None
    with pytest.raises(NotLoggedInError):
        client.get_me()


def test_delete_account_wrong_password_keeps_session(backend, make_client, store):
    backend.on("POST", "/auth/login", (200, tokens(1)))
    backend.on("DELETE", "/auth/me", (403, {"detail": "Invalid password"}))
    with pytest.raises(ApiError, match="Wrong password") as info:
        make_client().delete_account("bad-pw")
    assert info.value.status_code == 403
    assert "bad-pw" not in str(info.value)
    assert store.load("http://localhost:8000") == "refresh1"


@pytest.mark.parametrize(
    "failure", [(502, {}), httpx.ConnectError("down")], ids=["5xx", "network"]
)
def test_delete_account_is_attempted_once(backend, make_client, store, failure):
    backend.on("POST", "/auth/login", (200, tokens(1)))
    backend.on("DELETE", "/auth/me", failure)
    with pytest.raises((ServerError, NetworkError)):
        make_client().delete_account("pw-secret")
    assert backend.count("DELETE", "/auth/me") == 1
    assert make_client.sleeps == []
    assert store.load("http://localhost:8000") == "refresh1"


def test_delete_account_refreshes_after_401(backend, make_client, store):
    backend.on("POST", "/auth/login", (200, tokens(1)))
    backend.on("POST", "/auth/refresh", (200, tokens(2)))
    backend.on("DELETE", "/auth/me", (401, {"detail": "expired"}), (204, None))
    make_client().delete_account("pw-secret")
    assert backend.count("DELETE", "/auth/me") == 2
    assert backend.calls[-1].headers["Authorization"] == "Bearer access2"
    assert store.load("http://localhost:8000") is None


def test_delete_account_never_logs_the_password(backend, make_client, caplog):
    backend.on("POST", "/auth/login", (200, tokens(1)))
    backend.on("DELETE", "/auth/me", (204, None))
    with caplog.at_level("DEBUG"):
        make_client().delete_account("pw-secret")
    assert "pw-secret" not in caplog.text


HISTORY_PATH = "/reviews/history"
STATUS_PATH = "/reviews/history/status"
HISTORY_OK = {
    "start": "2025-02-01",
    "end": "2026-01-31",
    "days_written": 2,
    "history_synced_at": "2026-01-31T21:04:11+00:00",
}


def year(*days: ReviewDay) -> YearReviews:
    from datetime import date

    return YearReviews("you", date(2025, 2, 1), date(2026, 1, 31), list(days))


def test_upload_history_body_and_parse(backend, make_client):
    from datetime import date

    logged_in(backend)
    backend.on("PUT", HISTORY_PATH, (200, HISTORY_OK))
    result = make_client().upload_history(
        year(
            ReviewDay(date(2026, 1, 30), 10, 91.5, 12),
            ReviewDay(date(2026, 1, 31), 3),
        )
    )
    assert result.days_written == 2
    assert result.history_synced_at.year == 2026
    assert backend.body(1) == {
        "start": "2025-02-01",
        "end": "2026-01-31",
        "days": [
            {
                "study_date": "2026-01-30",
                "total_reviews": 10,
                "retention": 91.5,
                "study_minutes": 12,
            },
            {"study_date": "2026-01-31", "total_reviews": 3},
        ],
    }
    assert backend.calls[1].headers["Authorization"] == "Bearer access1"


def test_upload_history_empty_days_is_valid(backend, make_client):
    logged_in(backend)
    backend.on("PUT", HISTORY_PATH, (200, {**HISTORY_OK, "days_written": 0}))
    make_client().upload_history(year())
    assert backend.body(1)["days"] == []
    assert backend.body(1)["start"] == "2025-02-01"


def test_upload_history_retries_on_5xx(backend, make_client):
    logged_in(backend)
    backend.on("PUT", HISTORY_PATH, (502, {"detail": "db"}), (200, HISTORY_OK))
    make_client().upload_history(year())
    assert backend.count("PUT", HISTORY_PATH) == 2


def test_upload_history_maps_429_422_404(backend, make_client):
    logged_in(backend)
    client = make_client()
    backend.on("PUT", HISTORY_PATH, (429, {"detail": "slow down"}))
    with pytest.raises(ApiError, match="Try again in an hour") as exc:
        client.upload_history(year())
    assert exc.value.status_code == 429
    backend.on("PUT", HISTORY_PATH, (422, {"detail": "range too long"}))
    with pytest.raises(ApiError, match="rejected this history: range too long"):
        client.upload_history(year())
    backend.on("PUT", HISTORY_PATH, (404, {"detail": "Not Found"}))
    with pytest.raises(FeatureUnavailableError):
        client.upload_history(year())
    assert backend.count("PUT", HISTORY_PATH) == 3  # 4xx is never retried


def test_get_history_status(backend, make_client):
    logged_in(backend)
    backend.on(
        "GET", STATUS_PATH, (200, {"history_synced_at": None}), (200, {"history_synced_at": None})
    )
    client = make_client()
    assert client.get_history_status().history_synced_at is None
    assert backend.calls[1].headers["Authorization"] == "Bearer access1"
    backend.on("GET", STATUS_PATH, (200, {"history_synced_at": "2026-01-31T21:04:11+00:00"}))
    assert client.get_history_status().history_synced_at is not None


def test_get_history_status_404_and_malformed(backend, make_client):
    logged_in(backend)
    client = make_client()
    backend.on("GET", STATUS_PATH, (404, {"detail": "Not Found"}))
    with pytest.raises(FeatureUnavailableError):
        client.get_history_status()
    backend.on("GET", STATUS_PATH, (200, {"history_synced_at": 5}))
    with pytest.raises(ServerError):
        client.get_history_status()


def test_forgot_password_posts_email_without_bearer(backend, make_client):
    backend.on("POST", "/auth/forgot-password", (202, {"detail": "ok"}))
    make_client(login=False).forgot_password("a@b.c")
    assert backend.body(0) == {"email": "a@b.c"}
    assert "Authorization" not in backend.calls[0].headers


def test_forgot_password_ignores_response_body(backend, make_client):
    backend.on("POST", "/auth/forgot-password", (202, None))
    make_client(login=False).forgot_password("nobody@b.c")


@pytest.mark.parametrize(
    "status, text",
    [(429, "Too many reset requests"), (422, "valid email")],
)
def test_forgot_password_errors_not_retried(backend, make_client, status, text):
    backend.on("POST", "/auth/forgot-password", (status, {"detail": "x"}))
    with pytest.raises(ApiError, match=text):
        make_client(login=False).forgot_password("a@b.c")
    assert backend.count("POST", "/auth/forgot-password") == 1


def test_forgot_password_network_failure_not_retried(backend, make_client):
    backend.on("POST", "/auth/forgot-password", httpx.ConnectError("down"))
    with pytest.raises(NetworkError):
        make_client(login=False).forgot_password("a@b.c")
    assert backend.count("POST", "/auth/forgot-password") == 1


def test_refresh_rejected_for_disk_token_clears_store(backend, make_client, store):
    backend.on("POST", "/auth/login", (200, tokens(1)))
    backend.on("POST", "/auth/refresh", (401, {"detail": "used"}))
    backend.on("GET", "/auth/me", (401, {}))
    client = make_client()
    store.save("refresh-other", "http://localhost:8000")
    with pytest.raises(NotLoggedInError):
        client.get_me()
    assert backend.count("POST", "/auth/refresh") == 1
    assert store.load("http://localhost:8000") is None


def test_refresh_rejection_logs_unknown_age_after_restore(backend, make_client, store, caplog):
    store.save("saved", "http://localhost:8000")
    backend.on("POST", "/auth/refresh", (401, {"detail": "used"}))
    with caplog.at_level("INFO", logger="ankihelper"), pytest.raises(NotLoggedInError):
        make_client(login=False).get_me()
    assert "unknown (restored from disk)" in caplog.text
