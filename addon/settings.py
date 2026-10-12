from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from .errors import NotConfiguredError

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
DEFAULT_TIMEOUT = 15.0


@dataclass(frozen=True)
class Settings:
    base_url: str
    email: str
    password: str
    timeout_seconds: float = DEFAULT_TIMEOUT

    def __repr__(self) -> str:  # keep the password out of tracebacks and logs
        return (
            f"Settings(base_url={self.base_url!r}, email={self.email!r}, password=***)"
        )

    @classmethod
    def from_config(cls, config: dict[str, Any] | None) -> "Settings":
        config = config or {}
        base_url = str(config.get("base_url") or "").strip().rstrip("/")
        email = str(config.get("email") or "").strip()
        password = str(config.get("password") or "")
        if not (base_url and email and password):
            raise NotConfiguredError(
                "AnkiHelper: set base_url, email and password in Tools > Add-ons > Config."
            )
        parsed = urlparse(base_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise NotConfiguredError("AnkiHelper: base_url must be a full URL.")
        if parsed.scheme == "http" and parsed.hostname not in _LOCAL_HOSTS:
            raise NotConfiguredError("AnkiHelper: base_url must use https.")
        try:
            timeout = float(config.get("timeout_seconds", DEFAULT_TIMEOUT))
        except (TypeError, ValueError):
            timeout = DEFAULT_TIMEOUT
        if timeout <= 0:
            timeout = DEFAULT_TIMEOUT
        return cls(base_url, email, password, timeout)
