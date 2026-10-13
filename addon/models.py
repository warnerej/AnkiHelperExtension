from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


class TokenResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    access_token: str
    refresh_token: str
    expires_in: int = 3600


class UserResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str | None = None
    email: str | None = None


class DailyReview(BaseModel):
    model_config = ConfigDict(extra="ignore")

    study_date: date
    total_reviews: int
    retention: float | None = None
    study_minutes: float | None = None
    updated_at: datetime


class SignupResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    user: UserResponse
    tokens: TokenResponse | None = None
    confirmation_required: bool = False


class FriendDay(BaseModel):
    model_config = ConfigDict(extra="ignore")

    study_date: date
    total_reviews: int
    retention: float | None = None
    study_minutes: float | None = None


class ProfileReviews(BaseModel):
    model_config = ConfigDict(extra="ignore")

    username: str
    start: date
    end: date
    days: list[FriendDay] = []
