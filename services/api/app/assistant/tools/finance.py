"""Fachwerkzeuge des KI-Felds: Konten, Budget, Berichte, Vertraege, Ziele,
Vorschau, Ruecklagen, Empfehlungen (nur lesend - E5).

Jedes Werkzeug ruft den Dienst bzw. die Hilfsfunktion, die auch die
zugehoerige Route nutzt - keine zweite Rechenregel. Wo eine Route nebenbei
schreibt, gilt hier die lesende Variante:

  * get_budget_summary immer mit create_missing=False (die Route legt die
    budgets-Zeile des Monats an).
  * insights_service.build_report immer mit with_ai=False (sonst ein
    LLM-Aufruf mitten im Werkzeug).
  * reserve_service.get_reserve_overview ist rein lesend; ensure_reserve_pots
    (Schreibpfad) wird nicht gerufen.

Der Handler laeuft in read_only_session(): ein uebersehener Schreibpfad
scheitert laut statt still zu schreiben.

Ausgabe: summary mit Kennzahlen, table mit dem Listenteil. Betraege kommen
ueber plain()/records() als `*_eur`; interne Felder (Versionen,
Zeitstempel, Abgleichsparameter, Mandatsreferenzen) fallen weg, IDs fuer
Folgewerkzeuge (Vertrag, Buchung, Kategorie, Konto) bleiben.
"""

from datetime import date, timedelta
from typing import Any, Final, cast, get_args
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.routes import goals as goal_routes
from app.api.v1.routes import reports as report_routes
from app.api.v1.routes import transactions as transaction_routes
from app.assistant.tools.args import (
    bool_arg,
    check_keys,
    choice_arg,
    int_arg,
    optional_int_arg,
    text_arg,
    uuid_arg,
)
from app.assistant.tools.base import (
    ToolArgs,
    ToolContext,
    ToolRejected,
    ToolReply,
    ToolSpec,
    euro,
    object_schema,
    plain,
    records,
    table_of,
)
from app.schemas.contract import Contract, ContractDirection, ContractStatus, ContractType
from app.services import (
    category_service,
    contract_service,
    household_forecast_service,
    insights_service,
    reserve_service,
)
from app.services.account_balance_service import fetch_current_account_balances
from app.services.budget_service import get_annual_matrix, get_budget_summary
from app.services.goal_service import fetch_net_worth_report

# Gleiche Spanne wie die Uebersichtskachel im Dashboard (routes/reports.py:
# cashflow_last_6_months). Laengere Reihen liefert cashflow_trend.
_OVERVIEW_CASHFLOW_MONTHS: Final = 6

# Wertebereiche wie in den Routen (Query-Schranken), damit Werkzeug und API
# dieselben Eingaben annehmen.
_REPORT_MONTHS_MAX: Final = 36
_BUDGET_YEAR_MIN: Final = 2000
_BUDGET_YEAR_MAX: Final = 2100
_RESERVE_YEAR_MAX: Final = 2200
_MONTHS_IN_YEAR: Final = 12

# Statusvorgabe der Vertragsliste ohne include_ended (routes/contracts.py).
_DEFAULT_CONTRACT_STATUSES: Final = ("active", "paused", "cancelled")

_STATUSES: Final[tuple[str, ...]] = get_args(ContractStatus)
_TYPES: Final[tuple[str, ...]] = get_args(ContractType)
_DIRECTIONS: Final[tuple[str, ...]] = get_args(ContractDirection)

_NO_ARGS: Final = object_schema()


def _months_schema(default: int) -> dict[str, Any]:
    return {
        "type": "integer",
        "minimum": 1,
        "maximum": _REPORT_MONTHS_MAX,
        "description": f"Anzahl Monate (Standard {default}).",
    }


def _year_month(
    ctx: ToolContext, args: ToolArgs, *, year_max: int
) -> tuple[int, int]:
    year = int_arg(
        args, "year", default=ctx.today.year, minimum=_BUDGET_YEAR_MIN, maximum=year_max
    )
    month = int_arg(args, "month", default=ctx.today.month, minimum=1, maximum=_MONTHS_IN_YEAR)
    return year, month


def _year_month_schema(year_max: int) -> dict[str, Any]:
    return object_schema(
        {
            "year": {
                "type": "integer",
                "minimum": _BUDGET_YEAR_MIN,
                "maximum": year_max,
                "description": "Jahr (Standard: aktuelles Jahr).",
            },
            "month": {
                "type": "integer",
                "minimum": 1,
                "maximum": _MONTHS_IN_YEAR,
                "description": "Monat 1-12 (Standard: aktueller Monat).",
            },
        }
    )


# ---------------------------------------------------------------- Namen


async def _category_names(session: AsyncSession, household_id: UUID) -> dict[UUID, str]:
    """Alle Kategorienamen des Haushalts - auch archivierte, weil Vertraege
    und Buchungen auf sie zeigen koennen."""
    rows = await session.execute(
        text("select id, name from categories where household_id = :household_id"),
        {"household_id": str(household_id)},
    )
    return {row.id: str(row.name) for row in rows}


async def _account_names(session: AsyncSession, household_id: UUID) -> dict[UUID, str]:
    rows = await session.execute(
        text("select id, name from accounts where household_id = :household_id"),
        {"household_id": str(household_id)},
    )
    return {row.id: str(row.name) for row in rows}


async def _goal_names(session: AsyncSession, household_id: UUID) -> dict[UUID, str]:
    rows = await session.execute(
        text("select id, name from savings_goals where household_id = :household_id"),
        {"household_id": str(household_id)},
    )
    return {row.id: str(row.name) for row in rows}


# ---------------------------------------------------------------- Konten


async def account_rows(session: AsyncSession, household_id: UUID) -> list[dict[str, Any]]:
    """Konten mit aktuellem Saldo (account_balance_service, dieselbe Formel
    wie Nettovermoegen und Kontoabstimmung) und Buchungszeitraum. Ohne IBAN:
    die Kontospalten sind dieselben wie in GET /v1/accounts."""
    result = await session.execute(
        text(
            "select a.id, a.name, a.account_type, a.institution, a.currency, a.on_budget, "
            "a.is_closed, a.opening_balance_minor, a.opening_balance_date, "
            "min(t.booked_date) as first_booking_on, max(t.booked_date) as last_booking_on, "
            "count(t.id) as transaction_count "
            "from accounts a "
            "left join transactions t on t.account_id = a.id and not t.is_archived "
            "where a.household_id = :household_id "
            "group by a.id order by a.is_closed, a.created_at"
        ),
        {"household_id": str(household_id)},
    )
    balances = {
        item.account_id: item.balance_minor
        for item in await fetch_current_account_balances(
            session, household_id, include_closed=True
        )
    }
    return [
        {
            "id": row.id,
            "name": row.name,
            "account_type": row.account_type,
            "institution": row.institution,
            "currency": row.currency,
            "on_budget": row.on_budget,
            "is_closed": row.is_closed,
            "balance_minor": balances.get(row.id, int(row.opening_balance_minor)),
            "opening_balance_minor": int(row.opening_balance_minor),
            "opening_balance_date": row.opening_balance_date,
            "first_booking_on": row.first_booking_on,
            "last_booking_on": row.last_booking_on,
            "transaction_count": int(row.transaction_count),
        }
        for row in result
    ]


async def _list_accounts(ctx: ToolContext, args: ToolArgs) -> ToolReply:
    check_keys(args, ())
    accounts = await account_rows(ctx.session, ctx.household_id)
    open_accounts = [item for item in accounts if not item["is_closed"]]
    summary = {
        "account_count": len(accounts),
        "open_account_count": len(open_accounts),
        "total_balance_open_minor": sum(item["balance_minor"] for item in open_accounts),
        "on_budget_balance_minor": sum(
            item["balance_minor"] for item in open_accounts if item["on_budget"]
        ),
    }
    return ToolReply(summary=plain(summary), table=table_of(records(accounts)))


# ---------------------------------------------------------------- Uebersicht und Berichte


async def _overview(ctx: ToolContext, args: ToolArgs) -> ToolReply:
    check_keys(args, ())
    today = ctx.today
    budget = await get_budget_summary(
        ctx.session, ctx.household_id, today.year, today.month, create_missing=False
    )
    total_spent = budget.total_spent_minor
    categories: list[dict[str, Any]] = [
        {
            "category_id": category.category_id,
            "category_name": category.category_name,
            "group_name": category.group_name,
            "spent_minor": category.spent_minor,
            "share_percent": (
                round(category.spent_minor / total_spent * 100.0, 1) if total_spent > 0 else 0.0
            ),
        }
        for group in budget.groups
        for category in group.categories
        if category.spent_minor > 0
    ]
    categories.sort(key=lambda item: -int(item["spent_minor"]))

    cashflow = await report_routes._compute_cashflow_months(
        ctx.session, ctx.household_id, today.year, today.month, _OVERVIEW_CASHFLOW_MONTHS
    )
    due_days = report_routes._UPCOMING_DUE_DAYS
    due = await contract_service.build_forecast(
        ctx.session, ctx.household_id, today=today, until=today + timedelta(days=due_days)
    )
    summary = {
        "year": today.year,
        "month": today.month,
        "income_minor": budget.total_income_minor,
        "expense_minor": total_spent,
        "net_savings_minor": budget.total_income_minor - total_spent,
        "savings_rate_percent": budget.savings_rate_percent,
        "budget_remaining_minor": budget.total_remaining_minor,
        "uncategorized_spent_minor": budget.uncategorized_spent_minor,
        "fixed_costs_monthly_minor": budget.fixed_costs_monthly_minor,
        "available_after_fixed_minor": budget.available_after_fixed_minor,
        "fixed_costs_ratio_percent": budget.fixed_costs_ratio_percent,
        "upcoming_due_days": due_days,
        "upcoming_due": records(due),
        "cashflow_months": records(cashflow),
    }
    return ToolReply(summary=plain(summary), table=table_of(records(categories)))


async def _cashflow_trend(ctx: ToolContext, args: ToolArgs) -> ToolReply:
    check_keys(args, ("months",))
    months = int_arg(args, "months", default=24, minimum=1, maximum=_REPORT_MONTHS_MAX)
    trend = await report_routes._compute_cashflow_months(
        ctx.session, ctx.household_id, ctx.today.year, ctx.today.month, months
    )
    income = sum(item.income_minor for item in trend)
    expense = sum(item.expense_minor for item in trend)
    summary = {
        "months": months,
        "from": f"{trend[0].year:04d}-{trend[0].month:02d}",
        "to": f"{trend[-1].year:04d}-{trend[-1].month:02d}",
        "income_total_minor": income,
        "expense_total_minor": expense,
        "net_total_minor": income - expense,
        "income_avg_minor": round(income / months),
        "expense_avg_minor": round(expense / months),
        "net_avg_minor": round((income - expense) / months),
    }
    return ToolReply(summary=plain(summary), table=table_of(records(trend)))


async def _fixed_costs_trend(ctx: ToolContext, args: ToolArgs) -> ToolReply:
    check_keys(args, ("months",))
    months = int_arg(args, "months", default=24, minimum=1, maximum=_REPORT_MONTHS_MAX)
    trend = await report_routes._compute_fixed_costs_months(
        ctx.session, ctx.household_id, ctx.today.year, ctx.today.month, months
    )
    first, last = trend[0], trend[-1]
    change = last.fixed_costs_minor - first.fixed_costs_minor
    summary = {
        "months": months,
        "from": f"{first.year:04d}-{first.month:02d}",
        "to": f"{last.year:04d}-{last.month:02d}",
        "first_month_minor": first.fixed_costs_minor,
        "last_month_minor": last.fixed_costs_minor,
        "change_minor": change,
        "change_percent": (
            round(change / first.fixed_costs_minor * 100.0, 1)
            if first.fixed_costs_minor > 0
            else None
        ),
    }
    return ToolReply(summary=plain(summary), table=table_of(records(trend)))


async def _variable_costs(ctx: ToolContext, args: ToolArgs) -> ToolReply:
    check_keys(args, ("months",))
    months = int_arg(args, "months", default=12, minimum=1, maximum=_REPORT_MONTHS_MAX)
    report = await report_routes._compute_variable_costs(
        ctx.session, ctx.household_id, ctx.today, months
    )
    rows = [
        {
            "category_id": item.category_id,
            "category_name": item.category_name,
            "group_name": item.group_name,
            "monthly_average_minor": item.monthly_average_minor,
            "share_percent": item.share_percent,
            "transaction_count": item.transaction_count,
            # trend sind Minor Units ohne `_minor`-Namen - hier ausdruecklich.
            "trend_eur": [euro(value) for value in item.trend],
        }
        for item in report.categories
    ]
    summary = {
        "generated_on": report.generated_on,
        "months": report.months,
        "monthly_average_minor": report.monthly_average_minor,
        "monthly_fixed_average_minor": report.monthly_fixed_average_minor,
        "trend_months": [item.label for item in report.series],
        "series": records(report.series),
    }
    return ToolReply(summary=plain(summary), table=table_of(records(rows)))


# ---------------------------------------------------------------- Budget


async def _budget(ctx: ToolContext, args: ToolArgs) -> ToolReply:
    check_keys(args, ("year", "month"))
    year, month = _year_month(ctx, args, year_max=_BUDGET_YEAR_MAX)
    budget = await get_budget_summary(
        ctx.session, ctx.household_id, year, month, create_missing=False
    )
    summary: dict[str, Any] = {
        "budget_exists": budget.budget_id is not None,
        **budget.model_dump(exclude={"budget_id", "method", "groups", "fixed_cost_reserve_items"}),
        "groups": records(
            budget.groups, drop=("group_id", "sort_order", "categories")
        ),
        "fixed_cost_reserve_items": records(budget.fixed_cost_reserve_items),
    }
    categories = [category for group in budget.groups for category in group.categories]
    table = table_of(
        records(
            categories,
            drop=("group_id", "rollover_rule", "contract_forecast_sources", "has_activity_ever"),
        )
    )
    return ToolReply(summary=plain(summary), table=table)


async def _budget_year(ctx: ToolContext, args: ToolArgs) -> ToolReply:
    check_keys(args, ("year",))
    year = int_arg(
        args, "year", default=ctx.today.year, minimum=_BUDGET_YEAR_MIN, maximum=_BUDGET_YEAR_MAX
    )
    matrix = await get_annual_matrix(ctx.session, ctx.household_id, year)
    rows: list[dict[str, Any]] = []
    spent_by_month = [0] * _MONTHS_IN_YEAR
    income_by_month = [0] * _MONTHS_IN_YEAR
    without_activity = 0
    for row in matrix.rows:
        ordered = sorted(row.months, key=lambda item: item.month)
        for item in ordered:
            spent_by_month[item.month - 1] += item.spent_minor
            income_by_month[item.month - 1] += item.income_actual_minor
        # Eine Kategorie ohne jeden Wert im Jahr waere eine Zeile aus 36
        # Nullen - sie wird nur gezaehlt (verlustfrei: alle Werte sind 0).
        if not any(
            item.planned_minor or item.spent_minor or item.income_actual_minor
            for item in ordered
        ):
            without_activity += 1
            continue
        rows.append(
            {
                "category_id": row.category_id,
                "category_name": row.category_name,
                "group_name": row.group_name,
                "default_type": row.default_type,
                "budget_bucket": row.budget_bucket,
                "is_archived": row.is_archived,
                "total_planned_minor": row.total_planned_minor,
                "total_spent_minor": row.total_spent_minor,
                "total_income_actual_minor": row.total_income_actual_minor,
                "planned_eur_by_month": [euro(item.planned_minor) for item in ordered],
                "spent_eur_by_month": [euro(item.spent_minor) for item in ordered],
                "income_actual_eur_by_month": [
                    euro(item.income_actual_minor) for item in ordered
                ],
            }
        )
    summary = {
        "year": matrix.year,
        "category_count": len(rows),
        "categories_without_values": without_activity,
        "total_planned_minor": sum(row.total_planned_minor for row in matrix.rows),
        "total_spent_minor": sum(row.total_spent_minor for row in matrix.rows),
        "total_income_actual_minor": sum(row.total_income_actual_minor for row in matrix.rows),
        "spent_eur_by_month": [euro(value) for value in spent_by_month],
        "income_actual_eur_by_month": [euro(value) for value in income_by_month],
        "month_index": "Listen *_by_month: Index 0 = Januar ... Index 11 = Dezember",
    }
    return ToolReply(summary=plain(summary), table=table_of(records(rows)))


# ---------------------------------------------------------------- Vertraege


def _contract_row(
    contract: Contract,
    today: date,
    categories: dict[UUID, str],
    accounts: dict[UUID, str],
) -> dict[str, Any]:
    """Vertrag fuer das Modell. Ohne contract_key (kann eine
    SEPA-Mandatsreferenz sein), Abgleichsparameter, Version und Zeitstempel;
    die Kuendigungsfrist auf `today` gerechnet und flach."""
    deadline = contract_service.compute_term_deadline(contract, today)
    return {
        "id": contract.id,
        "name": contract.name,
        "provider_label": contract.provider_label,
        "contract_type": contract.contract_type,
        "direction": contract.direction,
        "status": contract.status,
        "amount_minor": contract.amount_minor,
        "frequency": contract.frequency,
        "monthly_minor": contract_service.monthly_minor(contract.amount_minor, contract.frequency),
        "yearly_minor": contract_service.yearly_minor(contract.amount_minor, contract.frequency),
        "next_due_date": contract.next_due_date,
        "due_rule": contract.due_rule,
        "due_day": contract.due_day,
        "category_id": contract.category_id,
        "category_name": categories.get(contract.category_id) if contract.category_id else None,
        "account_id": contract.account_id,
        "account_name": accounts.get(contract.account_id) if contract.account_id else None,
        "income_kind": contract.income_kind,
        "starts_on": contract.starts_on,
        "ends_on": contract.ends_on,
        "cancelled_on": contract.cancelled_on,
        "min_term_months": contract.min_term_months,
        "cancellation_period_days": contract.cancellation_period_days,
        "renewal_term_months": contract.renewal_term_months,
        "term_deadline_kind": deadline.kind if deadline else None,
        "cancel_by": deadline.deadline_on if deadline else None,
        "term_ends_on": deadline.term_ends_on if deadline else None,
        "installments_total": contract.installments_total,
        "installments_paid": contract.installments_paid,
        "first_seen_date": contract.first_seen_date,
        "last_seen_date": contract.last_seen_date,
        "missed_periods": contract.missed_periods,
        "source": contract.source,
        "detection_confidence": contract.detection_confidence,
        "reserve_enabled": contract.reserve_enabled,
        "reserve_monthly_minor": contract.reserve_monthly_minor,
        "note": contract.note,
    }


async def _contracts(ctx: ToolContext, args: ToolArgs) -> ToolReply:
    check_keys(args, ("status", "contract_type", "direction", "search", "include_ended"))
    status = choice_arg(args, "status", _STATUSES)
    contract_type = choice_arg(args, "contract_type", _TYPES)
    direction = choice_arg(args, "direction", _DIRECTIONS)
    search = text_arg(args, "search")
    include_ended = bool_arg(args, "include_ended", default=False)
    if status:
        statuses: list[str] | None = [status]
    elif include_ended:
        statuses = None
    else:
        statuses = list(_DEFAULT_CONTRACT_STATUSES)
    contracts = await contract_service.list_contracts(
        ctx.session,
        ctx.household_id,
        statuses=statuses,
        contract_type=contract_type,
        direction=direction,
        search=search,
    )
    categories = await _category_names(ctx.session, ctx.household_id)
    accounts = await _account_names(ctx.session, ctx.household_id)
    rows = [_contract_row(item, ctx.today, categories, accounts) for item in contracts]
    by_status: dict[str, int] = {}
    by_direction: dict[str, int] = {}
    for item in contracts:
        by_status[item.status] = by_status.get(item.status, 0) + 1
        by_direction[item.direction] = by_direction.get(item.direction, 0) + 1
    summary = {
        "count": len(contracts),
        "statuses": statuses if statuses is not None else "alle",
        "by_status": by_status,
        "by_direction": by_direction,
    }
    return ToolReply(summary=plain(summary), table=table_of(records(rows)))


async def _contract(ctx: ToolContext, args: ToolArgs) -> ToolReply:
    check_keys(args, ("id",))
    contract_id = uuid_arg(args, "id", source="contracts")
    contract = await contract_service.load_contract(ctx.session, ctx.household_id, contract_id)
    if contract is None:
        raise ToolRejected(
            f"Einen Vertrag {contract_id} gibt es in diesem Haushalt nicht (oder er wurde "
            "in einen anderen zusammengeführt). Gültige IDs liefert contracts."
        )
    categories = await _category_names(ctx.session, ctx.household_id)
    accounts = await _account_names(ctx.session, ctx.household_id)
    history = await contract_service.list_amount_history(
        ctx.session, ctx.household_id, contract_id
    )
    return ToolReply(
        text="Tabelle: Betragsverlauf des Vertrags (Preisstufen mit Gültigkeitszeitraum).",
        summary=plain(_contract_row(contract, ctx.today, categories, accounts)),
        table=table_of(records(history, drop=("id", "contract_id"))),
    )


async def _contract_summary(ctx: ToolContext, args: ToolArgs) -> ToolReply:
    check_keys(args, ())
    result = await contract_service.build_summary(ctx.session, ctx.household_id, today=ctx.today)
    income = await contract_service.build_income_summary(
        ctx.session, ctx.household_id, today=ctx.today
    )
    categories = await _category_names(ctx.session, ctx.household_id)
    summary = {
        "monthly_expense_minor": result.monthly_expense_minor,
        "yearly_expense_minor": result.yearly_expense_minor,
        "monthly_income_minor": result.monthly_income_minor,
        "yearly_income_minor": result.yearly_income_minor,
        "monthly_net_minor": result.monthly_net_minor,
        "active_count": result.active_count,
        "paused_count": result.paused_count,
        "cancelled_count": result.cancelled_count,
        "ended_count": result.ended_count,
        "by_type": records(result.by_type),
        "most_expensive_by_year_excerpt": records(result.yearly_by_contract),
        "upcoming_excerpt": records(result.upcoming),
        "ending_soon": records(result.ending_soon),
        "term_deadlines": records(result.term_deadlines),
        "income_contracts_monthly_minor": income.monthly_income_minor,
        "income_by_kind": records(income.by_kind),
        "income_by_person": records(income.by_person, drop=("person_id",)),
    }
    rows = [
        {
            "category_id": item.category_id,
            "category_name": (
                categories.get(item.category_id, "(unbekannt)")
                if item.category_id
                else "(ohne Kategorie)"
            ),
            "count": item.count,
            "monthly_minor": item.monthly_minor,
            "yearly_minor": item.yearly_minor,
        }
        for item in result.by_category
    ]
    return ToolReply(
        text=(
            "Tabelle: laufende Verträge je Kategorie (Ausgaben und Einnahmen zusammen). "
            "most_expensive_by_year_excerpt und upcoming_excerpt sind Auszüge wie auf der "
            "Vertragsseite; jeden Vertrag mit Monats- und Jahresbetrag liefert contracts."
        ),
        summary=plain(summary),
        table=table_of(records(rows)),
    )


# ---------------------------------------------------------------- Ziele, Vermoegen, Vorschau


async def _goals(ctx: ToolContext, args: ToolArgs) -> ToolReply:
    check_keys(args, ())
    goals = await goal_routes.list_goals((ctx.session, ctx.household_id))
    active = [goal for goal in goals if goal.status == "active"]
    by_status: dict[str, int] = {}
    for goal in goals:
        by_status[goal.status] = by_status.get(goal.status, 0) + 1
    summary = {
        "count": len(goals),
        "by_status": by_status,
        "active_target_minor": sum(goal.target_amount_minor for goal in active),
        "active_current_minor": sum(goal.current_amount_minor for goal in active),
        "active_monthly_required_minor": sum(goal.monthly_required_minor for goal in active),
    }
    return ToolReply(
        summary=plain(summary), table=table_of(records(goals, drop=("created_at",)))
    )


async def _net_worth(ctx: ToolContext, args: ToolArgs) -> ToolReply:
    check_keys(args, ())
    report = await fetch_net_worth_report(ctx.session, ctx.household_id)
    rows = [
        {"type": "asset", "asset_class": name, "amount_minor": value}
        for name, value in sorted(report.assets_by_class.items(), key=lambda item: -item[1])
    ] + [
        {"type": "debt", "asset_class": name, "amount_minor": value}
        for name, value in sorted(report.debts_by_class.items(), key=lambda item: -item[1])
    ]
    summary = {
        "total_assets_minor": report.total_assets_minor,
        "total_debts_minor": report.total_debts_minor,
        "net_worth_minor": report.net_worth_minor,
    }
    return ToolReply(summary=plain(summary), table=table_of(records(rows)))


async def _forecast(ctx: ToolContext, args: ToolArgs) -> ToolReply:
    check_keys(args, ("horizon_months", "include_savings"))
    horizon = optional_int_arg(
        args,
        "horizon_months",
        minimum=1,
        maximum=household_forecast_service._MAX_HORIZON_MONTHS,
    )
    include_savings = bool_arg(args, "include_savings", default=False)
    try:
        result = await household_forecast_service.build_forecast(
            ctx.session,
            ctx.household_id,
            as_of=ctx.today,
            horizon_months=horizon,
            granularity="month",
            include_savings=include_savings,
        )
    except ValueError as error:
        raise ToolRejected(str(error)) from None
    summary = {
        "as_of": result.as_of,
        "horizon_months": result.horizon_months,
        "include_savings": include_savings,
        "opening_liquidity_minor": result.opening_liquidity_minor,
        "bound_reserve_minor": result.bound_reserve_minor,
        "safety_buffer_minor": result.safety_buffer_minor,
        "metrics": result.metrics,
        "cause": result.cause,
        "warnings": result.warnings,
        "quality": result.quality,
        "opening_accounts": result.opening_accounts,
    }
    return ToolReply(summary=plain(summary), table=table_of(records(result.monthly)))


# ---------------------------------------------------------------- Stammdaten, Einzelbuchung


async def _categories(ctx: ToolContext, args: ToolArgs) -> ToolReply:
    check_keys(args, ())
    tree = await category_service.load_active_tree(ctx.session, ctx.household_id)
    rows = [
        {
            "id": category.id,
            "name": category.name,
            "group_id": group.id,
            "group_name": group.name,
            "default_type": category.default_type,
            "budget_bucket": category.budget_bucket,
            "is_budgeted": category.is_budgeted,
            "is_tax_deductible": category.is_tax_deductible,
            "synonyms": category.synonyms,
            "description": category.description,
        }
        for group in tree
        for category in group.categories
    ]
    summary = {
        "group_count": len(tree),
        "category_count": len(rows),
        "groups": [{"id": group.id, "name": group.name} for group in tree],
    }
    return ToolReply(summary=plain(summary), table=table_of(records(rows)))


async def _transaction(ctx: ToolContext, args: ToolArgs) -> ToolReply:
    check_keys(args, ("id",))
    transaction_id = uuid_arg(
        args, "id", source="query_analytics (Spalte transaction_id) oder budget/contract"
    )
    try:
        detail = await transaction_routes._load_transaction_detail(
            ctx.session, transaction_id, ctx.household_id
        )
    except HTTPException:
        raise ToolRejected(
            f"Eine Buchung {transaction_id} gibt es in diesem Haushalt nicht. Buchungs-IDs "
            "liefert query_analytics (grain=rows, Spalte transaction_id)."
        ) from None
    raw = (
        await ctx.session.execute(
            text(
                "select a.name as account_name, t.payee_raw, t.purpose_raw, "
                "t.bank_transaction_code, t.source::text as source "
                "from transactions t join accounts a on a.id = t.account_id "
                "where t.id = :id and t.household_id = :household_id"
            ),
            {"id": str(transaction_id), "household_id": str(ctx.household_id)},
        )
    ).one()
    categories = await _category_names(ctx.session, ctx.household_id)
    goals = await _goal_names(ctx.session, ctx.household_id)
    summary = {
        "id": detail.id,
        "booked_date": detail.booked_date,
        "value_date": detail.value_date,
        "amount_minor": detail.amount_minor,
        "currency": detail.currency,
        "account_id": detail.account_id,
        "account_name": raw.account_name,
        "payee_label": detail.payee_label,
        "payee_raw": raw.payee_raw,
        "purpose_raw": raw.purpose_raw,
        "note": detail.note,
        "bank_transaction_code": raw.bank_transaction_code,
        "source": raw.source,
        "status": detail.status,
        "is_transfer": detail.is_transfer,
        "is_archived": detail.is_archived,
        "is_split": detail.is_split,
    }
    splits = [
        {
            "split_id": split.id,
            "category_id": split.category_id,
            "category_name": categories.get(split.category_id) if split.category_id else None,
            "goal_id": split.goal_id,
            "goal_name": goals.get(split.goal_id) if split.goal_id else None,
            "amount_minor": split.amount_minor,
            "note": split.note,
        }
        for split in detail.splits
    ]
    return ToolReply(
        text="Tabelle: Teilbeträge (Splits) der Buchung mit Kategorie bzw. Sparziel.",
        summary=plain(summary),
        table=table_of(records(splits)),
    )


# ---------------------------------------------------------------- Ruecklagen, Empfehlungen


async def _reserves(ctx: ToolContext, args: ToolArgs) -> ToolReply:
    check_keys(args, ("year", "month"))
    year, month = _year_month(ctx, args, year_max=_RESERVE_YEAR_MAX)
    overview = await reserve_service.get_reserve_overview(
        ctx.session, ctx.household_id, year=year, month=month, today=ctx.today
    )
    summary = overview.model_dump(exclude={"pots"})
    return ToolReply(
        summary=plain(summary),
        table=table_of(records(overview.pots, drop=("movements",))),
    )


async def _insights(ctx: ToolContext, args: ToolArgs) -> ToolReply:
    check_keys(args, ("months",))
    months = int_arg(
        args,
        "months",
        default=insights_service.DEFAULT_MONTHS,
        minimum=insights_service.MIN_MONTHS,
        maximum=insights_service.MAX_MONTHS,
    )
    report = await insights_service.build_report(
        ctx.session, ctx.household_id, months=months, with_ai=False
    )
    summary = report.model_dump(exclude={"insights", "model_name", "ai_warnings"})
    return ToolReply(
        summary=plain(summary),
        table=table_of(records(report.insights, drop=("key", "ai_advice"))),
    )


# ---------------------------------------------------------------- Definitionen

TOOLS: Final[tuple[ToolSpec, ...]] = (
    ToolSpec(
        name="list_accounts",
        description=(
            "Alle Konten des Haushalts (auch geschlossene, is_closed) mit ID, Art, Bank, "
            "Budgetkonto ja/nein, aktuellem Saldo in Euro (Eröffnungssaldo plus alle "
            "Buchungen) sowie erster/letzter Buchung und Anzahl Buchungen. Keine IBAN. Keine "
            "Argumente."
        ),
        parameters=_NO_ARGS,
        handler=_list_accounts,
    ),
    ToolSpec(
        name="overview",
        description=(
            "Dashboard des laufenden Monats: Einnahmen, Ausgaben (positiv), Netto, Sparquote, "
            "Budgetrest, Fixkosten aus Verträgen, Vertragsfälligkeiten der nächsten Tage, "
            "Cashflow der letzten Monate; Tabelle = Ausgaben je Kategorie im laufenden Monat "
            "mit Anteil. Beträge in Euro. Ohne die Zahl offener Prüfungen (Review-Inbox). "
            "Keine Argumente."
        ),
        parameters=_NO_ARGS,
        handler=_overview,
    ),
    ToolSpec(
        name="cashflow_trend",
        description=(
            "Einnahmen, Ausgaben und Netto je Kalendermonat bis einschließlich des laufenden "
            "(unvollständigen) Monats - nur Budgetkonten, ohne Umbuchungen, dieselben Regeln "
            "wie Budget und Übersicht. Ausgaben als positive Euro-Beträge, Netto = Einnahmen "
            "minus Ausgaben. Kennzahlen: Summen und Monatsdurchschnitte. Argument months "
            "(Standard 24)."
        ),
        parameters=object_schema({"months": _months_schema(24)}),
        handler=_cashflow_trend,
    ),
    ToolSpec(
        name="fixed_costs_trend",
        description=(
            "Vereinbarte monatliche Fixkosten aus Ausgabeverträgen je Kalendermonat (aus dem "
            "Betragsverlauf, nicht aus Buchungen; Jahresbeträge auf Monate verteilt), mit "
            "Vertragsanzahl, Veränderung zum Vorjahresmonat in Prozent und Aufteilung nach "
            "Vertragsart. Euro, positiv. Argument months (Standard 24)."
        ),
        parameters=object_schema({"months": _months_schema(24)}),
        handler=_fixed_costs_trend,
    ),
    ToolSpec(
        name="variable_costs",
        description=(
            "Variable Kosten = gebuchte Ausgaben ohne die durch Verträge gedeckten "
            "Kategorien, über die letzten vollen Monate (laufender Monat nicht enthalten): "
            "je Kategorie Monatsdurchschnitt, Anteil, Anzahl Buchungen und Monatsverlauf "
            "(trend_eur, Reihenfolge wie trend_months); dazu die Monatsreihe variabel gegen "
            "fix. Ausgaben positiv in Euro. Argument months (Standard 12)."
        ),
        parameters=object_schema({"months": _months_schema(12)}),
        handler=_variable_costs,
    ),
    ToolSpec(
        name="budget",
        description=(
            "Monatsbudget: je Kategorie Plan, Übertrag, Verbrauch (spent, positiv; eine "
            "Erstattung senkt ihn), Einnahme-Ist bei Einnahmekategorien, Rest (Plan + "
            "Übertrag - Verbrauch), Prozent verbraucht, Vormonat und Vertragsprognose. "
            "Kennzahlen: Summen, unkategorisierte Ausgaben (nur in der Gesamtsumme), "
            "Sparquote, 50/30/20-Töpfe, Fixkosten (Erwartung aus Verträgen, nicht in den "
            "Ausgaben enthalten), Rücklagen und freier Betrag. budget_exists=false heißt: für "
            "den Monat wurde nie geplant (Planwerte 0). Euro. Argumente year, month "
            "(Standard: aktueller Monat)."
        ),
        parameters=_year_month_schema(_BUDGET_YEAR_MAX),
        handler=_budget,
    ),
    ToolSpec(
        name="budget_year",
        description=(
            "Jahresmatrix des Budgets: je Kategorie Plan, Verbrauch (positiv) und Einnahme-Ist "
            "für jeden Monat (Listen *_eur_by_month, Index 0 = Januar) und als Jahressumme; "
            "Kategorien ohne jeden Wert im Jahr sind nur gezählt "
            "(categories_without_values). Kennzahlen mit Monatssummen über alle Kategorien. "
            "Euro. Argument year (Standard: aktuelles Jahr)."
        ),
        parameters=object_schema(
            {
                "year": {
                    "type": "integer",
                    "minimum": _BUDGET_YEAR_MIN,
                    "maximum": _BUDGET_YEAR_MAX,
                    "description": "Jahr (Standard: aktuelles Jahr).",
                }
            }
        ),
        handler=_budget_year,
    ),
    ToolSpec(
        name="contracts",
        description=(
            "Verträge (Abos, Versicherungen, Miete, Kredite, Einnahmen wie Gehalt) mit ID, "
            "Betrag je Periode (immer positiv, Richtung in direction: expense/income), "
            "Frequenz, Monats- und Jahresbetrag, nächster Fälligkeit, Kategorie, Konto, "
            "Status, Laufzeit und Kündigungsfrist (cancel_by). Standard: aktive, pausierte "
            "und gekündigte. Argumente: status (einer), contract_type, direction, search "
            "(Name/Anbieter/Notiz), include_ended (auch beendete und verneinte)."
        ),
        parameters=object_schema(
            {
                "status": {"type": "string", "enum": list(_STATUSES)},
                "contract_type": {"type": "string", "enum": list(_TYPES)},
                "direction": {"type": "string", "enum": list(_DIRECTIONS)},
                "search": {"type": "string"},
                "include_ended": {"type": "boolean"},
            }
        ),
        handler=_contracts,
    ),
    ToolSpec(
        name="contract",
        description=(
            "Ein Vertrag im Detail (Felder wie bei contracts) und als Tabelle sein "
            "Betragsverlauf (Preisstufen mit gültig ab/bis, Änderung, Grund). Euro, positiv. "
            "Argument id (aus contracts)."
        ),
        parameters=object_schema(
            {"id": {"type": "string", "description": "Vertrags-ID"}}, required=("id",)
        ),
        handler=_contract,
    ),
    ToolSpec(
        name="contract_summary",
        description=(
            "Vertragsübersicht: monatliche und jährliche Summen getrennt nach Ausgaben und "
            "Einnahmen (nur laufende Verträge), Anzahl je Status, Summen je Vertragsart, "
            "teuerste Verträge und demnächst fällige (Auszüge), bald endende Verträge, "
            "Kündigungsfristen, Einnahmen nach Art und Person; Tabelle = Summen je Kategorie. "
            "Euro. Keine Argumente."
        ),
        parameters=_NO_ARGS,
        handler=_contract_summary,
    ),
    ToolSpec(
        name="goals",
        description=(
            "Sparziele mit Ziel- und aktuellem Betrag (Startbetrag plus zugeordnete Buchungen "
            "und Rücklagenbewegungen), Zieldatum, nötiger Monatsrate, Planungsstatus und "
            "voraussichtlichem Erreichen. Euro. Keine Argumente."
        ),
        parameters=_NO_ARGS,
        handler=_goals,
    ),
    ToolSpec(
        name="net_worth",
        description=(
            "Nettovermögen: Vermögen, Schulden und Saldo aus manuell erfassten "
            "Vermögenswerten/Schulden plus aktuellen Kontosalden (negative Salden zählen als "
            "Schulden); Tabelle je Vermögensklasse. Euro, Schulden positiv ausgewiesen. Keine "
            "Argumente."
        ),
        parameters=_NO_ARGS,
        handler=_net_worth,
    ),
    ToolSpec(
        name="forecast",
        description=(
            "Liquiditätsvorschau ab heute je Monat: Einnahmen, Fixkosten, variable Kosten, "
            "Netto und Kontostand am Monatsende (Euro; Kosten positiv). Kennzahlen: Startsaldo "
            "und einbezogene Konten, Tiefststand und Datum, erster Tag im Minus, Puffer, "
            "Sparquote, Fixkostenquote, Stände in 3/6/12/24 Monaten, Warnungen und "
            "Datenqualität. Argumente: horizon_months (Standard aus den Einstellungen), "
            "include_savings (Sparkonten einbeziehen, Standard false)."
        ),
        parameters=object_schema(
            {
                "horizon_months": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": household_forecast_service._MAX_HORIZON_MONTHS,
                },
                "include_savings": {"type": "boolean"},
            }
        ),
        handler=_forecast,
    ),
    ToolSpec(
        name="categories",
        description=(
            "Aktive Kategorien mit ID, Gruppe, Typ (income/expense/transfer), "
            "50/30/20-Bereich, im Budget ja/nein, steuerlich absetzbar, Synonymen und "
            "Beschreibung. Die IDs passen zu query_analytics (Feld category). Keine Argumente."
        ),
        parameters=_NO_ARGS,
        handler=_categories,
    ),
    ToolSpec(
        name="transaction",
        description=(
            "Eine Buchung im Detail: Datum, Betrag (Euro; Ausgabe negativ, Einnahme positiv), "
            "Konto, Empfänger, Verwendungszweck, Notiz, Status, Umbuchung ja/nein; Tabelle = "
            "Teilbeträge mit Kategorie bzw. Sparziel. Argument id (transaction_id aus "
            "query_analytics)."
        ),
        parameters=object_schema(
            {"id": {"type": "string", "description": "Buchungs-ID"}}, required=("id",)
        ),
        handler=_transaction,
    ),
    ToolSpec(
        name="reserves",
        description=(
            "Rücklagen eines Monats: je Topf (Vertrag mit Jahres-/Quartalszahlung, Sparziel, "
            "freie Rücklage) Zielbetrag, nächste Fälligkeit, empfohlene Monatsrate, "
            "eingezahlt, entnommen, offen, Stand und Empfehlung für den Folgemonat; "
            "Kennzahlen mit Summen. Euro. Argumente year, month (Standard: aktueller Monat)."
        ),
        parameters=_year_month_schema(_RESERVE_YEAR_MAX),
        handler=_reserves,
    ),
    ToolSpec(
        name="insights",
        description=(
            "Finanz-Empfehlungen aus den Buchungen der letzten vollständigen Monate: z. B. "
            "unkategorisierte Buchungen, Budgetüberschreitungen, Preiserhöhungen, ungenutzte "
            "oder doppelte Verträge, Kategorietrends, Sparquote, steuerlich Absetzbares - je "
            "Empfehlung Art, Schwere, Titel, Erklärung, Betrag, Monatspotenzial und Belege. "
            "Kennzahlen: Zeitraum und Monatsdurchschnitte von Einnahmen, Ausgaben, Netto. "
            "Euro. Argument months (Standard 12)."
        ),
        parameters=object_schema(
            {
                "months": {
                    "type": "integer",
                    "minimum": insights_service.MIN_MONTHS,
                    "maximum": insights_service.MAX_MONTHS,
                    "description": "Betrachtungszeitraum in vollständigen Monaten.",
                }
            }
        ),
        handler=_insights,
    ),
)


async def start_context_facts(
    session: AsyncSession, household_id: UUID, today: date
) -> dict[str, Any]:
    """Fakten fuer den Startkontext: heutiges Datum, Zeitraum und Anzahl der
    Buchungen, Konten mit Saldo. Nur lesend."""
    span = (
        await session.execute(
            text(
                "select min(booked_date) as first_on, max(booked_date) as last_on, "
                "count(*) as total from transactions "
                "where household_id = :household_id and not is_archived"
            ),
            {"household_id": str(household_id)},
        )
    ).one()
    accounts = await account_rows(session, household_id)
    facts = {
        "today": today,
        "transactions": {
            "first_booked_date": span.first_on,
            "last_booked_date": span.last_on,
            "count": int(span.total),
        },
        "accounts": [
            {
                "id": item["id"],
                "name": item["name"],
                "account_type": item["account_type"],
                "is_closed": item["is_closed"],
                "on_budget": item["on_budget"],
                "balance_minor": item["balance_minor"],
            }
            for item in accounts
        ],
        "amounts": "Euro",
    }
    return cast(dict[str, Any], plain(facts))
