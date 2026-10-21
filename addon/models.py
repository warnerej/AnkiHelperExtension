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
class ReviewDay:
    study_date: date
    total_reviews: int
    retention: float | None = None
    study_minutes: float | None = None

    @classmethod
    def from_dict(cls, data: Any) -> Self:
        d = _obj(data, "ReviewDay")
        return cls(
            study_date=_get(d, "ReviewDay", "study_date", _date),
            total_reviews=_get(d, "ReviewDay", "total_reviews", _int),
            retention=_get(d, "ReviewDay", "retention", _float, None),
            study_minutes=_get(d, "ReviewDay", "study_minutes", _float, None),
        )


@dataclass(frozen=True)
class YearReviews:
    """One person's daily rows over a date range: the input the heatmap draws."""

    username: str
    start: date
    end: date
    days: list[ReviewDay] = field(default_factory=list)


def _list[T](parse: Callable[[Any], T]) -> Callable[[Any], list[T]]:
    def parse_list(value: Any) -> list[T]:
        if not isinstance(value, list):
            raise ValueError(f"expected list, got {type(value).__name__}")
        return [parse(item) for item in value]

    return parse_list


@dataclass(frozen=True)
class GroupSummary:
    id: str
    name: str
    role: str
    member_count: int

    @classmethod
    def from_dict(cls, data: Any) -> Self:
        d = _obj(data, "GroupSummary")
        return cls(
            id=_get(d, "GroupSummary", "id", _str),
            name=_get(d, "GroupSummary", "name", _str),
            role=_get(d, "GroupSummary", "role", _str),
            member_count=_get(d, "GroupSummary", "member_count", _int),
        )


@dataclass(frozen=True)
class GroupMember:
    username: str
    role: str
    joined_at: datetime

    @classmethod
    def from_dict(cls, data: Any) -> Self:
        d = _obj(data, "GroupMember")
        return cls(
            username=_get(d, "GroupMember", "username", _str),
            role=_get(d, "GroupMember", "role", _str),
            joined_at=_get(d, "GroupMember", "joined_at", _datetime),
        )


@dataclass(frozen=True)
class Group:
    """`invite_code` and `allowed` are None unless the caller owns the group."""

    id: str
    name: str
    owner: str | None = None
    members: list[GroupMember] = field(default_factory=list)
    invite_code: str | None = None
    allowed: list[str] | None = None

    @classmethod
    def from_dict(cls, data: Any) -> Self:
        d = _obj(data, "Group")
        return cls(
            id=_get(d, "Group", "id", _str),
            name=_get(d, "Group", "name", _str),
            owner=_get(d, "Group", "owner", _str, None),
            members=_get(d, "Group", "members", _list(GroupMember.from_dict), None) or [],
            invite_code=_get(d, "Group", "invite_code", _str, None),
            allowed=_get(d, "Group", "allowed", _list(_str), None),
        )


@dataclass(frozen=True)
class InviteCode:
    invite_code: str

    @classmethod
    def from_dict(cls, data: Any) -> Self:
        d = _obj(data, "InviteCode")
        return cls(invite_code=_get(d, "InviteCode", "invite_code", _str))


@dataclass(frozen=True)
class MemberReviews:
    username: str
    days: list[ReviewDay] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Any) -> Self:
        d = _obj(data, "MemberReviews")
        return cls(
            username=_get(d, "MemberReviews", "username", _str),
            days=_get(d, "MemberReviews", "days", _list(ReviewDay.from_dict), None) or [],
        )


@dataclass(frozen=True)
class GroupReviews:
    group_id: str
    start: date
    end: date
    members: list[MemberReviews] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Any) -> Self:
        d = _obj(data, "GroupReviews")
        return cls(
            group_id=_get(d, "GroupReviews", "group_id", _str),
            start=_get(d, "GroupReviews", "start", _date),
            end=_get(d, "GroupReviews", "end", _date),
            members=_get(d, "GroupReviews", "members", _list(MemberReviews.from_dict), None) or [],
        )

    def year_for(self, member: MemberReviews) -> YearReviews:
        return YearReviews(member.username, self.start, self.end, member.days)

    def total(self, username: str = "Group total") -> YearReviews:
        """Everyone's reviews and study minutes summed per day (no combined retention)."""
        reviews: dict[date, int] = {}
        minutes: dict[date, float] = {}
        for member in self.members:
            for day in member.days:
                reviews[day.study_date] = reviews.get(day.study_date, 0) + day.total_reviews
                if day.study_minutes is not None:
                    minutes[day.study_date] = minutes.get(day.study_date, 0.0) + day.study_minutes
        days = [
            ReviewDay(d, reviews[d], None, minutes.get(d))
            for d in sorted(reviews)
        ]
        return YearReviews(username, self.start, self.end, days)
