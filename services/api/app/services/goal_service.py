"""Service-Logik fuer Sparziele, Kaufampel, Vermoegensaufstellung & Schuldentilgungsprognose."""

import calendar
import math
from dataclasses import dataclass
from datetime import date
from typing import Literal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.goals import NetWorthReportResponse
from app.services.account_balance_service import fetch_current_account_balances
from app.services.budget_service import get_budget_summary


async def fetch_goal_deposits_by_goal(session: AsyncSession, household_id: UUID) -> dict[UUID, int]:
    """Summe der amount_minor aller zugeordneten, nicht archivierten
    transaction_splits je Sparziel (Architekturplan 6.7: Einzahlungen werden
    aus zugeordneten Transaktionssplits abgeleitet)."""
    res = await session.execute(
        text(
            "select ts.goal_id, coalesce(sum(ts.amount_minor), 0) as deposits_minor "
            "from transaction_splits ts join transactions t on t.id = ts.transaction_id "
            "where ts.household_id = :household_id and ts.goal_id is not null "
            "and not t.is_archived "
            "group by ts.goal_id"
        ),
        {"household_id": str(household_id)},
    )
    result = {row.goal_id: int(row.deposits_minor) for row in res}
    # Manuell bestaetigte Ruecklagenbewegungen sind eine zweite, explizite
    # Quelle. Sie werden addiert, aber nie aus Bankbuchungen abgeleitet; so
    # bleiben Startwert, goal_id-Splits und Ledger jeweils genau einmal im Stand.
    ledger = await session.execute(
        text(
            "select rp.goal_id, coalesce(sum(rm.amount_minor), 0) as movement_minor "
            "from reserve_pots rp join reserve_movements rm "
            "on rm.pot_id = rp.id and rm.household_id = rp.household_id "
            "where rp.household_id = :household_id and rp.goal_id is not null "
            "group by rp.goal_id"
        ),
        {"household_id": str(household_id)},
    )
    for row in ledger:
        result[row.goal_id] = result.get(row.goal_id, 0) + int(row.movement_minor)
    return result


async def fetch_goal_deposit_minor(session: AsyncSession, household_id: UUID, goal_id: UUID) -> int:
    """Wie fetch_goal_deposits_by_goal, aber fuer genau ein Sparziel (Insert/Update)."""
    return (await fetch_goal_deposits_by_goal(session, household_id)).get(goal_id, 0)


async def fetch_goal_available_by_goal(
    session: AsyncSession, household_id: UUID
) -> dict[UUID, int]:
    """Abgeleiteter Sparzielstand je Ziel (starting_amount_minor + zugeordnete
    Splits) - exakt dieselbe Ableitung wie SavingsGoalResponse.current_amount_minor,
    damit es fuer die Kaufampel keine zweite Wahrheit gibt."""
    deposits = await fetch_goal_deposits_by_goal(session, household_id)
    res = await session.execute(
        text(
            "select id, starting_amount_minor from savings_goals where household_id = :household_id"
        ),
        {"household_id": str(household_id)},
    )
    return {row.id: int(row.starting_amount_minor) + deposits.get(row.id, 0) for row in res}


async def fetch_goal_available_minor(
    session: AsyncSession, household_id: UUID, goal_id: UUID
) -> int:
    """Wie fetch_goal_available_by_goal, aber fuer genau ein Sparziel. Gibt 0
    zurueck, wenn das Ziel nicht (mehr) zum Haushalt gehoert."""
    row = (
        await session.execute(
            text(
                "select starting_amount_minor from savings_goals "
                "where id = :goal_id and household_id = :household_id"
            ),
            {"goal_id": str(goal_id), "household_id": str(household_id)},
        )
    ).first()
    if row is None:
        return 0
    deposits_minor = await fetch_goal_deposit_minor(session, household_id, goal_id)
    return int(row.starting_amount_minor) + deposits_minor


async def fetch_free_monthly_minor(session: AsyncSession, household_id: UUID) -> int:
    """Verfuegbarer Restbetrag des laufenden Monatsbudgets fuer die Kaufampel."""
    today = date.today()
    summary = await get_budget_summary(session, household_id, today.year, today.month)
    return max(summary.total_remaining_minor, 0)


def calculate_payoff_months(
    current_value_minor: int, monthly_payment_minor: int, interest_rate_percent: float
) -> int | None:
    if current_value_minor <= 0:
        return 0
    if monthly_payment_minor <= 0:
        return None
    # Monatlicher Zinsbetrag
    monthly_interest = int(current_value_minor * (interest_rate_percent / 100.0) / 12.0)
    net_principal_reduction = monthly_payment_minor - monthly_interest
    if net_principal_reduction <= 0:
        return None  # Tilgung zu gering, Schulden wachsen
    return math.ceil(current_value_minor / net_principal_reduction)


@dataclass(frozen=True)
class TrafficLightEvaluation:
    """Ergebnis der Kaufampel inkl. der in Architekturplan 6.7 geforderten
    Kennzahlen Deckungsgrad, Fehlbetrag und fruehester Kaufzeitpunkt."""

    status: Literal["green", "yellow", "red"]
    coverage_ratio: float
    shortfall_minor: int
    earliest_purchase_date: date | None


def _add_months(base: date, months: int) -> date:
    """Verschiebt ein Datum um volle Monate und kappt den Tag auf das
    Monatsende (31.01. + 1 Monat -> 28./29.02.)."""
    total = base.month - 1 + months
    year = base.year + total // 12
    month = total % 12 + 1
    return date(year, month, min(base.day, calendar.monthrange(year, month)[1]))


def evaluate_traffic_light(
    price_minor: int, free_monthly_minor: int, goal_available_minor: int = 0
) -> TrafficLightEvaluation:
    """Bewertet einen Wunsch zuerst gegen das Guthaben des verknuepften
    Sparziels; nur die verbleibende Restluecke wird gegen das freie
    Monatsbudget bewertet. Ein voll aus dem Sparziel gedeckter Kauf ist immer
    gruen, unabhaengig vom Monatsbudget."""
    # Mindestens 100 Euro Annahme, wenn noch kein Budget existiert
    capacity = max(free_monthly_minor, 10000)
    goal_available = max(goal_available_minor, 0)
    # Restluecke nach Anrechnung des Sparzielguthabens
    gap_minor = max(price_minor - goal_available, 0)
    shortfall_minor = max(gap_minor - capacity, 0)
    available_minor = goal_available + capacity
    coverage_ratio = 1.0 if price_minor <= 0 else min(available_minor / price_minor, 1.0)

    today = date.today()
    if shortfall_minor == 0:
        return TrafficLightEvaluation(
            status="green",
            coverage_ratio=round(coverage_ratio, 4),
            shortfall_minor=0,
            earliest_purchase_date=today,
        )

    # Zusaetzlich noetige Monate, in denen die freie Kapazitaet angespart wird
    months_needed = math.ceil(shortfall_minor / capacity)
    status: Literal["green", "yellow", "red"] = "yellow" if months_needed <= 2 else "red"
    return TrafficLightEvaluation(
        status=status,
        coverage_ratio=round(coverage_ratio, 4),
        shortfall_minor=shortfall_minor,
        earliest_purchase_date=_add_months(today, months_needed),
    )


async def fetch_net_worth_report(
    session: AsyncSession, household_id: UUID
) -> NetWorthReportResponse:
    # 1. Manuelle Vermögenswerte & Schulden
    items_res = await session.execute(
        text(
            "select type, asset_class, current_value_minor "
            "from assets_and_debts where household_id = :household_id"
        ),
        {"household_id": str(household_id)},
    )
    assets_by_class: dict[str, int] = {}
    debts_by_class: dict[str, int] = {}

    total_assets = 0
    total_debts = 0

    for row in items_res:
        val = int(row.current_value_minor)
        if row.type == "asset":
            total_assets += val
            assets_by_class[row.asset_class] = assets_by_class.get(row.asset_class, 0) + val
        else:
            total_debts += val
            debts_by_class[row.asset_class] = debts_by_class.get(row.asset_class, 0) + val

    # 2. Bankkonten-Salden ergänzen (opening_balance_minor + gebuchte Umsätze,
    # gemeinsamer Dienst mit der Kontoabstimmung und den Vorhaben-Liquiditaets-
    # vorschlaegen -- account_balance_service.py, keine zweite Wahrheit).
    account_balances = await fetch_current_account_balances(session, household_id)
    account_class_map = {
        "checking": "cash",
        "savings": "cash",
        "cash": "cash",
        "brokerage": "depot",
        "real_estate": "real_estate",
        "credit_card": "credit",
        "loan": "loan",
        "other": "other",
    }
    for account_balance in account_balances:
        bal = account_balance.balance_minor
        asset_class = account_class_map.get(account_balance.account_type, "other")
        if bal >= 0:
            total_assets += bal
            assets_by_class[asset_class] = assets_by_class.get(asset_class, 0) + bal
        else:
            debt_val = abs(bal)
            total_debts += debt_val
            debts_by_class[asset_class] = debts_by_class.get(asset_class, 0) + debt_val

    return NetWorthReportResponse(
        total_assets_minor=total_assets,
        total_debts_minor=total_debts,
        net_worth_minor=total_assets - total_debts,
        assets_by_class=assets_by_class,
        debts_by_class=debts_by_class,
    )
