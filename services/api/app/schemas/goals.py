from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.services.savings_planning import (
    PlanningMode,
    PlanningStatus,
    validate_included_plan,
)

GoalStatus = Literal["active", "reached", "cancelled"]
GoalEditStatus = Literal["active", "cancelled"]
WishlistStatus = Literal["open", "deferred", "purchased", "archived"]
AssetType = Literal["asset", "debt"]
AssetClass = Literal["real_estate", "vehicle", "depot", "cash", "loan", "credit", "other"]
TrafficLightStatus = Literal["green", "yellow", "red"]


# --- Sparziele ---
class SavingsGoalCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    target_amount_minor: int = Field(gt=0)
    starting_amount_minor: int = Field(default=0, ge=0)
    target_date: date | None = None
    planning_mode: PlanningMode | None = None
    monthly_contribution_minor: int | None = Field(default=None, gt=0)
    include_in_planning: bool | None = None
    priority: int = Field(default=3, ge=1, le=5)
    account_id: UUID | None = None
    category_id: UUID | None = None

    @model_validator(mode="after")
    def complete_planning_defaults(self) -> "SavingsGoalCreate":
        # Legacy-Clients senden nur target_date. Ein vorhandenes Datum ist
        # eindeutig Datumsmodus; ohne Datum und Monatsbetrag bleibt das Ziel
        # bewusst unvollstaendig und wird nicht still ins Monatsbudget gelegt.
        mode: PlanningMode
        if self.planning_mode is None:
            mode = (
                "monthly_amount"
                if self.monthly_contribution_minor is not None
                else "target_date"
            )
            self.planning_mode = mode
        else:
            mode = self.planning_mode
        if self.include_in_planning is None:
            self.include_in_planning = (
                (mode == "target_date" and self.target_date is not None)
                or (
                    mode == "monthly_amount"
                    and self.monthly_contribution_minor is not None
                )
            )
        validate_included_plan(
            planning_mode=mode,
            target_date=self.target_date,
            monthly_contribution_minor=self.monthly_contribution_minor,
            include_in_planning=self.include_in_planning,
        )
        return self


class SavingsGoalUpdate(BaseModel):
    name: str | None = None
    target_amount_minor: int | None = Field(default=None, gt=0)
    starting_amount_minor: int | None = Field(default=None, ge=0)
    target_date: date | None = None
    planning_mode: PlanningMode | None = None
    monthly_contribution_minor: int | None = Field(default=None, gt=0)
    include_in_planning: bool | None = None
    priority: int | None = Field(default=None, ge=1, le=5)
    # "reached" wird aus aktuellem Stand und Zielbetrag abgeleitet. Clients
    # koennen ein Ziel nur aktivieren/wiederherstellen oder archivieren.
    status: GoalEditStatus | None = None


class SavingsGoalResponse(BaseModel):
    id: UUID
    name: str
    target_amount_minor: int
    starting_amount_minor: int
    # Berechnet aus starting_amount_minor + Summe der zugeordneten,
    # nicht archivierten transaction_splits (Architekturplan 6.7). Kann durch
    # zugeordnete Ausgabe-Splits theoretisch negativ werden, daher kein
    # ge=0-Constraint mehr wie beim frueheren, manuell gepflegten Feld.
    current_amount_minor: int
    target_date: date | None
    monthly_required_minor: int
    planning_mode: PlanningMode
    monthly_contribution_minor: int | None
    include_in_planning: bool
    priority: int
    effective_monthly_reserve_minor: int
    projected_completion_date: date | None
    planning_status: PlanningStatus
    account_id: UUID | None
    category_id: UUID | None
    status: GoalStatus
    created_at: datetime


class SavingsPlanPreviewRequest(BaseModel):
    target_amount_minor: int = Field(gt=0)
    current_amount_minor: int = Field(default=0, ge=0)
    planning_mode: PlanningMode
    target_date: date | None = None
    monthly_contribution_minor: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_complete_plan(self) -> "SavingsPlanPreviewRequest":
        validate_included_plan(
            planning_mode=self.planning_mode,
            target_date=self.target_date,
            monthly_contribution_minor=self.monthly_contribution_minor,
            include_in_planning=True,
        )
        return self


class SavingsPlanPreviewResponse(BaseModel):
    target_amount_minor: int
    current_amount_minor: int
    remaining_amount_minor: int
    planning_mode: PlanningMode
    monthly_required_minor: int
    effective_monthly_reserve_minor: int
    projected_completion_date: date | None
    planning_status: PlanningStatus


# --- Wunschliste & Kaufampel ---
class WishlistItemCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    price_minor: int = Field(gt=0)
    priority: int = Field(default=1, ge=1, le=5)
    note: str | None = None
    goal_id: UUID | None = None


class WishlistItemUpdate(BaseModel):
    name: str | None = None
    price_minor: int | None = Field(default=None, gt=0)
    priority: int | None = Field(default=None, ge=1, le=5)
    note: str | None = None
    # Explizit auf null setzbar, um die Verknuepfung zum Sparziel zu loesen.
    goal_id: UUID | None = None
    status: WishlistStatus | None = None


class WishlistItemResponse(BaseModel):
    id: UUID
    name: str
    price_minor: int
    priority: int
    note: str | None
    goal_id: UUID | None
    status: WishlistStatus
    buy_traffic_light: TrafficLightStatus
    # Kennzahlen der Kaufampel (Architekturplan 6.7): Anteil des Preises, der
    # aus Sparzielguthaben plus freiem Monatsbudget gedeckt ist (0.0 - 1.0),
    # der danach offene Fehlbetrag und der fruehestmoegliche Kaufzeitpunkt.
    coverage_ratio: float
    shortfall_minor: int
    earliest_purchase_date: date | None
    created_at: datetime


class WishlistSavingsPlanCreate(BaseModel):
    planning_mode: PlanningMode
    target_date: date | None = None
    monthly_contribution_minor: int | None = Field(default=None, gt=0)
    starting_amount_minor: int = Field(default=0, ge=0)
    include_in_planning: bool = True

    @model_validator(mode="after")
    def validate_complete_plan(self) -> "WishlistSavingsPlanCreate":
        validate_included_plan(
            planning_mode=self.planning_mode,
            target_date=self.target_date,
            monthly_contribution_minor=self.monthly_contribution_minor,
            include_in_planning=self.include_in_planning,
        )
        return self


class WishlistSavingsPlanResponse(BaseModel):
    goal: SavingsGoalResponse
    wishlist_item: WishlistItemResponse


# --- Vermögen, Schulden & Net Worth ---
class AssetDebtCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    type: AssetType
    asset_class: AssetClass = "other"
    current_value_minor: int = Field(default=0, ge=0)
    interest_rate_percent: float = Field(default=0.0, ge=0.0)
    monthly_payment_minor: int = Field(default=0, ge=0)
    account_id: UUID | None = None


class AssetDebtUpdate(BaseModel):
    name: str | None = None
    asset_class: AssetClass | None = None
    current_value_minor: int | None = Field(default=None, ge=0)
    interest_rate_percent: float | None = Field(default=None, ge=0.0)
    monthly_payment_minor: int | None = Field(default=None, ge=0)


class AssetDebtResponse(BaseModel):
    id: UUID
    name: str
    type: AssetType
    asset_class: AssetClass
    current_value_minor: int
    interest_rate_percent: float
    monthly_payment_minor: int
    account_id: UUID | None
    payoff_months_projected: int | None
    updated_at: datetime


class AssetValuationResponse(BaseModel):
    """Ein Punkt der Bewertungshistorie einer Vermoegens-/Schuldenposition."""

    id: UUID
    asset_id: UUID
    valued_on: date
    value_minor: int
    created_at: datetime


class NetWorthReportResponse(BaseModel):
    total_assets_minor: int
    total_debts_minor: int
    net_worth_minor: int
    assets_by_class: dict[str, int]
    debts_by_class: dict[str, int]
