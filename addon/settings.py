import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from .errors import NotConfiguredError
from .log import get_logger

log = get_logger()

USERNAME_RE = re.compile(r"[a-z0-9_]{3,20}")
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
DEFAULT_TIMEOUT = 15.0


LEGACY_KEYS = ("email", "password", "username")


def strip_legacy_credentials(
    config: dict[str, Any] | None,
) -> tuple[dict[str, Any], bool]:
    """Config without the keys that used to hold account details, and whether a
    password was present (the cue for the one-time "please log in" message)."""
    config = dict(config or {})
    had_password = bool(config.get("password"))
    for key in LEGACY_KEYS:
        config.pop(key, None)
    return config, had_password


def strip_friends(config: dict[str, Any] | None) -> tuple[dict[str, Any], bool]:
    """Config without the retired `friends` list, and whether it listed anyone
    (the cue for the one-time "sharing now works through groups" note)."""
    config = dict(config or {})
    had_friends = bool(config.pop("friends", None))
    return config, had_friends


@dataclass(frozen=True)
class Settings:
    base_url: str
    timeout_seconds: float = DEFAULT_TIMEOUT

    @classmethod
    def from_config(cls, config: dict[str, Any] | None) -> "Settings":
        config = config or {}
        base_url = str(config.get("base_url") or "").strip().rstrip("/")
        if not base_url:
            raise NotConfiguredError(
                "AnkiHelper: set base_url in Tools > Add-ons > Config."
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
        return cls(base_url, timeout)
