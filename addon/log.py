import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOGGER_NAME = "ankihelper"

_PATTERNS = [
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer [REDACTED]"),
    (
        re.compile(
            r"""(?i)(["']?(?:access_token|refresh_token|password|authorization)["']?\s*[:=]\s*)(["']?)[^"',\s}&]+\2"""
        ),
        r"\1[REDACTED]",
    ),
]


def redact(text: str) -> str:
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text


class RedactingFormatter(logging.Formatter):
    """Scrubs the finished line, so the message, traceback and stack_info are all
    covered and the record itself is left untouched for other handlers."""

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def get_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)


def setup_logging(log_dir: Path, debug: bool = False) -> logging.Logger:
    """Idempotent: reconfigures level, replaces handlers."""
    logger = get_logger()
    logger.setLevel(logging.DEBUG if debug else logging.INFO)
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        log_dir / "ankihelper.log", maxBytes=256 * 1024, backupCount=2, encoding="utf-8"
    )
    handler.setFormatter(RedactingFormatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    return logger
