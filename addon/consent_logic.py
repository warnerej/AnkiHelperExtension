"""Wording and rules for the opt-in to stat sharing. Free of aqt for plain-Python tests."""

from typing import Any

CONSENT_KEY = "sharing_consent"

CONSENT_TITLE = "AnkiHelper: share your stats?"
CONSENT_MESSAGE = (
    "AnkiHelper uploads these to the AnkiHelper server:\n\n"
    "  • your email address and username\n"
    "  • per day for the last 365 days: number of reviews, retention and "
    "minutes studied\n\n"
    "Members of any group you join can see your username and those daily "
    "stats. Card contents, deck names and review times are never uploaded.\n\n"
    "Until you agree, nothing is uploaded. You can stop at any time from "
    "Tools > AnkiHelper > Sharing, and remove your data with Delete account.\n\n"
    "Share your stats?"
)


def has_consent(config: dict[str, Any] | None) -> bool:
    return bool((config or {}).get(CONSENT_KEY) is True)


def with_consent(config: dict[str, Any] | None, value: bool) -> dict[str, Any]:
    return {**(config or {}), CONSENT_KEY: value}
