"""Response models: frozen dataclasses parsed from JSON dicts.

Each `from_dict` ignores unknown keys, applies defaults for optional fields and raises
ValueError on a missing or wrongly-typed field (callers map that to ServerError).
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Self

_MISSING: Any = object()


def _str(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError(f"expected string, got {type(value).__name__}")
    return value


def _int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"expected integer, got {type(value).__name__}")
    return value


def _float(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"expected number, got {type(value).__name__}")
    return float(value)


def _bool(value: Any) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"expected boolean, got {type(value).__name__}")
    return value


def _date(value: Any) -> date:
    return date.fromisoformat(_str(value))


def _datetime(value: Any) -> datetime:
    return datetime.fromisoformat(_str(value))


def _obj(data: Any, model: str) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError(f"{model}: expected object, got {type(data).__name__}")
    return data


def _get[T](data: dict[str, Any], model: str, key: str, parse: Callable[[Any], T], default: Any = _MISSING) -> T:
    """Parse `data[key]`. A missing key uses `default`, or raises when there is none.

    An explicit null counts as missing for fields that have a default.
    """
    value = data.get(key)
    if value is None:
        if default is _MISSING:
            raise ValueError(f"{model}.{key} is required")
        return default
    try:
        return parse(value)
    except ValueError as exc:
        raise ValueError(f"{model}.{key}: {exc}") from exc


@dataclass(frozen=True)
class TokenResponse:
    access_token: str
    refresh_token: str
    expires_in: int = 3600

    @classmethod
    def from_dict(cls, data: Any) -> Self:
        d = _obj(data, "TokenResponse")
        return cls(
            access_token=_get(d, "TokenResponse", "access_token", _str),
            refresh_token=_get(d, "TokenResponse", "refresh_token", _str),
            expires_in=_get(d, "TokenResponse", "expires_in", _int, 3600),
        )


@dataclass(frozen=True)
class UserResponse:
    id: str | None = None
    email: str | None = None

    @classmethod
    def from_dict(cls, data: Any) -> Self:
        d = _obj(data, "UserResponse")
        return cls(
            id=_get(d, "UserResponse", "id", _str, None),
            email=_get(d, "UserResponse", "email", _str, None),
        )


@dataclass(frozen=True)
class DailyReview:
    study_date: date
    total_reviews: int
    updated_at: datetime
    retention: float | None = None
    study_minutes: float | None = None

    @classmethod
    def from_dict(cls, data: Any) -> Self:
        d = _obj(data, "DailyReview")
        return cls(
            study_date=_get(d, "DailyReview", "study_date", _date),
            total_reviews=_get(d, "DailyReview", "total_reviews", _int),
            retention=_get(d, "DailyReview", "retention", _float, None),
            study_minutes=_get(d, "DailyReview", "study_minutes", _float, None),
            updated_at=_get(d, "DailyReview", "updated_at", _datetime),
        )


@dataclass(frozen=True)
class SignupResponse:
    user: UserResponse
    tokens: TokenResponse | None = None
    confirmation_required: bool = False

    @classmethod
    def from_dict(cls, data: Any) -> Self:
        d = _obj(data, "SignupResponse")
        return cls(
            user=_get(d, "SignupResponse", "user", UserResponse.from_dict),
            tokens=_get(d, "SignupResponse", "tokens", TokenResponse.from_dict, None),
            confirmation_required=_get(d, "SignupResponse", "confirmation_required", _bool, False),
        )


@dataclass(frozen=True)
class FriendDay:
    study_date: date
    total_reviews: int
    retention: float | None = None
    study_minutes: float | None = None

    @classmethod
    def from_dict(cls, data: Any) -> Self:
        d = _obj(data, "FriendDay")
        return cls(
            study_date=_get(d, "FriendDay", "study_date", _date),
            total_reviews=_get(d, "FriendDay", "total_reviews", _int),
            retention=_get(d, "FriendDay", "retention", _float, None),
            study_minutes=_get(d, "FriendDay", "study_minutes", _float, None),
        )


def _friend_days(value: Any) -> list[FriendDay]:
    if not isinstance(value, list):
        raise ValueError(f"expected list, got {type(value).__name__}")
    return [FriendDay.from_dict(item) for item in value]


@dataclass(frozen=True)
class ProfileReviews:
    username: str
    start: date
    end: date
    days: list[FriendDay] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Any) -> Self:
        d = _obj(data, "ProfileReviews")
        return cls(
            username=_get(d, "ProfileReviews", "username", _str),
            start=_get(d, "ProfileReviews", "start", _date),
            end=_get(d, "ProfileReviews", "end", _date),
            days=_get(d, "ProfileReviews", "days", _friend_days, None) or [],
        )
