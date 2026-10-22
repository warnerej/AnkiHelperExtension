import random
import threading
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

import httpx

from .errors import (
    ApiError,
    AuthError,
    ClientClosedError,
    FeatureUnavailableError,
    NetworkError,
    NotConfiguredError,
    ServerError,
)
from .log import get_logger
from .models import (
    DailyReview,
    Group,
    GroupReviews,
    GroupSummary,
    HistoryResult,
    HistoryStatus,
    InviteCode,
    SignupResponse,
    UserResponse,
    YearReviews,
)
from .session import Session, check_response
from .settings import Settings
from .stats import TodayStats
from .token_store import TokenStore

CONNECT_TIMEOUT = 5.0
GET_RETRIES = 2
BACKOFF_BASE = 0.5
HEATMAP_DAYS = 365

log = get_logger()


def _group_path(group_id: str) -> str:
    return f"/groups/{quote(group_id, safe='')}"


def _as_list(data: Any) -> list[Any]:
    if not isinstance(data, list):
        raise ValueError(f"expected list, got {type(data).__name__}")
    return data


def _parse[T](path: str, build: Callable[[], T]) -> T:
    """Run `build`, mapping a malformed body (ValueError) to ServerError."""
    try:
        return build()
    except ValueError as exc:
        raise ServerError(f"Unexpected response from {path}") from exc


def _history_error(exc: ApiError) -> Exception:
    """Map the history endpoints' 4xx errors to user-facing ones."""
    if exc.status_code == 404:
        return FeatureUnavailableError(
            "The server does not support history upload yet."
        )
    if exc.status_code == 429:
        return ApiError(429, "Uploaded too many times recently. Try again in an hour.")
    if exc.status_code == 422:
        return ApiError(422, f"The server rejected this history: {exc.detail}")
    return exc


class ApiClient:
    """Blocking client for the AnkiHelper API. Call from a background thread only."""

    def __init__(
        self,
        get_settings: Callable[[], Settings],
        token_store: TokenStore,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._get_settings = get_settings
        self._token_store = token_store
        self._transport = transport
        self._sleep = sleep
        self._http: httpx.Client | None = None
        self._base_url = ""
        self._timeout = 0.0
        self._session: Session | None = None
        self._closed = False
        # Worker threads share this client; without the lock two cold-start
        # callers would each build a Session and refresh the same single-use token.
        self._lock = threading.RLock()

    def _ensure_session(self) -> Session:
        """(Re)build the HTTP client when the user edits base_url or timeout."""
        with self._lock:
            if self._closed:
                raise ClientClosedError()
            settings = self._get_settings()  # raises NotConfiguredError
            if (
                self._http is None
                or self._session is None
                or settings.base_url != self._base_url
                or settings.timeout_seconds != self._timeout
            ):
                if self._http is not None:
                    self._http.close()
                self._base_url = settings.base_url
                self._timeout = settings.timeout_seconds
                self._http = httpx.Client(
                    base_url=settings.base_url,
                    timeout=httpx.Timeout(
                        settings.timeout_seconds, connect=CONNECT_TIMEOUT
                    ),
                    transport=self._transport,
                )
                self._session = Session(
                    self._http, self._token_store, settings.base_url
                )
            return self._session

    def request(
        self, method: str, path: str, *, retries: int | None = None, **kwargs: Any
    ) -> httpx.Response:
        """Send a request. `retries` overrides the per-method default (0 = try once)."""
        method = method.upper()
        if retries is None:
            retries = GET_RETRIES if method in ("GET", "PUT", "DELETE") else 0
        for attempt in range(retries + 1):
            try:
                return self._request_once(method, path, **kwargs)
            except (NetworkError, ServerError) as exc:
                if attempt >= retries:
                    raise
                delay = BACKOFF_BASE * 2**attempt + random.uniform(0, 0.25)
                log.info(
                    "%s %s failed (%s); retry %d in %.1fs",
                    method,
                    path,
                    exc,
                    attempt + 1,
                    delay,
                )
                self._sleep(delay)
        raise AssertionError("unreachable")

    def get(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("POST", path, **kwargs)

    def put(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("PUT", path, **kwargs)

    def patch(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("PATCH", path, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("DELETE", path, **kwargs)

    def put_daily_reviews(self, stats: TodayStats) -> DailyReview:
        """Upsert the running stats for the day (idempotent, safe to retry)."""
        body: dict[str, Any] = {
            "total_reviews": stats.total_reviews,
            "study_date": stats.study_date.isoformat(),
            "study_minutes": stats.study_minutes,
        }
        if stats.retention is not None:
            body["retention"] = stats.retention
        try:
            return DailyReview.from_dict(self.put("/reviews/daily", json=body).json())
        except ValueError as exc:
            raise ServerError("Unexpected response from /reviews/daily") from exc

    def get_history_status(self) -> HistoryStatus:
        path = "/reviews/history/status"
        try:
            resp = self.get(path)
        except ApiError as exc:
            raise _history_error(exc) from exc
        return _parse(path, lambda: HistoryStatus.from_dict(resp.json()))

    def upload_history(self, history: YearReviews) -> HistoryResult:
        """Replace the server's rows for `history.start`..`history.end` (idempotent)."""
        path = "/reviews/history"
        days: list[dict[str, Any]] = []
        for day in history.days:
            row: dict[str, Any] = {
                "study_date": day.study_date.isoformat(),
                "total_reviews": day.total_reviews,
            }
            if day.retention is not None:
                row["retention"] = day.retention
            if day.study_minutes is not None:
                row["study_minutes"] = day.study_minutes
            days.append(row)
        body = {
            "start": history.start.isoformat(),
            "end": history.end.isoformat(),
            "days": days,
        }
        try:
            resp = self.put(path, json=body)
        except ApiError as exc:
            raise _history_error(exc) from exc
        return _parse(path, lambda: HistoryResult.from_dict(resp.json()))

    def get_me(self) -> UserResponse:
        try:
            return UserResponse.from_dict(self.get("/auth/me").json())
        except ValueError as exc:
            raise ServerError("Unexpected response from /auth/me") from exc

    # --- groups ---

    def list_groups(self) -> list[GroupSummary]:
        path = "/groups"
        return _parse(
            path,
            lambda: [
                GroupSummary.from_dict(g) for g in _as_list(self.get(path).json())
            ],
        )

    def get_group(self, group_id: str) -> Group:
        """Group detail; `invite_code` and `allowed` are set only for the owner."""
        path = _group_path(group_id)
        return _parse(path, lambda: Group.from_dict(self.get(path).json()))

    def create_group(self, name: str) -> Group:
        return _parse(
            "/groups",
            lambda: Group.from_dict(self.post("/groups", json={"name": name}).json()),
        )

    def rename_group(self, group_id: str, name: str) -> Group:
        path = _group_path(group_id)
        return _parse(
            path, lambda: Group.from_dict(self.patch(path, json={"name": name}).json())
        )

    def delete_group(self, group_id: str) -> None:
        self.delete(_group_path(group_id))

    def join_group(self, invite_code: str) -> Group:
        try:
            resp = self.post("/groups/join", json={"invite_code": invite_code})
        except ApiError as exc:
            if exc.status_code == 404:
                raise ApiError(
                    404,
                    "Code not found, or your username isn't on this group's allowed list.",
                ) from exc
            if exc.status_code == 429:
                raise ApiError(429, "Too many attempts. Try again later.") from exc
            raise
        return _parse("/groups/join", lambda: Group.from_dict(resp.json()))

    def leave_group(self, group_id: str) -> None:
        try:
            self.post(f"{_group_path(group_id)}/leave")
        except ApiError as exc:
            if exc.status_code == 409:
                raise ApiError(
                    409,
                    "Owners can't leave; transfer ownership or delete the group.",
                ) from exc
            raise

    def allow_username(self, group_id: str, username: str) -> None:
        self.post(f"{_group_path(group_id)}/allowed", json={"username": username})

    def disallow_username(self, group_id: str, username: str) -> None:
        self.delete(f"{_group_path(group_id)}/allowed/{quote(username, safe='')}")

    def regenerate_invite_code(self, group_id: str) -> str:
        path = f"{_group_path(group_id)}/invite-code"
        return _parse(
            path, lambda: InviteCode.from_dict(self.post(path).json()).invite_code
        )

    def transfer_ownership(self, group_id: str, username: str) -> None:
        self.post(f"{_group_path(group_id)}/transfer", json={"username": username})

    def get_group_reviews(
        self, group_id: str, days: int = HEATMAP_DAYS
    ) -> GroupReviews:
        """Every joined member's review rows for the past `days` days."""
        path = f"{_group_path(group_id)}/reviews"
        return _parse(
            path,
            lambda: GroupReviews.from_dict(
                self.get(path, params={"days": days}).json()
            ),
        )

    def login(self, email: str, password: str) -> None:
        """Log in and persist the refresh token (never retried; password not kept)."""
        self._ensure_session().login(email, password)

    def restore(self) -> None:
        """Resume the stored session (raises NotLoggedInError if there is none)."""
        self._ensure_session().ensure_token()

    def signup(self, email: str, password: str, username: str) -> SignupResponse:
        """Create an account (no bearer token, never retried)."""
        return self._ensure_session().signup(email, password, username)

    def forgot_password(self, email: str) -> None:
        """Request a password reset email (no bearer token, never retried)."""
        self._ensure_session().forgot_password(email)

    def logout(self) -> None:
        """End the server session (best effort) and delete the stored token."""
        try:
            session = self._ensure_session()
        except NotConfiguredError:
            self._token_store.clear()
            return
        session.logout()

    def delete_account(self, password: str) -> None:
        """Permanently delete the account, then drop the local session.

        Irreversible and carries the password, so it is never retried.
        """
        try:
            self.request("DELETE", "/auth/me", json={"password": password}, retries=0)
        except ApiError as exc:
            if exc.status_code == 403:
                raise ApiError(403, "Wrong password.") from None
            raise
        self._ensure_session().account_deleted()

    def close(self) -> None:
        """Release the HTTP client. The stored session is kept for the next start.

        Terminal until reopen(): later calls raise ClientClosedError instead of
        building a fresh session, whose token refresh Anki could cut off.
        """
        with self._lock:
            self._closed = True
            if self._http is not None:
                self._http.close()
                self._http = None
                self._session = None

    def reopen(self) -> None:
        """Allow requests again (a profile was reopened in the same Anki run)."""
        with self._lock:
            self._closed = False

    def has_fresh_token(self) -> bool:
        """True if a request now needs no login or refresh."""
        session = self._session
        return not self._closed and session is not None and session.has_fresh_token()

    def _request_once(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        session = self._ensure_session()
        token = session.ensure_token()
        resp = self._send(method, path, token, **kwargs)
        if resp.status_code == 401:
            token = session.refresh_after_401(token)
            resp = self._send(method, path, token, **kwargs)
            if resp.status_code == 401:
                raise AuthError("Session rejected after re-login.")
        return check_response(resp)

    def _send(
        self, method: str, path: str, token: str, **kwargs: Any
    ) -> httpx.Response:
        http = self._http
        if http is None:
            raise ClientClosedError()
        headers = {**kwargs.pop("headers", {}), "Authorization": f"Bearer {token}"}
        start = time.monotonic()
        try:
            resp = http.request(method, path, headers=headers, **kwargs)
        except httpx.TimeoutException as exc:
            log.info(
                "%s %s timed out after %.0f ms",
                method,
                path,
                (time.monotonic() - start) * 1000,
            )
            raise NetworkError("The AnkiHelper server timed out.") from exc
        except httpx.TransportError as exc:
            log.info("%s %s connection failed: %s", method, path, type(exc).__name__)
            raise NetworkError("Could not reach the AnkiHelper server.") from exc
        except RuntimeError as exc:
            # httpx raises this when close() ran on another thread mid-request.
            if self._closed:
                raise ClientClosedError() from exc
            raise
        log.info(
            "%s %s -> %d (%.0f ms)",
            method,
            path,
            resp.status_code,
            (time.monotonic() - start) * 1000,
        )
        return resp
