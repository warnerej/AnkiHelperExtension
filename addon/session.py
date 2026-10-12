import threading
import time
from collections.abc import Callable

import httpx

from .errors import (
    AnkiHelperError,
    ApiError,
    AuthError,
    NetworkError,
    ServerError,
)
from .log import get_logger
from .models import TokenResponse
from .settings import Settings

REFRESH_MARGIN_SECONDS = 60

log = get_logger()


def check_response(resp: httpx.Response) -> httpx.Response:
    """Return `resp` if 2xx, else raise the matching AnkiHelperError."""
    if resp.is_success:
        return resp
    if resp.status_code >= 500:
        raise ServerError(f"Server error ({resp.status_code})")
    detail = ""
    try:
        body = resp.json()
        detail = str(body.get("detail", "")) if isinstance(body, dict) else ""
    except ValueError:
        pass
    if resp.status_code == 401:
        raise AuthError(detail or "Unauthorized")
    raise ApiError(resp.status_code, detail or resp.reason_phrase)


class Session:
    """In-memory token holder. Thread-safe; at most one login/refresh at a time."""

    def __init__(
        self,
        http: httpx.Client,
        get_settings: Callable[[], Settings],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._http = http
        self._get_settings = get_settings
        self._clock = clock
        self._lock = threading.RLock()
        self._access: str | None = None
        self._refresh: str | None = None
        self._expires_at = 0.0

    @property
    def is_logged_in(self) -> bool:
        return self._access is not None

    def ensure_token(self) -> str:
        """A valid access token, logging in or refreshing only when needed."""
        with self._lock:
            if self._access is None:
                self._login()
            elif self._clock() >= self._expires_at - REFRESH_MARGIN_SECONDS:
                self._refresh_or_login()
            assert self._access is not None
            return self._access

    def refresh_after_401(self, failed_token: str) -> str:
        """Recover from a 401. Reuses the token if another thread already rotated it."""
        with self._lock:
            if self._access is not None and self._access != failed_token:
                return self._access
            self._refresh_or_login()
            assert self._access is not None
            return self._access

    def logout(self) -> None:
        """Best-effort server logout, then always clear local state."""
        with self._lock:
            token = self._access
            self._clear()
        if token is None:
            return
        try:
            self._post("/auth/logout", headers={"Authorization": f"Bearer {token}"})
        except AnkiHelperError as exc:
            log.info("logout request failed: %s", exc)

    def _clear(self) -> None:
        self._access = None
        self._refresh = None
        self._expires_at = 0.0

    def _store(self, resp: httpx.Response) -> None:
        try:
            tokens = TokenResponse.model_validate(resp.json())
        except ValueError as exc:  # pydantic ValidationError and JSON errors
            raise ServerError("Unexpected response from /auth") from exc
        self._access = tokens.access_token
        self._refresh = tokens.refresh_token
        self._expires_at = self._clock() + tokens.expires_in

    def _login(self) -> None:
        settings = self._get_settings()
        try:
            resp = self._post(
                "/auth/login",
                json={"email": settings.email, "password": settings.password},
            )
        except AuthError as exc:
            self._clear()
            raise AuthError("Invalid email or password.") from exc
        self._store(resp)
        log.info("logged in")

    def _refresh_or_login(self) -> None:
        refresh_token = self._refresh
        self._clear()
        if refresh_token is not None:
            try:
                self._store(
                    self._post("/auth/refresh", json={"refresh_token": refresh_token})
                )
                log.info("token refreshed")
                return
            except (AuthError, ApiError) as exc:
                # Rejected or already-used refresh token: fall back to a full login.
                log.info("refresh rejected (%s); logging in again", exc)
        self._login()

    def _post(self, path: str, **kwargs) -> httpx.Response:
        try:
            resp = self._http.post(path, **kwargs)
        except httpx.TimeoutException as exc:
            raise NetworkError("The AnkiHelper server timed out.") from exc
        except httpx.TransportError as exc:
            raise NetworkError("Could not reach the AnkiHelper server.") from exc
        return check_response(resp)
