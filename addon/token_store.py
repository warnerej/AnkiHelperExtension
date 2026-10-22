import json
import os
from pathlib import Path

from .log import get_logger

log = get_logger()


class TokenStore:
    """The refresh token on disk (the only credential ever persisted).

    Refresh tokens rotate and are single use, so `save` is atomic: a crash leaves
    either the old or the new token, never a torn file.
    """

    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self, base_url: str) -> str | None:
        """The stored refresh token for `base_url`, or None if absent or unusable."""
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            log.info("ignoring unreadable session file")
            self.clear()
            return None
        token = data.get("refresh_token") if isinstance(data, dict) else None
        if not isinstance(token, str) or not token:
            self.clear()
            return None
        if data.get("base_url") != base_url:
            return None  # belongs to another server; leave it for that server
        return token

    def save(self, refresh_token: str, base_url: str) -> bool:
        """Write atomically; True if written. Failures are logged, not raised: the in-memory session still works."""
        tmp = self._path.with_name(self._path.name + ".tmp")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"refresh_token": refresh_token, "base_url": base_url}, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self._path)
        except OSError as exc:
            log.info("could not save session file: %s", exc)
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            return False
        return True

    def clear(self) -> None:
        try:
            self._path.unlink(missing_ok=True)
        except OSError as exc:
            log.info("could not delete session file: %s", exc)
