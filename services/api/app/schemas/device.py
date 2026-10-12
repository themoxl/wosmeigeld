from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class PushTokenRegister(BaseModel):
    expo_token: str = Field(min_length=1)
    platform: Literal["ios", "android"]
    device_name: str | None = None


class PushTokenUnregister(BaseModel):
    expo_token: str = Field(min_length=1)


class PushTokenResponse(BaseModel):
    id: UUID
    platform: str
    is_valid: bool
