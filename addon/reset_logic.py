"""Decisions behind the Forgot password flow.

Free of aqt so it can be tested in plain Python.
"""

import re

RESET_SENT_MESSAGE = (
    "If an account exists for that email, a reset link has been sent. "
    "Check your inbox and spam folder."
)

_EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


def validate_reset_email(email: str) -> str | None:
    """A problem message, or None when the reset request may be sent."""
    if not email.strip():
        return "Enter your email."
    if not _EMAIL_RE.fullmatch(email.strip()):
        return "Enter a valid email address."
    return None
