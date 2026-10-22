import threading
import time
from collections.abc import Callable

import httpx

from .errors import (
    AnkiHelperError,
    ApiError,
    AuthError,
    NetworkError,
    NotLoggedInError,
    ServerError,
)
from .log import get_logger
from .models import SignupResponse, TokenResponse
from .token_store import TokenStore

REFRESH_MARGIN_SECONDS = 60

log = get_logger()


def format_detail(detail: object) -> str:
    """Readable text for a FastAPI `detail`: a string, or a list of validation errors."""
    if isinstance(detail, str):
        return detail
    if isinstance(detail, list):
        parts = []
        for item in detail:
            if isinstance(item, dict) and item.get("msg"):
                loc = [
                    str(p) for p in item.get("loc", []) if p not in ("body", "query")
                ]
                msg = str(item["msg"])
                parts.append(f"{'.'.join(loc)}: {msg}" if loc else msg)
            else:
                parts.append(str(item))
        return "; ".join(parts)
    return "" if detail is None else str(detail)


def check_response(resp: httpx.Response) -> httpx.Response:
    """Return `resp` if 2xx, else raise the matching AnkiHelperError."""
    if resp.is_success:
        return resp
    if resp.status_code >= 500:
        raise ServerError(f"Server error ({resp.status_code})")
    detail = ""
    try:
        body = resp.json()
        detail = format_detail(body.get("detail")) if isinstance(body, dict) else ""
    except ValueError:
        pass
    if resp.status_code == 401:
        raise AuthError(detail or "Unauthorized")
    raise ApiError(resp.status_code, detail or resp.reason_phrase)


class Session:
    """Token holder. Thread-safe; at most one login/refresh at a time.

    The access token lives in memory only. The refresh token is also written to
    `store` after every rotation so the session survives a restart. The password
    is used for one login request and never kept.
    """

    def __init__(
        self,
        http: httpx.Client,
        store: TokenStore,
        base_url: str,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._http = http
        self._store_file = store
        self._base_url = base_url
        self._clock = clock
        self._lock = threading.RLock()
        self._access: str | None = None
        self._refresh: str | None = None
        self._expires_at = 0.0
        self._issued_at = 0.0
        self._disk_stale = False  # last save failed: the file holds a spent token

    def has_fresh_token(self) -> bool:
        """True if the access token is valid beyond the refresh margin."""
        return (
            self._access is not None
            and self._clock() < self._expires_at - REFRESH_MARGIN_SECONDS
        )

    def ensure_token(self) -> str:
        """A valid access token, restoring or refreshing only when needed."""
        with self._lock:
            if self._access is None:
                self._restore()
            elif self._clock() >= self._expires_at - REFRESH_MARGIN_SECONDS:
                self._refresh_session()
            assert self._access is not None
            return self._access

    def refresh_after_401(self, failed_token: str) -> str:
        """Recover from a 401. Reuses the token if another thread already rotated it."""
        with self._lock:
            if self._access is not None and self._access != failed_token:
                return self._access
            self._refresh_session()
            assert self._access is not None
            return self._access

    def login(self, email: str, password: str) -> None:
        """Log in with credentials and persist the refresh token."""
        with self._lock:
            try:
                resp = self._post(
                    "/auth/login", json={"email": email, "password": password}
                )
            except AuthError as exc:
                raise AuthError("Invalid email or password.") from exc
            self._store(resp)
            log.info("logged in")

    def signup(self, email: str, password: str, username: str) -> SignupResponse:
        """Create an account. Adopts the returned tokens when the server issues them."""
        try:
            resp = self._post(
                "/auth/signup",
                json={"email": email, "password": password, "username": username},
            )
        except ApiError as exc:
            if exc.status_code == 409:
                raise ApiError(409, "That username or email is already taken.") from exc
            raise
        try:
            result = SignupResponse.from_dict(resp.json())
        except ValueError as exc:
            raise ServerError("Unexpected response from /auth/signup") from exc
        if result.tokens is not None:
            self.adopt_tokens(result.tokens)
        log.info("signed up (confirmation_required=%s)", result.confirmation_required)
        return result

    def forgot_password(self, email: str) -> None:
        """Ask the server to email a reset link. The response never says if the account exists."""
        try:
            self._post("/auth/forgot-password", json={"email": email})
        except ApiError as exc:
            if exc.status_code == 429:
                raise ApiError(
                    429, "Too many reset requests. Try again later."
                ) from exc
            if exc.status_code == 422:
                raise ApiError(422, "Enter a valid email address.") from exc
            raise
        log.info("password reset requested")

    def logout(self) -> None:
        """Best-effort server logout, then always clear local state and the stored token."""
        with self._lock:
            try:
                token = self.ensure_token()
            except AnkiHelperError as exc:
                log.info("no session to end on the server: %s", exc)
                token = None
            if token is not None:
                try:
                    self._post(
                        "/auth/logout", headers={"Authorization": f"Bearer {token}"}
                    )
                except AnkiHelperError as exc:
                    log.info("logout request failed: %s", exc)
            self._clear()
            self._store_file.clear()

    def account_deleted(self) -> None:
        """The account is gone server-side: drop local tokens without calling logout."""
        with self._lock:
            self._clear()
            self._store_file.clear()
        log.info("account deleted")

    def _clear(self) -> None:
        self._access = None
        self._refresh = None
        self._expires_at = 0.0
        self._issued_at = 0.0
        self._disk_stale = False

    def adopt_tokens(self, tokens: TokenResponse) -> None:
        """Use tokens obtained elsewhere (e.g. signup) as the current session."""
        with self._lock:
            self._set_tokens(tokens)

    def _set_tokens(self, tokens: TokenResponse) -> None:
        self._access = tokens.access_token
        self._refresh = tokens.refresh_token
        self._issued_at = self._clock()
        self._expires_at = self._issued_at + tokens.expires_in
        self._disk_stale = not self._store_file.save(
            tokens.refresh_token, self._base_url
        )

    def _store(self, resp: httpx.Response) -> None:
        try:
            tokens = TokenResponse.from_dict(resp.json())
        except ValueError as exc:  # bad shape and JSON errors
            raise ServerError("Unexpected response from /auth") from exc
        self._set_tokens(tokens)

    def _restore(self) -> None:
        """Start from the stored refresh token (or fail as not logged in)."""
        if self._refresh is None:
            self._refresh = self._store_file.load(self._base_url)
        if self._refresh is None:
            raise NotLoggedInError()
        self._refresh_session()

    def _refresh_session(self) -> None:
        # Prefer the file: another Anki process may have rotated the token since we
        # loaded it, and sending a spent token would make the server revoke the session.
        # Unless our last save failed: then the file holds a token we already spent.
        if self._disk_stale and self._refresh:
            log.info("session file is stale (last save failed); using in-memory token")
            refresh_token = self._refresh
        else:
            refresh_token = self._store_file.load(self._base_url) or self._refresh
        self._access = None
        if refresh_token is None:
            self._clear()
            raise NotLoggedInError()
        try:
            self._store(
                self._post("/auth/refresh", json={"refresh_token": refresh_token})
            )
        except AuthError as exc:
            # Rejected or already-used refresh token: the user has to log in again.
            age = (
                f"{round(self._clock() - self._issued_at)} s"
                if self._issued_at
                else "unknown (restored from disk)"
            )
            log.info("refresh rejected (%s); token age %s", exc, age)
            self._clear()
            self._store_file.clear()
            raise NotLoggedInError(
                "Your AnkiHelper session expired. Use Tools > AnkiHelper > Log in."
            ) from exc
        log.info("token refreshed")

    def _post(self, path: str, **kwargs) -> httpx.Response:
        try:
            resp = self._http.post(path, **kwargs)
        except httpx.TimeoutException as exc:
            raise NetworkError("The AnkiHelper server timed out.") from exc
        except httpx.TransportError as exc:
            raise NetworkError("Could not reach the AnkiHelper server.") from exc
        return check_response(resp)
