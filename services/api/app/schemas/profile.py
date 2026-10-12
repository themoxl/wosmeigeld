from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from app.schemas.household import Membership

DateFormat = Literal["iso", "de", "us"]


class Me(BaseModel):
    user_id: UUID
    display_name: str | None
    language: str
    timezone: str
    currency: str
    date_format: DateFormat
    households: list[Membership]


class ProfileUpdate(BaseModel):
    date_format: DateFormat | None = None
