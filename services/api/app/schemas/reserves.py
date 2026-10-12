"""API-Vertraege fuer bestaetigte Ruecklagenstaende und -bewegungen."""

from datetime import date
from datetime import datetime as DateTime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

ReserveKind = Literal["free", "contract", "goal"]
ReserveStatus = Literal["open", "partial", "done", "skipped"]


class ReserveMovementResponse(BaseModel):
    id: UUID
    batch_id: UUID
    occurred_on: date
    kind: Literal["deposit", "withdrawal", "reallocation"]
    amount_minor: int
    note: str | None
    created_at: DateTime


class ReservePotResponse(BaseModel):
    id: UUID
    kind: ReserveKind
    source_id: UUID | None
    name: str
    frequency: Literal["quarterly", "semiannual", "yearly"] | None = None
    target_amount_minor: int | None = None
    target_date: date | None = None
    next_due_date: date | None = None
    recommended_minor: int
    deposited_minor: int
    withdrawn_minor: int
    open_minor: int
    balance_minor: int
    next_month_recommended_minor: int
    status: ReserveStatus
    is_included_in_planning: bool
    movements: list[ReserveMovementResponse] = []


class ReserveOverviewResponse(BaseModel):
    year: int
    month: int
    recommended_minor: int
    deposited_minor: int
    open_minor: int
    total_balance_minor: int
    fixed_reserve_recommended_minor: int
    goal_reserve_recommended_minor: int
    free_reserve_balance_minor: int
    pots: list[ReservePotResponse]


class ReserveAllocationInput(BaseModel):
    pot_id: UUID
    amount_minor: int = Field(gt=0)


class ReserveDepositRequest(BaseModel):
    occurred_on: date
    total_minor: int = Field(gt=0)
    allocations: list[ReserveAllocationInput] = Field(min_length=1)
    note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def allocations_match_total(self) -> "ReserveDepositRequest":
        if sum(item.amount_minor for item in self.allocations) != self.total_minor:
            raise ValueError("Die Verteilung muss exakt dem Gesamtbetrag entsprechen.")
        if len({item.pot_id for item in self.allocations}) != len(self.allocations):
            raise ValueError("Jeder Topf darf pro Einzahlung nur einmal vorkommen.")
        return self


class ReserveWithdrawalRequest(BaseModel):
    pot_id: UUID
    amount_minor: int = Field(gt=0)
    occurred_on: date
    note: str | None = Field(default=None, max_length=500)
    contract_occurrence_id: UUID | None = None


class ReserveReallocationRequest(BaseModel):
    occurred_on: date
    allocations: list[ReserveAllocationInput] = Field(min_length=1)
    note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def unique_targets(self) -> "ReserveReallocationRequest":
        if len({item.pot_id for item in self.allocations}) != len(self.allocations):
            raise ValueError("Jeder Zieltopf darf nur einmal vorkommen.")
        return self


class ReserveSkipRequest(BaseModel):
    pot_id: UUID
    year: int = Field(ge=2000, le=2200)
    month: int = Field(ge=1, le=12)


class ReserveBatchResponse(BaseModel):
    id: UUID
    occurred_on: date
    kind: Literal["deposit", "withdrawal", "reallocation"]
    total_minor: int
    note: str | None
    allocations: list[ReserveAllocationInput]


class ReserveDistributionPreviewRequest(BaseModel):
    year: int = Field(ge=2000, le=2200)
    month: int = Field(ge=1, le=12)
    total_minor: int = Field(gt=0)


class ReserveDistributionPreviewResponse(BaseModel):
    total_minor: int
    allocations: list[ReserveAllocationInput]
    free_remainder_minor: int
