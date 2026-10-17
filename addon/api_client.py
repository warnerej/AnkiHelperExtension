import random
import time
from collections.abc import Callable, Iterable
from typing import Any
from urllib.parse import quote

import httpx

from .errors import ApiError, AuthError, NetworkError, ServerError
from .log import get_logger
from .models import DailyReview, ProfileReviews, SignupResponse, UserResponse
from .session import Session, check_response
from .settings import Settings
from .stats import TodayStats

CONNECT_TIMEOUT = 5.0
GET_RETRIES = 2
BACKOFF_BASE = 0.5
HEATMAP_DAYS = 365

log = get_logger()


class ApiClient:
    """Blocking client for the AnkiHelper API. Call from a background thread only."""

    def __init__(
        self,
        get_settings: Callable[[], Settings],
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._get_settings = get_settings
        self._transport = transport
        self._sleep = sleep
        self._http: httpx.Client | None = None
        self._base_url = ""
        self._timeout = 0.0
        self._session: Session | None = None

    def _ensure_session(self) -> Session:
        """(Re)build the HTTP client when the user edits base_url or timeout."""
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
            self._session = Session(self._http, self._get_settings)
        return self._session

    def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        method = method.upper()
        retries = GET_RETRIES if method in ("GET", "PUT") else 0
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
            return DailyReview.model_validate(
                self.put("/reviews/daily", json=body).json()
            )
        except ValueError as exc:
            raise ServerError("Unexpected response from /reviews/daily") from exc

    def get_me(self) -> UserResponse:
        try:
            return UserResponse.model_validate(self.get("/auth/me").json())
        except ValueError as exc:
            raise ServerError("Unexpected response from /auth/me") from exc

    def get_profile_reviews(
        self, username: str, days: int = HEATMAP_DAYS
    ) -> ProfileReviews:
        """A user's review rows for the past `days` days (404 raises ApiError)."""
        path = f"/profiles/{quote(username, safe='')}/reviews"
        try:
            return ProfileReviews.model_validate(
                self.get(path, params={"days": days}).json()
            )
        except ValueError as exc:
            raise ServerError(f"Unexpected response from {path}") from exc

    def get_friends_reviews(
        self, usernames: Iterable[str] | None = None
    ) -> dict[str, ProfileReviews | None]:
        """Fetch each friend's last year. Unknown usernames map to None."""
        if usernames is None:
            usernames = self._get_settings().friends
        result: dict[str, ProfileReviews | None] = {}
        for name in usernames:
            try:
                result[name] = self.get_profile_reviews(name)
            except ApiError as exc:
                if exc.status_code != 404:
                    raise
                result[name] = None
        return result

    def signup(self, email: str, password: str, username: str) -> SignupResponse:
        """Create an account (no bearer token, never retried)."""
        return self._ensure_session().signup(email, password, username)

    def logout(self) -> None:
        if self._session is not None:
            self._session.logout()

    def close(self) -> None:
        self.logout()
        if self._http is not None:
            self._http.close()
            self._http = None
            self._session = None

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
        assert self._http is not None
        headers = {**kwargs.pop("headers", {}), "Authorization": f"Bearer {token}"}
        start = time.monotonic()
        try:
            resp = self._http.request(method, path, headers=headers, **kwargs)
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
        log.info(
            "%s %s -> %d (%.0f ms)",
            method,
            path,
            resp.status_code,
            (time.monotonic() - start) * 1000,
        )
        return resp
