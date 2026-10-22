"""Decisions behind the Delete account dialog.

Free of aqt so it can be tested in plain Python.
"""

DELETE_WARNING = (
    "This permanently deletes your AnkiHelper account, all your review history "
    "and your group memberships. Groups you own pass to the member who has been "
    "in them longest, or are deleted if nobody else has joined. This cannot be undone."
)


def validate_delete(
    expected_email: str | None, typed_email: str, password: str
) -> str | None:
    """A problem message, or None when the deletion may go ahead."""
    if not expected_email:
        return "Could not look up your account email. Log in again and retry."
    if typed_email.strip().lower() != expected_email.strip().lower():
        return "The email does not match your account."
    if not password:
        return "Enter your password."
    return None
