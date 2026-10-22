from dataclasses import FrozenInstanceError
from datetime import date, datetime

import pytest

from addon.models import (
    DailyReview,
    HistoryResult,
    HistoryStatus,
    ReviewDay,
    SignupResponse,
    TokenResponse,
    UserResponse,
)

TOKENS = {"access_token": "a", "refresh_token": "r", "expires_in": 60}
DAY = {
    "study_date": "2026-01-02",
    "total_reviews": 5,
    "retention": 0.9,
    "study_minutes": 12,
}
DAILY = {**DAY, "updated_at": "2026-01-02T10:00:00+00:00"}


class TestTokenResponse:
    def test_valid(self):
        t = TokenResponse.from_dict(TOKENS)
        assert (t.access_token, t.refresh_token, t.expires_in) == ("a", "r", 60)

    def test_extra_keys_and_default(self):
        t = TokenResponse.from_dict(
            {"access_token": "a", "refresh_token": "r", "token_type": "bearer"}
        )
        assert t.expires_in == 3600

    @pytest.mark.parametrize("key", ["access_token", "refresh_token"])
    def test_missing(self, key):
        with pytest.raises(ValueError, match=key):
            TokenResponse.from_dict({k: v for k, v in TOKENS.items() if k != key})

    @pytest.mark.parametrize(
        "patch",
        [
            {"access_token": 1},
            {"expires_in": "60"},
            {"expires_in": True},
            {"expires_in": 1.5},
        ],
    )
    def test_bad_types(self, patch):
        with pytest.raises(ValueError):
            TokenResponse.from_dict({**TOKENS, **patch})

    @pytest.mark.parametrize("bad", [None, [], "x", 3])
    def test_not_an_object(self, bad):
        with pytest.raises(ValueError):
            TokenResponse.from_dict(bad)

    def test_frozen(self):
        with pytest.raises(FrozenInstanceError):
            TokenResponse.from_dict(TOKENS).access_token = "x"  # type: ignore[misc]


class TestUserResponse:
    def test_valid(self):
        u = UserResponse.from_dict({"id": "1", "email": "a@b.c", "extra": 1})
        assert (u.id, u.email) == ("1", "a@b.c")

    def test_defaults(self):
        assert UserResponse.from_dict({}) == UserResponse(None, None)

    def test_bad_types(self):
        with pytest.raises(ValueError):
            UserResponse.from_dict({"id": 1})
        with pytest.raises(ValueError):
            UserResponse.from_dict("nope")


class TestDailyReview:
    def test_valid(self):
        r = DailyReview.from_dict(DAILY)
        assert r.study_date == date(2026, 1, 2)
        assert r.updated_at == datetime.fromisoformat("2026-01-02T10:00:00+00:00")
        assert (r.total_reviews, r.retention, r.study_minutes) == (5, 0.9, 12.0)

    def test_extra_keys_and_optionals(self):
        r = DailyReview.from_dict(
            {
                "study_date": "2026-01-02",
                "total_reviews": 0,
                "updated_at": "2026-01-02T10:00:00Z",
                "id": "x",
            }
        )
        assert r.retention is None and r.study_minutes is None

    @pytest.mark.parametrize("key", ["study_date", "total_reviews", "updated_at"])
    def test_missing(self, key):
        with pytest.raises(ValueError, match=key):
            DailyReview.from_dict({k: v for k, v in DAILY.items() if k != key})

    @pytest.mark.parametrize(
        "patch",
        [
            {"study_date": "not-a-date"},
            {"study_date": 20260102},
            {"updated_at": "yesterday"},
            {"total_reviews": "5"},
            {"retention": "high"},
            {"study_minutes": True},
        ],
    )
    def test_bad_types(self, patch):
        with pytest.raises(ValueError):
            DailyReview.from_dict({**DAILY, **patch})


class TestSignupResponse:
    def test_with_tokens(self):
        s = SignupResponse.from_dict(
            {
                "user": {"id": "1"},
                "tokens": TOKENS,
                "confirmation_required": True,
                "x": 1,
            }
        )
        assert (
            s.user.id == "1"
            and s.tokens == TokenResponse("a", "r", 60)
            and s.confirmation_required
        )

    def test_defaults(self):
        s = SignupResponse.from_dict({"user": {}})
        assert s.tokens is None and s.confirmation_required is False
        assert SignupResponse.from_dict({"user": {}, "tokens": None}).tokens is None

    def test_missing_user(self):
        with pytest.raises(ValueError, match="user"):
            SignupResponse.from_dict({"tokens": TOKENS})

    @pytest.mark.parametrize(
        "patch",
        [
            {"user": "bob"},
            {"tokens": {"access_token": "a"}},
            {"tokens": "x"},
            {"confirmation_required": "yes"},
        ],
    )
    def test_bad_types(self, patch):
        with pytest.raises(ValueError):
            SignupResponse.from_dict({"user": {}, **patch})


class TestReviewDay:
    def test_valid(self):
        d = ReviewDay.from_dict({**DAY, "extra": 1})
        assert d == ReviewDay(date(2026, 1, 2), 5, 0.9, 12.0)

    def test_optionals_default(self):
        d = ReviewDay.from_dict({"study_date": "2026-01-02", "total_reviews": 1})
        assert d.retention is None and d.study_minutes is None

    @pytest.mark.parametrize("key", ["study_date", "total_reviews"])
    def test_missing(self, key):
        with pytest.raises(ValueError, match=key):
            ReviewDay.from_dict({k: v for k, v in DAY.items() if k != key})

    @pytest.mark.parametrize(
        "patch",
        [{"study_date": "2026-13-45"}, {"total_reviews": 2.5}, {"retention": []}],
    )
    def test_bad_types(self, patch):
        with pytest.raises(ValueError):
            ReviewDay.from_dict({**DAY, **patch})


def test_group_models_defaults_and_errors():
    from addon.models import Group, GroupReviews

    g = Group.from_dict({"id": "g", "name": "n"})
    assert g.members == [] and g.invite_code is None and g.allowed is None
    with pytest.raises(ValueError, match="Group.name"):
        Group.from_dict({"id": "g"})
    with pytest.raises(ValueError):
        GroupReviews.from_dict({"group_id": "g", "start": "2026-01-01", "end": "bad"})


def test_group_reviews_year_for_and_total():
    from addon.models import GroupReviews, MemberReviews, YearReviews

    d1, d2 = date(2026, 1, 1), date(2026, 1, 2)
    a = MemberReviews("a", [ReviewDay(d1, 5, 90.0, 10.0), ReviewDay(d2, 1)])
    b = MemberReviews("b", [ReviewDay(d1, 7, None, 4.0)])
    g = GroupReviews("g", d1, d2, [a, b])
    assert g.year_for(a) == YearReviews("a", d1, d2, a.days)
    total = g.total()
    assert total.username == "Group total"
    assert total.days == [ReviewDay(d1, 12, None, 14.0), ReviewDay(d2, 1, None, None)]
    assert GroupReviews("g", d1, d2).total().days == []


class TestHistory:
    def test_result(self):
        r = HistoryResult.from_dict(
            {
                "start": "2025-02-01",
                "end": "2026-01-31",
                "days_written": 213,
                "history_synced_at": "2026-01-31T21:04:11+00:00",
            }
        )
        assert (r.start, r.end, r.days_written) == (
            date(2025, 2, 1),
            date(2026, 1, 31),
            213,
        )
        assert r.history_synced_at == datetime.fromisoformat(
            "2026-01-31T21:04:11+00:00"
        )

    def test_result_missing_field(self):
        with pytest.raises(ValueError, match="days_written"):
            HistoryResult.from_dict(
                {
                    "start": "2025-02-01",
                    "end": "2026-01-31",
                    "history_synced_at": "2026-01-31T00:00:00Z",
                }
            )

    def test_status_null_and_set(self):
        assert (
            HistoryStatus.from_dict({"history_synced_at": None}).history_synced_at
            is None
        )
        assert HistoryStatus.from_dict({}).history_synced_at is None
        s = HistoryStatus.from_dict({"history_synced_at": "2026-01-31T21:04:11+00:00"})
        assert s.history_synced_at is not None

    def test_status_bad_type(self):
        with pytest.raises(ValueError, match="history_synced_at"):
            HistoryStatus.from_dict({"history_synced_at": 1})
