"""Tagesgenaue Liquiditaetsvorschau des Haushalts
(docs/EINNAHMEN-VORSCHAU-SZENARIEN-PLAN.md, Stufe 1 und 2).

Aufbau wie `household_cashflow_service.py`: ein reiner, DB-freier Rechenkern
mit `frozen=True`-Modellen und `tuple`-Kollektionen, davor ein Loader, der die
Eingaben aus Konten, Vertraegen, Budget und Ruecklagen zusammentraegt. `as_of`
wird immer hereingereicht und nie aus `date.today()` gezogen - sonst waere kein
Referenzfall reproduzierbar.

Die Rechenlogik ist eine Portierung des lauffaehigen Prototyps
`docs/mockups/vorschau-prototyp.html` (`dueDatesInWindow`, `variablePerDay`,
`calculateForecast`, `causeOf`, `applyChanges`), nicht eine zweite Erfindung
derselben Regeln.

Vier Fallen, die diese Rechnung systematisch verfaelschen wuerden und deshalb
je einen eigenen Test haben:

1. Der Startsaldo kommt aus `account_balance_service.fetch_account_balances_as_of`
   und NICHT aus `fetch_current_account_balances`. Letzteres summiert
   ausdruecklich "ohne Stichtagsgrenze" und enthaelt bereits zukuenftig
   datierte Buchungen - die dann als Forecast-Flow ein zweites Mal zaehlten.
2. Im laufenden Monat wird nur der REST der variablen Ausgaben prognostiziert.
   Was bis heute ausgegeben wurde, steckt bereits im Startsaldo.
3. Eine zukuenftig datierte Buchung, die ueber `contract_occurrences` einer
   Vertragsfaelligkeit zugeordnet ist, verdraengt diese Faelligkeit.
4. Eine Simulationsaenderung erhoeht NIE den variablen Monatsansatz. Eine neue
   wiederkehrende Ausgabe ist bereits ein `ScheduledFlow`; wuerde sie
   zusaetzlich den Ansatz heben, zaehlte sie doppelt (der Fehler ist im
   Prototyp aufgetreten und hat die Simulation schlechter statt besser
   gemacht). Strukturell abgesichert: `apply_changes` fasst `variable_monthly`
   ueberhaupt nicht an, und der Monatsansatz der Kennzahlen wird im Kern aus
   `variable_monthly` abgeleitet statt hereingereicht.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Final, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.forecast import (
    ForecastCause,
    ForecastChange,
    ForecastComparison,
    ForecastContribution,
    ForecastDay,
    ForecastDayGroup,
    ForecastFlow,
    ForecastGranularity,
    ForecastMetrics,
    ForecastMonth,
    ForecastOpeningAccount,
    ForecastQuality,
    ForecastQualityLevel,
    ForecastResponse,
    ForecastSettings,
    ForecastSettingsInput,
    ForecastSimulationRequest,
    ForecastSimulationResponse,
    ForecastSourceKind,
    ForecastVariableBasis,
    ForecastWarning,
)
from app.services import account_balance_service, budget_service, contract_service, reserve_service
from app.services.cashflow_semantics import CASHFLOW_EXPENSE_PREDICATE

# ---------------------------------------------------------------------------
# Konstanten - die Grenzen stehen hier und nicht als Zahlen in der Route
# ---------------------------------------------------------------------------

# Jenseits von zwoelf Monaten ist die Tageslage nicht mehr belastbar
# (Plan 5.1). Der Default fuer die Anzeige sind 90 Tage, geliefert wird
# trotzdem bis zu dieser Grenze, damit die Umschalter 30/90/365 im Web ohne
# zweiten Abruf funktionieren.
_DAILY_DETAIL_MONTHS: Final = 12
# 120 Monate ohne Inflationsmodell und ohne belastbare Vertraege waeren
# Scheingenauigkeit (Plan 5.1).
_MAX_HORIZON_MONTHS: Final = 60
# Ab 250 EUR Tagesbewegung ein Ereignispunkt im Diagramm.
_BIG_EVENT_MINOR: Final = 25_000
# Ab 100 EUR zaehlt eine Einzelposition als "grosse Faelligkeit".
_HEAVY_ITEM_MINOR: Final = 10_000
# Ab 1.000 EUR Tagessumme ist der Tag auch mit einer einzigen Position schwer.
_HEAVY_DAY_TOTAL_MINOR: Final = 100_000
# Juengster Umsatz aelter als zwei Wochen: eine Prognose auf veralteten Daten
# ist eine Luege und muss sich als solche zeigen (Leitsatz 6).
_STALE_DAYS: Final = 14
# Gepflegte Einnahmen mehr als 10 % unter dem gebuchten Durchschnitt: es fehlt
# eine Quelle (Plan Abschnitt 10).
_INCOME_GAP_RATIO: Final = Decimal("0.9")

# Kontoarten, die ohne ausdrueckliche Entscheidung als liquide gelten.
# Ein Tagesgeldkonto und ein Bausparvertrag sind beide 'savings' - deshalb
# entscheidet dort `accounts.forecast_liquidity` (Plan 4.5).
_LIQUID_ACCOUNT_TYPES: Final = ("checking", "cash")
# Kontoart, die der Schnellschalter "Tagesgeld einbeziehen" zusaetzlich
# hereinnimmt, ohne die gespeicherte Auswahl zu aendern.
_SAVINGS_ACCOUNT_TYPE: Final = "savings"

# Einheitliche Vorgaben, solange kein Datensatz in `forecast_settings` steht.
_DEFAULT_SETTINGS: Final = ForecastSettings(
    safety_buffer_minor=0,
    variable_basis="max",
    variable_lookback_months=6,
    horizon_months=24,
    notify_shortfall=True,
    notify_below_buffer=True,
    notify_income_missing=True,
    updated_at=None,
)

_DEFAULT_UPCOMING_DAYS: Final = 90

ForecastDirection = Literal["expense", "income"]


def _round_minor(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _ratio_percent(part_minor: int, basis_minor: int) -> float:
    """Quote mit derselben Rundung wie im Budget
    (`contract_target_service._ratio_percent`)."""
    if basis_minor <= 0:
        return 0.0
    return round(part_minor / basis_minor * 100.0, 1)


def _month_index(year: int, month: int) -> int:
    return year * 12 + (month - 1)


def _days_in_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


# ---------------------------------------------------------------------------
# Eingaben des Rechenkerns
# ---------------------------------------------------------------------------


class ScheduledFlow(BaseModel):
    """Eine datierte Bewegung. `amount_minor` ist vorzeichenbehaftet."""

    model_config = ConfigDict(frozen=True)

    on: date
    amount_minor: int
    label: str
    source_kind: ForecastSourceKind
    source_id: UUID | None = None


class VariableMonth(BaseModel):
    """Variabler Monatsansatz und das, was in diesem Monat bereits gebucht ist.

    `booked_minor` zaehlt ALLE variablen Splits des Monats, auch zukuenftig
    datierte. Fuer den laufenden Monat ist das genau die Formel aus Plan 4.2;
    fuer kuenftige Monate verhindert es, dass eine vorgemerkte Buchung einmal
    als `ScheduledFlow` und ein zweites Mal im Monatsansatz steckt.
    """

    model_config = ConfigDict(frozen=True)

    year: int = Field(ge=2000, le=2200)
    month: int = Field(ge=1, le=12)
    planned_minor: int = Field(ge=0)
    booked_minor: int = 0


class ForecastContractTerm(BaseModel):
    """Vertragskonditionen, soweit `apply_changes` sie braucht: ohne Frequenz
    laesst sich die Monatswirkung einer Aenderung nicht normieren."""

    model_config = ConfigDict(frozen=True)

    contract_id: UUID
    label: str
    direction: ForecastDirection
    amount_minor: int = Field(gt=0)
    frequency: str
    # Seit Stufe 6 am Vertrag: nur damit laesst sich ein Gehalt von einer Miete
    # oder einer Rente unterscheiden - und nur beim Gehalt haengt die
    # Steuerklasse daran (tax_class_hint_applies).
    income_kind: str | None = None
    person_id: UUID | None = None


class ForecastContext(BaseModel):
    """Alles, was Warnungen und Datenqualitaet speist und nicht aus der
    Zahlenreihe selbst hervorgeht."""

    model_config = ConfigDict(frozen=True)

    last_booking_on: date | None = None
    contract_count: int = 0
    income_count: int = 0
    variable_basis: ForecastVariableBasis = "max"
    variable_from_history: bool = False
    variable_without_basis: bool = False
    excluded_account_names: tuple[str, ...] = ()
    contracts_ending_in_horizon: int = 0
    booked_income_monthly_minor: int | None = None
    extra_warnings: tuple[ForecastWarning, ...] = ()


class ForecastInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    as_of: date
    horizon_months: int = Field(ge=1, le=_MAX_HORIZON_MONTHS)
    daily_until: date
    opening_liquidity_minor: int
    opening_accounts: tuple[ForecastOpeningAccount, ...] = ()
    scheduled: tuple[ScheduledFlow, ...] = ()
    variable_monthly: tuple[VariableMonth, ...] = ()
    contract_terms: tuple[ForecastContractTerm, ...] = ()
    bound_reserve_minor: int = 0
    safety_buffer_minor: int = 0
    # Je Vertrag normiert und erst dann summiert (contract_service.monthly_minor),
    # niemals aus der Tagesreihe zurueckgerechnet.
    income_monthly_minor: int = 0
    fixed_monthly_minor: int = 0
    context: ForecastContext = ForecastContext()


# ---------------------------------------------------------------------------
# Rechenkern
# ---------------------------------------------------------------------------


class _DayPoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    on: date
    opening_balance_minor: int
    variable_minor: int
    balance_minor: int
    flows: tuple[ForecastFlow, ...]


def _variable_per_day_minor(data: ForecastInput, year: int, month: int) -> int:
    """Gleichmaessige Verteilung des variablen Rests auf die Tage des Monats.

    Bewusst kein historisches Wochentagsprofil (Plan 5.4) - das waere
    Genauigkeit, die die Datenlage nicht traegt.
    """
    entry = next(
        (item for item in data.variable_monthly if item.year == year and item.month == month),
        None,
    )
    if entry is None:
        return 0
    rest = max(0, entry.planned_minor - entry.booked_minor)
    if year == data.as_of.year and month == data.as_of.month:
        days = _days_in_month(year, month) - data.as_of.day + 1
    else:
        days = _days_in_month(year, month)
    if days <= 0 or rest <= 0:
        return 0
    return _round_minor(Decimal(rest) / Decimal(days))


def _variable_reference_minor(data: ForecastInput) -> int:
    """Der typische variable Monatsansatz - Durchschnitt ueber die Monate des
    Horizonts. Wird ausschliesslich hier abgeleitet, nie hereingereicht: damit
    kann keine Simulationsaenderung ihn heben (kritische Regel 4)."""
    if not data.variable_monthly:
        return 0
    total = sum(item.planned_minor for item in data.variable_monthly)
    return _round_minor(Decimal(total) / Decimal(len(data.variable_monthly)))


def _horizon_end(as_of: date, horizon_months: int) -> date:
    return contract_service.add_months(as_of, horizon_months)


def _build_days(data: ForecastInput) -> tuple[list[_DayPoint], list[ForecastMonth]]:
    """Die Tagesreihe ueber den GESAMTEN Horizont.

    Sie wird immer voll gerechnet, auch wenn nur Monatswerte ausgeliefert
    werden: Tiefpunkt, erster Minustag und die Staende nach 3/6/12/24 Monaten
    sind tagesgenaue Aussagen, die eine Monatsreihe nicht hergibt.
    """
    horizon_end = _horizon_end(data.as_of, data.horizon_months)

    flows_by_day: dict[date, list[ScheduledFlow]] = {}
    for flow in data.scheduled:
        if data.as_of <= flow.on <= horizon_end:
            flows_by_day.setdefault(flow.on, []).append(flow)
    for bucket in flows_by_day.values():
        # Abfluesse zuerst, dann stabil nach Bezeichnung - dieselbe Reihenfolge
        # wie im Prototyp, damit Tagesdetail und Zahlungsliste reproduzierbar
        # sortiert sind.
        bucket.sort(key=lambda item: (item.amount_minor, item.label))

    days: list[_DayPoint] = []
    months: list[ForecastMonth] = []
    balance = data.opening_liquidity_minor
    current_key: tuple[int, int] | None = None
    per_day = 0
    income_acc = 0
    fixed_acc = 0
    variable_acc = 0

    cursor = data.as_of
    while cursor <= horizon_end:
        month_key = (cursor.year, cursor.month)
        if current_key != month_key:
            if current_key is not None:
                months.append(
                    _month_row(current_key, income_acc, fixed_acc, variable_acc, balance)
                )
            current_key = month_key
            per_day = _variable_per_day_minor(data, cursor.year, cursor.month)
            income_acc = fixed_acc = variable_acc = 0

        todays = flows_by_day.get(cursor, [])
        opening = balance
        for flow in todays:
            balance += flow.amount_minor
            if flow.amount_minor > 0:
                income_acc += flow.amount_minor
            else:
                fixed_acc += -flow.amount_minor
        balance -= per_day
        variable_acc += per_day

        days.append(
            _DayPoint(
                on=cursor,
                opening_balance_minor=opening,
                variable_minor=per_day,
                balance_minor=balance,
                flows=tuple(
                    ForecastFlow(
                        label=flow.label,
                        amount_minor=flow.amount_minor,
                        source_kind=flow.source_kind,
                        source_id=flow.source_id,
                    )
                    for flow in todays
                ),
            )
        )
        cursor += timedelta(days=1)

    if current_key is not None:
        months.append(_month_row(current_key, income_acc, fixed_acc, variable_acc, balance))
    return days, months


def _month_row(
    key: tuple[int, int], income: int, fixed: int, variable: int, closing: int
) -> ForecastMonth:
    return ForecastMonth(
        year=key[0],
        month=key[1],
        income_minor=income,
        fixed_minor=fixed,
        variable_minor=variable,
        net_minor=income - fixed - variable,
        closing_balance_minor=closing,
    )


def _is_event(day: _DayPoint) -> bool:
    if not day.flows:
        return False
    net = sum(flow.amount_minor for flow in day.flows)
    heavy = sum(1 for flow in day.flows if abs(flow.amount_minor) >= _HEAVY_ITEM_MINOR)
    return abs(net) >= _BIG_EVENT_MINOR or heavy >= 2


def _cause_of(days: list[_DayPoint], first_negative_on: date | None) -> ForecastCause | None:
    """Groesster Abflusstag in den 30 Tagen vor dem ersten Minustag."""
    if first_negative_on is None:
        return None
    window_start = first_negative_on - timedelta(days=30)
    best: ForecastCause | None = None
    for day in days:
        if day.on < window_start or day.on > first_negative_on:
            continue
        outflows = tuple(flow for flow in day.flows if flow.amount_minor < 0)
        if not outflows:
            continue
        total = sum(flow.amount_minor for flow in outflows)
        if best is None or total < best.total_minor:
            best = ForecastCause(on=day.on, total_minor=total, flows=outflows)
    return best


def _build_warnings(
    data: ForecastInput, metrics: ForecastMetrics
) -> tuple[ForecastWarning, ...]:
    context = data.context
    warnings: list[ForecastWarning] = []

    if metrics.first_negative_on is not None:
        warnings.append(
            ForecastWarning(
                code="below_zero",
                level="critical",
                context={
                    "first_negative_on": metrics.first_negative_on.isoformat(),
                    "needed_minor": metrics.needed_against_negative_minor,
                },
            )
        )
    if metrics.first_below_buffer_on is not None:
        # Bei Puffer 0 waere die Meldung deckungsgleich mit `below_zero` und
        # damit reines Rauschen - "an 731 Tagen unter Puffer" sagt dann nichts.
        if data.safety_buffer_minor > 0:
            warnings.append(
                ForecastWarning(
                    code="below_buffer",
                    level="watch",
                    context={
                        "first_below_buffer_on": metrics.first_below_buffer_on.isoformat(),
                        "needed_minor": metrics.needed_for_buffer_minor,
                    },
                )
            )

    stale_days: int | None = None
    if context.last_booking_on is not None:
        stale_days = (data.as_of - context.last_booking_on).days
    if stale_days is None or stale_days > _STALE_DAYS:
        warnings.append(
            ForecastWarning(
                code="stale_account_data",
                level="watch",
                context={
                    "last_booking_on": (
                        context.last_booking_on.isoformat() if context.last_booking_on else None
                    ),
                    "age_days": stale_days,
                },
            )
        )

    if context.income_count == 0:
        warnings.append(
            ForecastWarning(code="no_income_sources", level="critical", context={})
        )
    elif context.booked_income_monthly_minor is not None:
        threshold = _round_minor(
            Decimal(context.booked_income_monthly_minor) * _INCOME_GAP_RATIO
        )
        if data.income_monthly_minor < threshold:
            warnings.append(
                ForecastWarning(
                    code="income_below_booked",
                    level="watch",
                    context={
                        "contract_monthly_minor": data.income_monthly_minor,
                        "booked_monthly_minor": context.booked_income_monthly_minor,
                    },
                )
            )

    if context.variable_without_basis:
        warnings.append(
            ForecastWarning(
                code="no_variable_basis",
                level="watch",
                context={"variable_basis": context.variable_basis},
            )
        )
    elif context.variable_from_history:
        warnings.append(
            ForecastWarning(
                code="variable_from_history",
                level="info",
                context={"variable_basis": context.variable_basis},
            )
        )

    if context.contracts_ending_in_horizon > 0:
        warnings.append(
            ForecastWarning(
                code="horizon_beyond_contracts",
                level="info",
                context={"contract_count": context.contracts_ending_in_horizon},
            )
        )

    warnings.extend(context.extra_warnings)
    return tuple(warnings)


def _quality_level(
    data: ForecastInput, warnings: tuple[ForecastWarning, ...]
) -> ForecastQualityLevel:
    """Ampel aus den Fakten, nicht aus dem Bauch.

    `unzureichend`, sobald die Grundlage selbst nicht traegt (veraltete
    Kontodaten oder gar keine Einnahmequelle); `eingeschraenkt`, wenn die
    Rechnung laeuft, aber auf Annahmen steht.
    """
    codes = {warning.code for warning in warnings}
    if codes & {"stale_account_data", "no_income_sources"}:
        return "insufficient"
    limited = codes & {
        "no_variable_basis",
        "variable_from_history",
        "horizon_beyond_contracts",
        "income_below_booked",
    }
    if limited or data.context.excluded_account_names:
        return "limited"
    return "good"


def calculate_forecast(
    data: ForecastInput, *, granularity: ForecastGranularity = "day"
) -> ForecastResponse:
    """Reiner Rechenkern: gleiche Eingabe, gleiche Ausgabe, kein DB-Zugriff."""
    days, months = _build_days(data)
    if not days:  # pragma: no cover - horizon_months >= 1 erzwingt mindestens einen Tag
        raise ValueError("Der Horizont enthaelt keinen einzigen Tag.")

    lowest = days[0]
    first_negative: _DayPoint | None = None
    first_below_buffer: _DayPoint | None = None
    days_below_zero = 0
    days_below_buffer = 0
    for day in days:
        if day.balance_minor < lowest.balance_minor:
            lowest = day
        if day.balance_minor < 0:
            days_below_zero += 1
            if first_negative is None:
                first_negative = day
        if day.balance_minor < data.safety_buffer_minor:
            days_below_buffer += 1
            if first_below_buffer is None:
                first_below_buffer = day

    recovery_on: date | None = None
    if first_negative is not None:
        # Ausgleich zaehlt ab dem Tiefpunkt, nicht ab dem ersten Minustag -
        # sonst waere ein kurzer Zwischenanstieg schon die "Erholung".
        start = days.index(lowest)
        for day in days[start:]:
            if day.balance_minor >= 0:
                recovery_on = day.on
                break

    variable_reference = _variable_reference_minor(data)
    surplus = data.income_monthly_minor - data.fixed_monthly_minor - variable_reference

    def balance_after_months(months_ahead: int) -> int:
        """Stand am Kalenderdatum `as_of + n Monate`.

        Feste Tagesoffsets (frueher 91/182/364) treffen je nach Monatslaengen
        und Schaltjahr mehrere Tage daneben - und genau dort liegen Gehalt und
        Lastschriften, der Stand springt an diesen Tagen um ganze Monatsraten.

        Reicht der Horizont nicht so weit, ist der letzte gerechnete Tag die
        ehrlichste Antwort. Das war beim 24-Monats-Wert vorher nicht so: er
        nahm unbesehen `days[-1]` und zeigte bei einem 60-Monats-Horizont den
        Stand nach 60 statt nach 24 Monaten.
        """
        target = contract_service.add_months(data.as_of, months_ahead)
        if target >= days[-1].on:
            return days[-1].balance_minor
        return days[(target - data.as_of).days].balance_minor

    metrics = ForecastMetrics(
        lowest_balance_minor=lowest.balance_minor,
        lowest_balance_on=lowest.on,
        first_negative_on=first_negative.on if first_negative else None,
        first_below_buffer_on=first_below_buffer.on if first_below_buffer else None,
        recovery_on=recovery_on,
        days_below_zero=days_below_zero,
        days_below_buffer=days_below_buffer,
        # "Wie viel fehlt" - beide bei 0 gedeckelt, damit ein gesunder Verlauf
        # keinen negativen Fehlbetrag ausweist.
        needed_against_negative_minor=max(0, -lowest.balance_minor),
        needed_for_buffer_minor=max(0, data.safety_buffer_minor - lowest.balance_minor),
        average_monthly_surplus_minor=surplus,
        income_monthly_minor=data.income_monthly_minor,
        fixed_monthly_minor=data.fixed_monthly_minor,
        variable_monthly_minor=variable_reference,
        savings_rate_percent=_ratio_percent(surplus, data.income_monthly_minor),
        fixed_cost_ratio_percent=_ratio_percent(
            data.fixed_monthly_minor, data.income_monthly_minor
        ),
        reserve_without_income_months=(
            round(data.opening_liquidity_minor / data.fixed_monthly_minor, 1)
            if data.fixed_monthly_minor > 0
            else 0.0
        ),
        balance_in_3_months_minor=balance_after_months(3),
        balance_in_6_months_minor=balance_after_months(6),
        balance_in_12_months_minor=balance_after_months(12),
        balance_in_24_months_minor=balance_after_months(24),
    )

    warnings = _build_warnings(data, metrics)
    quality = ForecastQuality(
        level=_quality_level(data, warnings),
        last_booking_on=data.context.last_booking_on,
        contract_count=data.context.contract_count,
        income_count=data.context.income_count,
        variable_basis=data.context.variable_basis,
        excluded_account_names=data.context.excluded_account_names,
        contracts_ending_in_horizon=data.context.contracts_ending_in_horizon,
    )

    daily: tuple[ForecastDay, ...] = ()
    if granularity == "day":
        daily = tuple(
            ForecastDay(
                on=day.on,
                opening_balance_minor=day.opening_balance_minor,
                variable_minor=day.variable_minor,
                balance_minor=day.balance_minor,
                is_event=_is_event(day),
                flows=day.flows,
            )
            for day in days
            if day.on <= data.daily_until
        )

    return ForecastResponse(
        as_of=data.as_of,
        horizon_months=data.horizon_months,
        granularity=granularity,
        opening_liquidity_minor=data.opening_liquidity_minor,
        opening_accounts=data.opening_accounts,
        bound_reserve_minor=data.bound_reserve_minor,
        safety_buffer_minor=data.safety_buffer_minor,
        daily=daily,
        monthly=tuple(months),
        metrics=metrics,
        cause=_cause_of(days, metrics.first_negative_on),
        warnings=warnings,
        quality=quality,
    )


def group_upcoming(result: ForecastResponse, *, until: date) -> list[ForecastDayGroup]:
    """Die naechsten Zahlungen nach Tagen gruppiert, mit Tagessumme und dem
    Stand danach. Tage ohne Bewegung entfallen."""
    groups: list[ForecastDayGroup] = []
    for day in result.daily:
        if day.on > until or not day.flows:
            continue
        total = sum(flow.amount_minor for flow in day.flows)
        heavy_items = sum(
            1 for flow in day.flows if abs(flow.amount_minor) >= _HEAVY_ITEM_MINOR
        )
        groups.append(
            ForecastDayGroup(
                on=day.on,
                total_minor=total,
                balance_after_minor=day.balance_minor,
                is_heavy=heavy_items >= 2 or abs(total) >= _HEAVY_DAY_TOTAL_MINOR,
                flows=day.flows,
            )
        )
    return groups


# ---------------------------------------------------------------------------
# Was-waere-wenn: reine Funktion VOR dem Kern
# ---------------------------------------------------------------------------


def _changed_amount_minor(original_minor: int, change: ForecastChange) -> int:
    """Neuer vorzeichenbehafteter Betrag eines bestehenden Flows.

    Das Vorzeichen (die Richtung) bleibt erhalten; `amount_minor` und
    `amount_percent` wirken auf den BETRAG. Nur so ist eine Kostenreduktion mit
    negativem Delta ausdrueckbar (Plan 6.2) - mit reiner Vorzeichenaddition
    wuerde derselbe negative Delta eine Ausgabe teurer machen.
    """
    sign = 1 if original_minor >= 0 else -1
    magnitude = abs(original_minor)
    if change.amount_mode == "absolute":
        assert change.amount_minor is not None
        return sign * abs(change.amount_minor)
    if change.amount_mode == "delta":
        assert change.amount_minor is not None
        return sign * max(0, magnitude + change.amount_minor)
    assert change.amount_percent is not None
    factor = Decimal(1) + (change.amount_percent / Decimal(100))
    return sign * max(0, _round_minor(Decimal(magnitude) * factor))


def _change_due_dates(change: ForecastChange, *, as_of: date, horizon_end: date) -> list[date]:
    """Faelligkeiten einer neuen Reihe - ueber dieselbe Rechnung wie ein
    Vertrag (`contract_service.due_dates_in_window`), nicht nachgebaut."""
    if change.frequency == "once":
        return [change.starts_on] if as_of <= change.starts_on <= horizon_end else []

    if change.frequency == "weekly":
        anchor = change.starts_on
    else:
        day = change.due_day or change.starts_on.day
        last = _days_in_month(change.starts_on.year, change.starts_on.month)
        anchor = date(change.starts_on.year, change.starts_on.month, min(day, last))

    window_start = max(as_of, change.starts_on)
    if window_start > horizon_end:
        return []
    return contract_service.due_dates_in_window(
        next_due_date_value=anchor,
        frequency=change.frequency,
        due_rule="day_of_month",
        due_day=change.due_day,
        starts_on=change.starts_on,
        ends_on=change.ends_on,
        window_start=window_start,
        window_end=horizon_end,
    )


def _is_permanent(change: ForecastChange, horizon_end: date) -> bool:
    """Wirkt die Aenderung ueber den ganzen Horizont oder laeuft sie vorher aus?

    Die Monatswerte (Ueberschuss, Sparquote, Fixkostenquote, Reserve ohne
    Einkommen) beschreiben den DAUERZUSTAND. Eine Aenderung, die vor dem
    Horizontende endet, veraendert ihn nicht - sie gehoert in die Tagesreihe
    und in den Tiefpunkt, nicht in die Monatskennzahlen. Ohne diese
    Unterscheidung liest sich "drei Monate Kurzarbeit" wie eine dauerhafte
    Gehaltskuerzung.
    """
    return change.ends_on is None or change.ends_on >= horizon_end


def apply_changes(base: ForecastInput, changes: tuple[ForecastChange, ...]) -> ForecastInput:
    """Reine Funktion vor dem Kern: dieselbe Vorschau mit veraenderten
    Annahmen (Leitsatz 2). Ohne Aenderungen liefert sie die Eingabe
    unveraendert zurueck - das ist ein Test, keine Zusicherung auf Zuruf.

    `variable_monthly` wird ausdruecklich NICHT angefasst (kritische Regel 4).
    """
    if not changes:
        return base

    horizon_end = _horizon_end(base.as_of, base.horizon_months)
    flows = list(base.scheduled)
    terms = {term.contract_id: term for term in base.contract_terms}
    income_monthly = base.income_monthly_minor
    fixed_monthly = base.fixed_monthly_minor

    for change in changes:
        if change.change_type in ("income_change", "expense_change"):
            target = change.target_contract_id
            assert target is not None
            # ends_on begrenzt die Aenderung: "sechs Monate weniger Miete"
            # muss danach wieder auf den Vertragsbetrag zurueckfallen. Vorher
            # zaehlte nur starts_on - jede befristete Aenderung lief bis zum
            # Horizontende weiter und rechnete die Entlastung zu gross.
            replaced: list[ScheduledFlow] = []
            for flow in flows:
                within_change = change.starts_on <= flow.on and (
                    change.ends_on is None or flow.on <= change.ends_on
                )
                if flow.source_id == target and within_change:
                    replaced.append(
                        flow.model_copy(
                            update={
                                "amount_minor": _changed_amount_minor(
                                    flow.amount_minor, change
                                ),
                                "source_kind": "simulation",
                            }
                        )
                    )
                else:
                    replaced.append(flow)
            flows = replaced

            term = terms.get(target) if _is_permanent(change, horizon_end) else None
            if term is not None:
                signed = term.amount_minor if term.direction == "income" else -term.amount_minor
                new_signed = _changed_amount_minor(signed, change)
                old_monthly = contract_service.monthly_minor(term.amount_minor, term.frequency)
                new_monthly = contract_service.monthly_minor(abs(new_signed), term.frequency)
                if term.direction == "income":
                    income_monthly += new_monthly - old_monthly
                else:
                    fixed_monthly += new_monthly - old_monthly
                terms[target] = term.model_copy(update={"amount_minor": max(1, abs(new_signed))})

        elif change.change_type in ("income_end", "expense_end"):
            target = change.target_contract_id
            assert target is not None
            flows = [
                flow
                for flow in flows
                if not (flow.source_id == target and flow.on >= change.starts_on)
            ]
            term = terms.pop(target, None)
            if term is not None:
                monthly = contract_service.monthly_minor(term.amount_minor, term.frequency)
                if term.direction == "income":
                    income_monthly -= monthly
                else:
                    fixed_monthly -= monthly

        else:
            assert change.amount_minor is not None or change.amount_percent is not None
            amount = change.amount_minor if change.amount_minor is not None else 0
            for due in _change_due_dates(change, as_of=base.as_of, horizon_end=horizon_end):
                flows.append(
                    ScheduledFlow(
                        on=due,
                        amount_minor=amount,
                        label=change.label,
                        source_kind="simulation",
                        source_id=None,
                    )
                )
            if _is_permanent(change, horizon_end):
                if change.change_type == "income_add":
                    income_monthly += contract_service.monthly_minor(abs(amount), change.frequency)
                elif change.change_type == "expense_add":
                    fixed_monthly += contract_service.monthly_minor(abs(amount), change.frequency)
            # `one_off` hat keine Monatswirkung - genau eine Zahlung.

    return base.model_copy(
        update={
            "scheduled": tuple(flows),
            "contract_terms": tuple(terms.values()),
            "income_monthly_minor": income_monthly,
            "fixed_monthly_minor": fixed_monthly,
        }
    )


def tax_class_hint_applies(base: ForecastInput, changes: tuple[ForecastChange, ...]) -> bool:
    """Kommt ein zweites GEHALT einer ZWEITEN Person hinzu?

    Die App sagt dann, dass beide Nettobetraege zu pruefen sind, statt so zu
    tun, als kenne sie die neue Steuerklassenkombination (Plan 12.1).

    Bis Stufe 6 loeste der Hinweis aus, sobald irgendein Einnahmevertrag
    existierte - `contracts` kannte weder Art noch Person, und "bereits eine
    Einnahme vorhanden" war das genaueste verfuegbare Signal. Seither ist es
    das nicht mehr, und die alte Fassung war in zwei Faellen schlicht falsch:
    neben einer Mieteinnahme oder einer Rente aendert ein hinzukommendes
    Gehalt keine Steuerklassenkombination, und ein Zweitjob DERSELBEN Person
    auch nicht.

    Ausgeloest wird also nur bei einem `income_add` mit `income_kind='salary'`,
    wenn zugleich ein Vertrag mit `income_kind='salary'` existiert, der einer
    anderen Person gehoert - oder gar keiner. "Gar keiner" zaehlt mit, weil ein
    ungepflegtes `person_id` nicht bedeutet, dass es dieselbe Person ist; im
    Zweifel ist der Hinweis die harmlosere Antwort.
    """
    salary_terms = [term for term in base.contract_terms if term.income_kind == "salary"]
    if not salary_terms:
        return False
    return any(
        change.change_type == "income_add"
        and change.income_kind == "salary"
        and any(
            term.person_id is None or term.person_id != change.person_id
            for term in salary_terms
        )
        for change in changes
    )


def compare_forecasts(base: ForecastResponse, simulation: ForecastResponse) -> ForecastComparison:
    base_metrics = base.metrics
    sim_metrics = simulation.metrics
    shift: int | None = None
    if base_metrics.first_negative_on is not None and sim_metrics.first_negative_on is not None:
        shift = (sim_metrics.first_negative_on - base_metrics.first_negative_on).days
    return ForecastComparison(
        surplus_before_minor=base_metrics.average_monthly_surplus_minor,
        surplus_after_minor=sim_metrics.average_monthly_surplus_minor,
        surplus_delta_minor=(
            sim_metrics.average_monthly_surplus_minor
            - base_metrics.average_monthly_surplus_minor
        ),
        savings_rate_before_percent=base_metrics.savings_rate_percent,
        savings_rate_after_percent=sim_metrics.savings_rate_percent,
        savings_rate_delta_percent=round(
            sim_metrics.savings_rate_percent - base_metrics.savings_rate_percent, 1
        ),
        fixed_cost_ratio_before_percent=base_metrics.fixed_cost_ratio_percent,
        fixed_cost_ratio_after_percent=sim_metrics.fixed_cost_ratio_percent,
        fixed_cost_ratio_delta_percent=round(
            sim_metrics.fixed_cost_ratio_percent - base_metrics.fixed_cost_ratio_percent, 1
        ),
        reserve_without_income_before_months=base_metrics.reserve_without_income_months,
        reserve_without_income_after_months=sim_metrics.reserve_without_income_months,
        reserve_without_income_delta_months=round(
            sim_metrics.reserve_without_income_months
            - base_metrics.reserve_without_income_months,
            1,
        ),
        balance_delta_3_months_minor=(
            sim_metrics.balance_in_3_months_minor - base_metrics.balance_in_3_months_minor
        ),
        balance_delta_6_months_minor=(
            sim_metrics.balance_in_6_months_minor - base_metrics.balance_in_6_months_minor
        ),
        balance_delta_12_months_minor=(
            sim_metrics.balance_in_12_months_minor - base_metrics.balance_in_12_months_minor
        ),
        balance_delta_24_months_minor=(
            sim_metrics.balance_in_24_months_minor - base_metrics.balance_in_24_months_minor
        ),
        lowest_balance_delta_minor=(
            sim_metrics.lowest_balance_minor - base_metrics.lowest_balance_minor
        ),
        first_negative_shift_days=shift,
        resolves_shortfall=(
            base_metrics.first_negative_on is not None
            and sim_metrics.first_negative_on is None
        ),
    )


def contribution_of(
    base_input: ForecastInput,
    base_result: ForecastResponse,
    change: ForecastChange,
    index: int,
) -> ForecastContribution:
    """Ein EIGENER Rechenlauf mit nur dieser Aenderung. Ohne ihn liesse sich
    nicht sagen, welche Annahme den Engpass tatsaechlich beseitigt."""
    single = calculate_forecast(apply_changes(base_input, (change,)), granularity="month")
    monthly = (single.metrics.income_monthly_minor - base_result.metrics.income_monthly_minor) - (
        single.metrics.fixed_monthly_minor - base_result.metrics.fixed_monthly_minor
    )
    base_negative = base_result.metrics.first_negative_on
    single_negative = single.metrics.first_negative_on
    shift: int | None = None
    if base_negative is not None and single_negative is not None:
        shift = (single_negative - base_negative).days
    return ForecastContribution(
        change_index=index,
        monthly_minor=monthly,
        resolves_shortfall=base_negative is not None and single_negative is None,
        shift_days=shift,
        first_negative_on=single_negative,
    )


# ---------------------------------------------------------------------------
# Loader: von der Datenbank zu ForecastInput
# ---------------------------------------------------------------------------


class _AccountRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    account_id: UUID
    name: str
    account_type: str
    included: bool
    # 'heuristic' = allein aus der Kontoart abgeleitet, 'explicit' = der
    # Nutzer hat `accounts.forecast_liquidity` gesetzt.
    reason: str


class _AccountSelection(BaseModel):
    model_config = ConfigDict(frozen=True)

    included_ids: tuple[UUID, ...]
    excluded_names: tuple[str, ...]
    rows: tuple[_AccountRow, ...] = ()


async def _load_accounts(
    session: AsyncSession, household_id: UUID, *, include_savings: bool
) -> _AccountSelection:
    result = await session.execute(
        text(
            "select id, name, cast(account_type as text) as account_type, forecast_liquidity "
            "from accounts where household_id = :household_id and not is_closed "
            "order by lower(name)"
        ),
        {"household_id": str(household_id)},
    )
    included: list[UUID] = []
    excluded: list[str] = []
    rows: list[_AccountRow] = []
    for row in result:
        explicit = row.forecast_liquidity is not None
        if explicit:
            liquid = bool(row.forecast_liquidity)
        else:
            liquid = row.account_type in _LIQUID_ACCOUNT_TYPES
        if not liquid and include_savings and row.account_type == _SAVINGS_ACCOUNT_TYPE:
            liquid = True
        account_id = UUID(str(row.id))
        if liquid:
            included.append(account_id)
        else:
            excluded.append(str(row.name))
        # Auch ausgeschlossene Konten wandern in die Aufschluesselung: ein
        # nicht mitgezaehltes Kreditkartenminus laesst die Lage besser
        # aussehen, als sie ist, und der Nutzer soll das sehen koennen.
        rows.append(
            _AccountRow(
                account_id=account_id,
                name=str(row.name),
                account_type=str(row.account_type),
                included=liquid,
                reason="explicit" if explicit else "heuristic",
            )
        )
    return _AccountSelection(
        included_ids=tuple(included), excluded_names=tuple(excluded), rows=tuple(rows)
    )


async def _load_last_booking_per_account(
    session: AsyncSession,
    household_id: UUID,
    *,
    as_of: date,
    account_ids: list[UUID],
) -> dict[UUID, date]:
    """Letzter gebuchter Umsatz je Konto.

    Steht in der Aufschluesselung neben dem Saldo: ein Konto, dessen letzte
    Buchung Wochen zurueckliegt, traegt einen veralteten Wert bei - und genau
    das ist an einer Summe nicht zu erkennen.
    """
    if not account_ids:
        return {}
    result = await session.execute(
        text(
            "select account_id, max(booked_date) as letzte "
            "from transactions "
            "where household_id = :household_id and not is_archived "
            "and booked_date <= :as_of "
            "and account_id = any(cast(:ids as uuid[])) "
            "group by account_id"
        ),
        {
            "household_id": str(household_id),
            "as_of": as_of,
            "ids": [str(value) for value in account_ids],
        },
    )
    return {UUID(str(row.account_id)): row.letzte for row in result}


async def _load_contract_flows(
    session: AsyncSession,
    household_id: UUID,
    *,
    as_of: date,
    horizon_end: date,
) -> tuple[list[ScheduledFlow], list[ForecastContractTerm], int, int, int]:
    """Vertragsfaelligkeiten beider Richtungen, dazu die normierten
    Monatssummen. Erst je Vertrag normieren, dann summieren - sonst weichen
    Vorschau, Budget und Berichte um Rundungscent voneinander ab (Plan 5.2)."""
    result = await session.execute(
        text(
            "select id, name, amount_minor, frequency, direction, due_rule, due_day, "
            "next_due_date, starts_on, ends_on, income_kind, person_id from contracts "
            "where household_id = :household_id "
            "and status = any(cast(:statuses as text[])) "
            "and merged_into_id is null "
            "and (starts_on is null or starts_on <= :window_end) "
            "and (ends_on is null or ends_on >= :window_start)"
        ),
        {
            "household_id": str(household_id),
            # Einzige Wahrheit fuer "zaehlt mit": ein gekuendigter Vertrag kostet
            # bis zu seinem Enddatum weiter Geld.
            "statuses": list(contract_service._COUNTING_STATUSES),
            "window_start": as_of,
            "window_end": horizon_end,
        },
    )

    # Bereits abgeglichene oder ausdruecklich uebersprungene Faelligkeiten
    # erzeugen keinen Forecast-Flow mehr (Plan 4.2 und 5.2, Punkt 2).
    settled = await session.execute(
        text(
            "select contract_id, due_date from contract_occurrences "
            "where household_id = :household_id and status in ('matched', 'skipped') "
            "and due_date >= :window_start and due_date <= :window_end"
        ),
        {
            "household_id": str(household_id),
            "window_start": as_of,
            "window_end": horizon_end,
        },
    )
    settled_dues = {(UUID(str(row.contract_id)), row.due_date) for row in settled}

    flows: list[ScheduledFlow] = []
    terms: list[ForecastContractTerm] = []
    income_monthly = 0
    fixed_monthly = 0
    income_count = 0
    for row in result:
        contract_id = UUID(str(row.id))
        amount = int(row.amount_minor)
        signed = amount if row.direction == "income" else -amount
        monthly = contract_service.monthly_minor(amount, row.frequency)
        if row.direction == "income":
            income_monthly += monthly
            income_count += 1
        else:
            fixed_monthly += monthly
        terms.append(
            ForecastContractTerm(
                contract_id=contract_id,
                label=str(row.name),
                direction=row.direction,
                amount_minor=amount,
                frequency=row.frequency,
                income_kind=row.income_kind,
                person_id=UUID(str(row.person_id)) if row.person_id is not None else None,
            )
        )
        for due in contract_service.due_dates_in_window(
            next_due_date_value=row.next_due_date,
            frequency=row.frequency,
            due_rule=row.due_rule,
            due_day=row.due_day,
            starts_on=row.starts_on,
            ends_on=row.ends_on,
            window_start=as_of,
            window_end=horizon_end,
        ):
            if (contract_id, due) in settled_dues:
                continue
            flows.append(
                ScheduledFlow(
                    on=due,
                    amount_minor=signed,
                    label=str(row.name),
                    source_kind="contract",
                    source_id=contract_id,
                )
            )
    return flows, terms, income_monthly, fixed_monthly, income_count


async def _load_future_transaction_flows(
    session: AsyncSession,
    household_id: UUID,
    *,
    as_of: date,
    horizon_end: date,
    account_ids: tuple[UUID, ...],
) -> list[ScheduledFlow]:
    """Bereits gebuchte oder vorgemerkte Zukunft.

    Umbuchungen werden bewusst NICHT ausgefiltert: der Startsaldo enthaelt sie
    ebenfalls. Zwischen zwei einbezogenen Konten heben sie sich auf, von einem
    ausgeschlossenen Konto sind sie ein echter Zufluss.
    """
    if not account_ids:
        return []
    result = await session.execute(
        text(
            "select t.id, t.booked_date, t.amount_minor, "
            "coalesce(nullif(btrim(t.payee_label), ''), nullif(btrim(t.note), ''), "
            "'Vorgemerkte Buchung') as label "
            "from transactions t "
            "where t.household_id = :household_id and not t.is_archived "
            "and t.booked_date > :as_of and t.booked_date <= :window_end "
            "and t.account_id = any(cast(:account_ids as uuid[])) "
            "order by t.booked_date, t.amount_minor"
        ),
        {
            "household_id": str(household_id),
            "as_of": as_of,
            "window_end": horizon_end,
            "account_ids": [str(item) for item in account_ids],
        },
    )
    return [
        ScheduledFlow(
            on=row.booked_date,
            amount_minor=int(row.amount_minor),
            label=str(row.label),
            source_kind="transaction",
            source_id=UUID(str(row.id)),
        )
        for row in result
    ]


# Eine Buchung, die per `contract_occurrences` nachweislich zu einem zaehlenden
# Ausgabenvertrag gehoert, darf NICHT in die variable Basis - sonst zaehlt
# dieselbe Zahlung doppelt: einmal als Vertragsfaelligkeit in
# `fixed_monthly_minor`, ein zweites Mal als variabler Verbrauch.
#
# Der Kategorieausschluss allein reicht dafuer nicht. Er vergleicht die
# Kategorie des VERTRAGS mit der Kategorie der BUCHUNG; weichen beide
# voneinander ab - etwa weil eine Krankenversicherungspraemie als "Arzt &
# Zahnarzt" kategorisiert wurde -, greift er ins Leere. Die exakte Zuordnung
# liegt bereits in `contract_occurrences`, und `_load_contract_flows` liest
# sie fuer die spiegelbildliche Entdoppelung schon.
#
# `contract_occurrences_transaction_uniq` garantiert, dass eine Buchung
# hoechstens einen Vertrag erfuellt - der Join kann also nicht doppelt
# ausschliessen. Der Vertragsfilter ist derselbe wie in
# `covered_category_ids` und `_load_contract_flows`.
CONTRACT_BOUND_EXPENSE_SQL: Final = (
    "not exists ("
    "select 1 from contract_occurrences o "
    "join contracts k on k.id = o.contract_id "
    "where o.transaction_id = t.id "
    "and k.household_id = t.household_id "
    "and k.merged_into_id is null "
    "and k.direction = 'expense' "
    "and k.status = any(cast(:statuses as text[]))"
    ")"
)


async def covered_category_ids(session: AsyncSession, household_id: UUID) -> list[str]:
    """Kategorien mit Vertragsdeckung - deren Planbetrag ist keine variable
    Ausgabe, sonst zaehlte derselbe Vertrag zweimal.

    Oeffentlich, weil der Bericht "variable Kosten"
    (routes/reports._compute_variable_costs) genau dieselbe Abgrenzung braucht.
    Eine zweite Kopie waere zwei Wahrheiten: die Vorschau nennte dem Nutzer
    einen variablen Monatsansatz, der Bericht einen anderen - und beide
    behaupteten, dasselbe zu meinen.
    """
    result = await session.execute(
        text(
            "select distinct category_id from contracts "
            "where household_id = :household_id and merged_into_id is null "
            "and category_id is not null and direction = 'expense' "
            "and status = any(cast(:statuses as text[]))"
        ),
        {
            "household_id": str(household_id),
            "statuses": list(contract_service._COUNTING_STATUSES),
        },
    )
    return [str(row.category_id) for row in result]


async def _load_variable_months(
    session: AsyncSession,
    household_id: UUID,
    *,
    as_of: date,
    horizon_end: date,
    settings: ForecastSettings,
) -> tuple[tuple[VariableMonth, ...], bool, bool]:
    """Variabler Monatsansatz je Monat des Horizonts.

    Reihenfolge nach Plan 4.3: Budgetplan des Monats, sonst Durchschnitt der
    letzten `variable_lookback_months` aus den tatsaechlichen Buchungen, sonst
    0 mit Warnung. `variable_basis` entscheidet, welcher Weg gilt.
    """
    covered = await covered_category_ids(session, household_id)

    first_index = _month_index(as_of.year, as_of.month)
    last_index = _month_index(horizon_end.year, horizon_end.month)

    planned_rows = await session.execute(
        text(
            "select b.year, b.month, coalesce(sum(ba.planned_minor), 0) as planned_minor "
            "from budgets b "
            "join budget_allocations ba on ba.budget_id = b.id "
            "join categories c on c.id = ba.category_id "
            "where b.household_id = :household_id "
            "and (b.year * 12 + b.month - 1) between :first_index and :last_index "
            "and coalesce(c.default_type, '') <> 'income' "
            "and c.default_type is distinct from 'transfer' "
            "and not (c.id = any(cast(:covered as uuid[]))) "
            "group by b.year, b.month"
        ),
        {
            "household_id": str(household_id),
            "first_index": first_index,
            "last_index": last_index,
            "covered": covered,
        },
    )
    planned_by_month = {
        (int(row.year), int(row.month)): max(0, int(row.planned_minor)) for row in planned_rows
    }

    # Historie: die letzten vollen Monate VOR dem laufenden Monat.
    lookback_end = date(as_of.year, as_of.month, 1) - timedelta(days=1)
    lookback_start_index = _month_index(as_of.year, as_of.month) - settings.variable_lookback_months
    lookback_start = date(
        lookback_start_index // 12, lookback_start_index % 12 + 1, 1
    )
    history_total = await session.execute(
        text(
            "select coalesce(sum(-s.amount_minor), 0) as spent_minor "
            "from transaction_splits s "
            "join transactions t on t.id = s.transaction_id "
            "join accounts a on a.id = t.account_id "
            "left join categories c on c.id = s.category_id "
            "where t.household_id = :household_id and a.on_budget = true "
            "and not t.is_archived and not t.is_transfer "
            "and t.booked_date >= :start_date and t.booked_date <= :end_date "
            f"and {CASHFLOW_EXPENSE_PREDICATE} "
            "and (s.category_id is null "
            "or not (s.category_id = any(cast(:covered as uuid[])))) "
            f"and {CONTRACT_BOUND_EXPENSE_SQL}"
        ),
        {
            "household_id": str(household_id),
            "start_date": lookback_start,
            "end_date": lookback_end,
            "covered": covered,
            "statuses": list(contract_service._COUNTING_STATUSES),
        },
    )
    history_sum = max(0, int(history_total.scalar() or 0))
    has_history = history_sum > 0
    history_avg = (
        _round_minor(Decimal(history_sum) / Decimal(settings.variable_lookback_months))
        if has_history
        else 0
    )

    # Bereits gebuchte variable Ausgaben je Monat - inklusive zukuenftig
    # datierter, damit eine Vormerkung nicht doppelt wirkt.
    booked_rows = await session.execute(
        text(
            "select extract(year from t.booked_date)::int as year, "
            "extract(month from t.booked_date)::int as month, "
            "coalesce(sum(-s.amount_minor), 0) as spent_minor "
            "from transaction_splits s "
            "join transactions t on t.id = s.transaction_id "
            "join accounts a on a.id = t.account_id "
            "left join categories c on c.id = s.category_id "
            "where t.household_id = :household_id and a.on_budget = true "
            "and not t.is_archived and not t.is_transfer "
            "and t.booked_date >= :start_date and t.booked_date <= :end_date "
            f"and {CASHFLOW_EXPENSE_PREDICATE} "
            "and (s.category_id is null "
            "or not (s.category_id = any(cast(:covered as uuid[])))) "
            f"and {CONTRACT_BOUND_EXPENSE_SQL} "
            "group by 1, 2"
        ),
        {
            "household_id": str(household_id),
            "start_date": date(as_of.year, as_of.month, 1),
            "end_date": horizon_end,
            "covered": covered,
            "statuses": list(contract_service._COUNTING_STATUSES),
        },
    )
    booked_by_month = {
        (int(row.year), int(row.month)): max(0, int(row.spent_minor)) for row in booked_rows
    }

    months: list[VariableMonth] = []
    from_history = False
    without_basis = False
    for index in range(first_index, last_index + 1):
        year, month = index // 12, index % 12 + 1
        planned = planned_by_month.get((year, month))
        if settings.variable_basis == "budget":
            value = planned if planned is not None else history_avg
            used_history = planned is None and has_history
        elif settings.variable_basis == "history":
            value = history_avg
            used_history = has_history
        else:
            value = max(planned or 0, history_avg)
            used_history = history_avg > (planned or 0)
        from_history = from_history or used_history
        if value <= 0:
            without_basis = True
        months.append(
            VariableMonth(
                year=year,
                month=month,
                planned_minor=max(0, value),
                booked_minor=booked_by_month.get((year, month), 0),
            )
        )
    return tuple(months), from_history, without_basis


async def _load_context_facts(
    session: AsyncSession,
    household_id: UUID,
    *,
    as_of: date,
    horizon_end: date,
    account_ids: tuple[UUID, ...],
    lookback_months: int,
) -> tuple[date | None, int, int, int | None]:
    """Datenstand, Vertragszahlen und der gebuchte Einnahmendurchschnitt."""
    last_booking: date | None = None
    if account_ids:
        row = (
            await session.execute(
                text(
                    "select max(booked_date) as last_booking from transactions "
                    "where household_id = :household_id and not is_archived "
                    "and booked_date <= :as_of "
                    "and account_id = any(cast(:account_ids as uuid[]))"
                ),
                {
                    "household_id": str(household_id),
                    "as_of": as_of,
                    "account_ids": [str(item) for item in account_ids],
                },
            )
        ).first()
        if row is not None and row.last_booking is not None:
            last_booking = row.last_booking

    counts = (
        await session.execute(
            text(
                "select count(*) as total, "
                "count(*) filter (where ends_on is not null and ends_on >= :as_of "
                "and ends_on <= :horizon_end) as ending "
                "from contracts where household_id = :household_id "
                "and status = any(cast(:statuses as text[])) and merged_into_id is null "
                "and (starts_on is null or starts_on <= :horizon_end) "
                "and (ends_on is null or ends_on >= :as_of)"
            ),
            {
                "household_id": str(household_id),
                "as_of": as_of,
                "horizon_end": horizon_end,
                "statuses": list(contract_service._COUNTING_STATUSES),
            },
        )
    ).one()

    lookback_end = date(as_of.year, as_of.month, 1) - timedelta(days=1)
    lookback_start_index = _month_index(as_of.year, as_of.month) - lookback_months
    lookback_start = date(lookback_start_index // 12, lookback_start_index % 12 + 1, 1)
    booked_income = await budget_service.booked_income_minor(
        session, household_id, lookback_start, lookback_end
    )
    booked_income_monthly = (
        _round_minor(Decimal(booked_income) / Decimal(lookback_months))
        if booked_income > 0
        else None
    )
    return last_booking, int(counts.total), int(counts.ending), booked_income_monthly


async def _load_bound_reserve_minor(
    session: AsyncSession, household_id: UUID, *, as_of: date
) -> int:
    """Gebundener Betrag der Ruecklagentoepfe - nur zur Anzeige.

    Wird bewusst NICHT vom Kontostand abgezogen: `reserve_pots` hat keinen
    Kontobezug, und laege die Ruecklage auf einem ausgeschlossenen Konto,
    entstuende ein kuenstlich negatives "frei verfuegbar" (Plan 4.4).
    """
    overview = await reserve_service.get_reserve_overview(
        session, household_id, year=as_of.year, month=as_of.month, today=as_of
    )
    return overview.total_balance_minor


async def load_forecast_input(
    session: AsyncSession,
    household_id: UUID,
    *,
    as_of: date,
    horizon_months: int | None = None,
    include_savings: bool = False,
    daily_days: int | None = None,
) -> ForecastInput:
    """Traegt alle Eingaben des Rechenkerns zusammen. Committet nie."""
    settings = await get_forecast_settings(session, household_id)
    horizon = horizon_months if horizon_months is not None else settings.horizon_months
    if not 1 <= horizon <= _MAX_HORIZON_MONTHS:
        raise ValueError(f"horizon_months muss zwischen 1 und {_MAX_HORIZON_MONTHS} liegen.")
    horizon_end = _horizon_end(as_of, horizon)

    daily_limit = contract_service.add_months(as_of, _DAILY_DETAIL_MONTHS)
    if daily_days is not None:
        daily_limit = min(daily_limit, as_of + timedelta(days=daily_days))
    daily_until = min(daily_limit, horizon_end)

    accounts = await _load_accounts(session, household_id, include_savings=include_savings)
    # Salden ALLER offenen Konten, nicht nur der einbezogenen: die
    # Aufschluesselung soll auch zeigen, was bewusst draussen bleibt.
    all_ids = [row.account_id for row in accounts.rows]
    balances = await account_balance_service.fetch_account_balances_as_of(
        session, household_id, as_of_date=as_of, account_ids=all_ids
    )
    balance_by_account = {item.account_id: item.balance_minor for item in balances}
    opening = sum(
        balance_by_account.get(account_id, 0) for account_id in accounts.included_ids
    )
    last_booking_by_account = await _load_last_booking_per_account(
        session, household_id, as_of=as_of, account_ids=all_ids
    )
    opening_accounts = tuple(
        ForecastOpeningAccount(
            account_id=row.account_id,
            name=row.name,
            account_type=row.account_type,
            included=row.included,
            balance_minor=balance_by_account.get(row.account_id, 0),
            last_booking_on=last_booking_by_account.get(row.account_id),
            reason="explicit" if row.reason == "explicit" else "heuristic",
        )
        for row in accounts.rows
    )

    (
        contract_flows,
        terms,
        income_monthly,
        fixed_monthly,
        income_count,
    ) = await _load_contract_flows(
        session, household_id, as_of=as_of, horizon_end=horizon_end
    )
    transaction_flows = await _load_future_transaction_flows(
        session,
        household_id,
        as_of=as_of,
        horizon_end=horizon_end,
        account_ids=accounts.included_ids,
    )
    variable_months, from_history, without_basis = await _load_variable_months(
        session, household_id, as_of=as_of, horizon_end=horizon_end, settings=settings
    )
    last_booking, contract_count, ending_count, booked_income = await _load_context_facts(
        session,
        household_id,
        as_of=as_of,
        horizon_end=horizon_end,
        account_ids=accounts.included_ids,
        lookback_months=settings.variable_lookback_months,
    )
    bound_reserve = await _load_bound_reserve_minor(session, household_id, as_of=as_of)

    return ForecastInput(
        as_of=as_of,
        horizon_months=horizon,
        daily_until=daily_until,
        opening_liquidity_minor=opening,
        opening_accounts=opening_accounts,
        scheduled=tuple(contract_flows + transaction_flows),
        variable_monthly=variable_months,
        contract_terms=tuple(terms),
        bound_reserve_minor=bound_reserve,
        safety_buffer_minor=settings.safety_buffer_minor,
        income_monthly_minor=income_monthly,
        fixed_monthly_minor=fixed_monthly,
        context=ForecastContext(
            last_booking_on=last_booking,
            contract_count=contract_count,
            income_count=income_count,
            variable_basis=settings.variable_basis,
            variable_from_history=from_history,
            variable_without_basis=without_basis,
            excluded_account_names=accounts.excluded_names,
            contracts_ending_in_horizon=ending_count,
            booked_income_monthly_minor=booked_income,
        ),
    )


# ---------------------------------------------------------------------------
# Einstellungen
# ---------------------------------------------------------------------------


async def get_forecast_settings(session: AsyncSession, household_id: UUID) -> ForecastSettings:
    row = (
        await session.execute(
            text(
                "select safety_buffer_minor, variable_basis, variable_lookback_months, "
                "horizon_months, notify_shortfall, notify_below_buffer, "
                "notify_income_missing, updated_at from forecast_settings "
                "where household_id = :household_id"
            ),
            {"household_id": str(household_id)},
        )
    ).first()
    if row is None:
        return _DEFAULT_SETTINGS
    return ForecastSettings(
        safety_buffer_minor=int(row.safety_buffer_minor),
        variable_basis=row.variable_basis,
        variable_lookback_months=int(row.variable_lookback_months),
        horizon_months=int(row.horizon_months),
        notify_shortfall=bool(row.notify_shortfall),
        notify_below_buffer=bool(row.notify_below_buffer),
        notify_income_missing=bool(row.notify_income_missing),
        updated_at=row.updated_at,
    )


async def update_forecast_settings(
    session: AsyncSession,
    household_id: UUID,
    payload: ForecastSettingsInput,
    *,
    updated_by: UUID | None = None,
) -> ForecastSettings:
    """Legt den Datensatz beim ersten Aufruf an. Committet nie."""
    await session.execute(
        text(
            "insert into forecast_settings (household_id, safety_buffer_minor, "
            "variable_basis, variable_lookback_months, horizon_months, "
            "notify_shortfall, notify_below_buffer, notify_income_missing, "
            "updated_at, updated_by) "
            "values (:household_id, :safety_buffer_minor, :variable_basis, "
            ":variable_lookback_months, :horizon_months, :notify_shortfall, "
            ":notify_below_buffer, :notify_income_missing, now(), :updated_by) "
            "on conflict (household_id) do update set "
            "safety_buffer_minor = excluded.safety_buffer_minor, "
            "variable_basis = excluded.variable_basis, "
            "variable_lookback_months = excluded.variable_lookback_months, "
            "horizon_months = excluded.horizon_months, "
            "notify_shortfall = excluded.notify_shortfall, "
            "notify_below_buffer = excluded.notify_below_buffer, "
            "notify_income_missing = excluded.notify_income_missing, "
            "updated_at = now(), updated_by = excluded.updated_by"
        ),
        {
            "household_id": str(household_id),
            "safety_buffer_minor": payload.safety_buffer_minor,
            "variable_basis": payload.variable_basis,
            "variable_lookback_months": payload.variable_lookback_months,
            "horizon_months": payload.horizon_months,
            "notify_shortfall": payload.notify_shortfall,
            "notify_below_buffer": payload.notify_below_buffer,
            "notify_income_missing": payload.notify_income_missing,
            "updated_by": str(updated_by) if updated_by is not None else None,
        },
    )
    return await get_forecast_settings(session, household_id)


# ---------------------------------------------------------------------------
# Anwendungsfaelle
# ---------------------------------------------------------------------------


async def build_forecast(
    session: AsyncSession,
    household_id: UUID,
    *,
    as_of: date,
    horizon_months: int | None = None,
    granularity: ForecastGranularity = "day",
    include_savings: bool = False,
) -> ForecastResponse:
    data = await load_forecast_input(
        session,
        household_id,
        as_of=as_of,
        horizon_months=horizon_months,
        include_savings=include_savings,
    )
    return calculate_forecast(data, granularity=granularity)


async def build_upcoming(
    session: AsyncSession,
    household_id: UUID,
    *,
    as_of: date,
    days: int = _DEFAULT_UPCOMING_DAYS,
) -> list[ForecastDayGroup]:
    if not 1 <= days <= 365:
        raise ValueError("days muss zwischen 1 und 365 liegen.")
    # Der Horizont muss die angefragten Tage tragen; aufgerundet auf volle Monate.
    horizon = max(1, min(_MAX_HORIZON_MONTHS, days // 30 + 1))
    data = await load_forecast_input(
        session, household_id, as_of=as_of, horizon_months=horizon, daily_days=days
    )
    result = calculate_forecast(data, granularity="day")
    return group_upcoming(result, until=as_of + timedelta(days=days))


async def simulate_forecast(
    session: AsyncSession,
    household_id: UUID,
    *,
    as_of: date,
    payload: ForecastSimulationRequest,
) -> ForecastSimulationResponse:
    """Ungespeicherte Simulation aus einem Request-Body."""
    return await run_simulation(
        session,
        household_id,
        as_of=as_of,
        horizon_months=payload.horizon_months,
        include_savings=payload.include_savings,
        changes=tuple(payload.changes),
    )


async def run_simulation(
    session: AsyncSession,
    household_id: UUID,
    *,
    as_of: date,
    horizon_months: int | None,
    include_savings: bool,
    changes: tuple[ForecastChange, ...],
) -> ForecastSimulationResponse:
    """Der Rechenlauf selbst - unabhaengig davon, WOHER die Aenderungen kommen.

    `simulate_forecast` reicht einen Request-Body herein, der Szenariodienst
    eine gespeicherte Zeilenliste. Beide muessen denselben Weg nehmen
    (Leitsatz 2), duerfen sich aber nicht ueber `ForecastSimulationRequest`
    treffen: dessen erneute Pruefung wuerde gespeicherte Zeilen ablehnen, die
    die Tabelle ausdruecklich erlaubt (etwa `amount_minor = 0` aus der
    Uebernahme der Fixkostenszenarien).
    """
    base_input = await load_forecast_input(
        session,
        household_id,
        as_of=as_of,
        horizon_months=horizon_months,
        include_savings=include_savings,
    )

    missing = {
        change.target_contract_id
        for change in changes
        if change.target_contract_id is not None
    } - {term.contract_id for term in base_input.contract_terms}
    if missing:
        raise ValueError("Mindestens ein Zielvertrag existiert nicht oder zaehlt nicht mit.")

    base_result = calculate_forecast(base_input, granularity="day")

    simulation_input = apply_changes(base_input, changes)
    if tax_class_hint_applies(base_input, changes):
        simulation_input = simulation_input.model_copy(
            update={
                "context": simulation_input.context.model_copy(
                    update={
                        "extra_warnings": (
                            *simulation_input.context.extra_warnings,
                            ForecastWarning(
                                code="simulation_tax_class_hint", level="info", context={}
                            ),
                        )
                    }
                )
            }
        )
    simulation_result = calculate_forecast(simulation_input, granularity="day")

    contributions = tuple(
        contribution_of(base_input, base_result, change, index)
        for index, change in enumerate(changes)
    )
    return ForecastSimulationResponse(
        base=base_result,
        simulation=simulation_result,
        comparison=compare_forecasts(base_result, simulation_result),
        contributions=contributions,
    )
