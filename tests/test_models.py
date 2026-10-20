from dataclasses import FrozenInstanceError
from datetime import date, datetime

import pytest

from addon.models import DailyReview, FriendDay, ProfileReviews, SignupResponse, TokenResponse, UserResponse

TOKENS = {"access_token": "a", "refresh_token": "r", "expires_in": 60}
DAY = {"study_date": "2026-01-02", "total_reviews": 5, "retention": 0.9, "study_minutes": 12}
DAILY = {**DAY, "updated_at": "2026-01-02T10:00:00+00:00"}
PROFILE = {"username": "bob", "start": "2025-01-01", "end": "2026-01-01", "days": [DAY]}


class TestTokenResponse:
    def test_valid(self):
        t = TokenResponse.from_dict(TOKENS)
        assert (t.access_token, t.refresh_token, t.expires_in) == ("a", "r", 60)

    def test_extra_keys_and_default(self):
        t = TokenResponse.from_dict({"access_token": "a", "refresh_token": "r", "token_type": "bearer"})
        assert t.expires_in == 3600

    @pytest.mark.parametrize("key", ["access_token", "refresh_token"])
    def test_missing(self, key):
        with pytest.raises(ValueError, match=key):
            TokenResponse.from_dict({k: v for k, v in TOKENS.items() if k != key})

    @pytest.mark.parametrize(
        "patch", [{"access_token": 1}, {"expires_in": "60"}, {"expires_in": True}, {"expires_in": 1.5}]
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
            {"study_date": "2026-01-02", "total_reviews": 0, "updated_at": "2026-01-02T10:00:00Z", "id": "x"}
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
        s = SignupResponse.from_dict({"user": {"id": "1"}, "tokens": TOKENS, "confirmation_required": True, "x": 1})
        assert s.user.id == "1" and s.tokens == TokenResponse("a", "r", 60) and s.confirmation_required

    def test_defaults(self):
        s = SignupResponse.from_dict({"user": {}})
        assert s.tokens is None and s.confirmation_required is False
        assert SignupResponse.from_dict({"user": {}, "tokens": None}).tokens is None

    def test_missing_user(self):
        with pytest.raises(ValueError, match="user"):
            SignupResponse.from_dict({"tokens": TOKENS})

    @pytest.mark.parametrize(
        "patch",
        [{"user": "bob"}, {"tokens": {"access_token": "a"}}, {"tokens": "x"}, {"confirmation_required": "yes"}],
    )
    def test_bad_types(self, patch):
        with pytest.raises(ValueError):
            SignupResponse.from_dict({"user": {}, **patch})


class TestFriendDay:
    def test_valid(self):
        d = FriendDay.from_dict({**DAY, "extra": 1})
        assert d == FriendDay(date(2026, 1, 2), 5, 0.9, 12.0)

    def test_optionals_default(self):
        d = FriendDay.from_dict({"study_date": "2026-01-02", "total_reviews": 1})
        assert d.retention is None and d.study_minutes is None

    @pytest.mark.parametrize("key", ["study_date", "total_reviews"])
    def test_missing(self, key):
        with pytest.raises(ValueError, match=key):
            FriendDay.from_dict({k: v for k, v in DAY.items() if k != key})

    @pytest.mark.parametrize("patch", [{"study_date": "2026-13-45"}, {"total_reviews": 2.5}, {"retention": []}])
    def test_bad_types(self, patch):
        with pytest.raises(ValueError):
            FriendDay.from_dict({**DAY, **patch})


class TestProfileReviews:
    def test_valid(self):
        p = ProfileReviews.from_dict({**PROFILE, "extra": 1})
        assert (p.username, p.start, p.end) == ("bob", date(2025, 1, 1), date(2026, 1, 1))
        assert p.days == [FriendDay(date(2026, 1, 2), 5, 0.9, 12.0)]

    def test_days_default_is_not_shared(self):
        base = {"username": "bob", "start": "2025-01-01", "end": "2026-01-01"}
        a, b = ProfileReviews.from_dict(base), ProfileReviews.from_dict(base)
        assert a.days == [] and a.days is not b.days

    @pytest.mark.parametrize("key", ["username", "start", "end"])
    def test_missing(self, key):
        with pytest.raises(ValueError, match=key):
            ProfileReviews.from_dict({k: v for k, v in PROFILE.items() if k != key})

    @pytest.mark.parametrize(
        "patch",
        [
            {"username": 5},
            {"start": "soon"},
            {"days": "none"},
            {"days": {"a": 1}},
            {"days": ["2026-01-02"]},
            {"days": [{"study_date": "2026-01-02"}]},
        ],
    )
    def test_bad_types(self, patch):
        with pytest.raises(ValueError):
            ProfileReviews.from_dict({**PROFILE, **patch})
