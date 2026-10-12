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
