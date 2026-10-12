from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

HouseholdRoleLiteral = Literal["owner", "editor", "viewer", "child"]


class HouseholdCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)


class HouseholdUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=160)


class Household(BaseModel):
    id: UUID
    name: str
    base_currency: str
    timezone: str
    budget_method: str
    fiscal_year_start_month: int
    status: str
    role: str


class Membership(BaseModel):
    household_id: UUID
    name: str
    role: str


class HouseholdMember(BaseModel):
    user_id: UUID
    role: str
    status: str
    invited_at: datetime
    joined_at: datetime | None
    display_name: str | None


class HouseholdInviteCreate(BaseModel):
    role: HouseholdRoleLiteral = "editor"


class HouseholdInvite(BaseModel):
    id: UUID
    household_id: UUID
    code: str
    role: str
    created_by: UUID
    created_at: datetime
    expires_at: datetime
    revoked_at: datetime | None
    used_by: UUID | None
    used_at: datetime | None


class HouseholdInvitePreview(BaseModel):
    household_id: UUID
    household_name: str
    role: str
    expires_at: datetime


class HouseholdInviteRedeem(BaseModel):
    code: str = Field(min_length=1, max_length=32)
