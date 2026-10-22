"""Decisions behind the Groups screen: validation, which controls to show, messages.

Free of aqt so it can be tested in plain Python.
"""

from dataclasses import dataclass

from .errors import AnkiHelperError, ApiError, NetworkError
from .models import Group
from .settings import USERNAME_RE

OWNER = "owner"
MAX_GROUP_NAME = 50

JOIN_CONFIRMATION = (
    "Joining shares your last year of review stats with everyone in this group, "
    "and lets you see theirs."
)
LEAVE_CONFIRMATION = (
    "Leave this group? You will stop seeing its members' stats and they will "
    "stop seeing yours."
)
STALE_HINT = (
    "The request may or may not have reached the server. The list has been "
    "refreshed; check it before trying again."
)


def validate_group_name(raw: str) -> tuple[str, str | None]:
    """The trimmed name and a problem message (None when valid)."""
    name = " ".join(raw.split())
    if not name:
        return name, "Enter a group name."
    if len(name) > MAX_GROUP_NAME:
        return name, f"Group names are at most {MAX_GROUP_NAME} characters."
    return name, None


def validate_invite_code(raw: str) -> tuple[str, str | None]:
    code = "".join(raw.split())
    if not code:
        return code, "Enter an invite code."
    return code, None


def validate_username(raw: str) -> tuple[str, str | None]:
    username = raw.strip().lower()
    if not USERNAME_RE.fullmatch(username):
        return username, "Usernames are 3-20 letters, digits or _."
    return username, None


@dataclass(frozen=True)
class DetailControls:
    leave: bool
    invite_code: bool


def detail_controls(group: Group, role: str | None = None) -> DetailControls:
    """Owner controls follow the role the group list reports for the viewer.

    Without a role (not in the list yet), fall back to the server only sending
    the invite code to owners.
    """
    owner = group.invite_code is not None if role is None else role == OWNER
    return DetailControls(leave=not owner, invite_code=owner)


def transfer_candidates(group: Group) -> list[str]:
    """Joined members other than the current owner."""
    return [m.username for m in group.members if m.username != group.owner]


def can_remove_allowed(group: Group, username: str) -> bool:
    return username != group.owner


def remove_allowed_confirmation(group: Group, username: str) -> str:
    joined = any(m.username == username for m in group.members)
    text = f"Remove {username} from the allowed list?"
    if joined:
        text += (
            f" They have joined '{group.name}', so they will also be removed "
            "from it, and you will stop seeing each other's stats."
        )
    return text


def delete_confirmed(group: Group, typed: str) -> bool:
    return typed.strip() == group.name


def delete_prompt(group: Group) -> str:
    return (
        f"This permanently deletes '{group.name}' for all its members.\n\n"
        "Type the group name to confirm:"
    )


def error_message(exc: BaseException, *, mutation: bool = False) -> str:
    """User-facing text. After a network failure a non-retried write may have landed."""
    if isinstance(exc, ApiError):
        text = exc.detail
    elif isinstance(exc, AnkiHelperError):
        text = str(exc)
    else:
        text = "Unexpected error. See user_files/ankihelper.log."
    if mutation and isinstance(exc, NetworkError):
        text += f"\n\n{STALE_HINT}"
    return text


def needs_refresh(exc: BaseException) -> bool:
    return isinstance(exc, NetworkError)
