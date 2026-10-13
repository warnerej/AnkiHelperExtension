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
    updated_at: datetime
