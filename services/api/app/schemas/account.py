from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

AccountType = Literal[
    "checking", "credit_card", "savings", "cash", "brokerage", "loan", "real_estate", "other"
]


class AccountCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    account_type: AccountType
    institution: str | None = None
    currency: str = "EUR"
    on_budget: bool = True
    opening_balance_minor: int = 0
    opening_balance_date: date | None = None
    # Zaehlt das Konto zur Liquiditaetsvorschau? None = Heuristik nach
    # account_type (checking, cash). Kontoarten sind als Auswahl zu grob:
    # Tagesgeld und Bausparvertrag sind beide 'savings'.
    forecast_liquidity: bool | None = None


class AccountUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    institution: str | None = None
    is_closed: bool | None = None
    # Ausdruecklich auf None setzbar - das ist die Rueckkehr zur Heuristik und
    # kein "nicht angegeben"; deshalb zaehlt hier exclude_unset, nicht None.
    forecast_liquidity: bool | None = None


class Account(BaseModel):
    id: UUID
    name: str
    account_type: str
    institution: str | None
    currency: str
    on_budget: bool
    opening_balance_minor: int
    opening_balance_date: date | None
    is_closed: bool
    forecast_liquidity: bool | None = None


class ReconcileInput(BaseModel):
    as_of_date: date
    statement_balance_minor: int


class Reconciliation(BaseModel):
    id: UUID
    account_id: UUID
    as_of_date: date
    statement_balance_minor: int
    computed_balance_minor: int
    difference_minor: int
    status: Literal["completed", "discrepancy"]
