"""API-Endpunkte fuer Auswertungen und Dashboard (Phase 5)."""

import calendar
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Final
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_household_session
from app.services import contract_service, household_forecast_service
from app.services.budget_service import get_budget_summary
from app.services.cashflow_semantics import (
    CASHFLOW_EXPENSE_PREDICATE,
    CASHFLOW_INCOME_PREDICATE,
)
from app.services.rules_service import get_review_inbox

router = APIRouter(prefix="/reports", tags=["reports"])

HouseholdSession = Annotated[tuple[AsyncSession, UUID], Depends(get_household_session)]

# Fenster der Faelligkeitsvorschau auf der Uebersicht. Eine Woche ist der
# Horizont, in dem eine Abbuchung noch disponierbar ist.
_UPCOMING_DUE_DAYS: Final = 7

# Vorlauf des Fixkostentrends: fuer den Vorjahresvergleich (I10) braucht auch
# der aelteste ausgewiesene Monat seinen Vorjahresmonat. Zwoelf zusaetzliche
# Monate fallen dadurch aus derselben Abfrage ab und kosten keine zweite.
_FIXED_COSTS_YOY_LOOKBACK_MONTHS: Final = 12

# Beschriftung der Monatsreihe im Bericht "variable Kosten". Bewusst eine
# feste deutsche Tabelle statt `strftime('%b')`: dessen Ausgabe haengt an der
# Locale des Serverprozesses und waere auf einem englischen Container "Aug",
# auf einem deutschen "Aug." und auf einem ungesetzten schlicht unvorhersagbar.
_MONTH_ABBR: Final = (
    "Jan",
    "Feb",
    "Mär",
    "Apr",
    "Mai",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Okt",
    "Nov",
    "Dez",
)

# Anzeigename einer Buchung ohne Kategorie. Diese Zeile wird ausdruecklich
# nicht unterschlagen: unkategorisierte Abgaenge sind variable Kosten
# (cashflow_semantics.CASHFLOW_EXPENSE_PREDICATE zaehlt sie mit), und sie
# auszublenden hiesse, den Monatsschnitt kleiner zu zeigen, als er ist.
_UNCATEGORIZED_LABEL: Final = "(ohne Kategorie)"


class CashflowMonthData(BaseModel):
    year: int
    month: int
    income_minor: int
    expense_minor: int
    net_minor: int


class CategoryReportData(BaseModel):
    category_id: UUID
    category_name: str
    amount_minor: int
    percentage: float


class OverviewReportResponse(BaseModel):
    current_year: int
    current_month: int
    total_income_minor: int
    total_expense_minor: int
    net_savings_minor: int
    savings_rate_percent: float
    budget_remaining_minor: int
    open_review_count: int
    # Fixkosten aus Vertraegen. Erwartungswerte, keine gebuchten Betraege -
    # sie sind deshalb weder in total_expense_minor noch in
    # net_savings_minor enthalten (siehe schemas/budgets.BudgetSummary).
    fixed_costs_monthly_minor: int
    available_after_fixed_minor: int
    fixed_costs_ratio_percent: float
    # Vertragsfaelligkeiten der naechsten sieben Tage, inklusive
    # Einnahmevertraege - die Kachel beantwortet "was passiert diese Woche
    # auf dem Konto", nicht "was muss ich zahlen".
    upcoming_due_count: int
    top_expense_categories: list[CategoryReportData]
    cashflow_last_6_months: list[CashflowMonthData]


class CashflowTrendResponse(BaseModel):
    months: list[CashflowMonthData]


class FixedCostsTypeData(BaseModel):
    contract_type: str
    amount_minor: int


class FixedCostsMonthData(BaseModel):
    year: int
    month: int
    fixed_costs_minor: int
    contract_count: int
    # Veraenderung zum selben Monat des Vorjahres in Prozent; None, wenn es
    # damals keine Fixkosten gab (eine Steigerung "um unendlich" ist keine
    # Aussage).
    yoy_percent: float | None
    by_type: list[FixedCostsTypeData]


class FixedCostsTrendResponse(BaseModel):
    months: list[FixedCostsMonthData]


class VariableCostCategory(BaseModel):
    # None = Buchungen ohne Kategorie. Ein echter Wert, kein Fehlerfall.
    category_id: UUID | None
    category_name: str
    group_name: str | None
    monthly_average_minor: int
    share_percent: float
    transaction_count: int
    # Ein Wert je Monat aus `series`, gleiche Reihenfolge - damit die
    # Trendspalte ohne zweite Abfrage und ohne eigene Zeitachse auskommt.
    trend: list[int]


class VariableCostMonth(BaseModel):
    year: int
    month: int
    label: str
    variable_minor: int
    fixed_minor: int


class VariableCostsResponse(BaseModel):
    generated_on: date
    months: int
    monthly_average_minor: int
    monthly_fixed_average_minor: int
    categories: list[VariableCostCategory]
    series: list[VariableCostMonth]


def _round_minor(value: Decimal) -> int:
    """Kaufmaennisch runden - dieselbe Regel wie im Rechenkern der Vorschau
    (household_forecast_service._round_minor). Ohne sie waeren die beiden
    Monatsdurchschnitte um Rundungscent verschieden, und der Nutzer saehe zwei
    Zahlen fuer dieselbe Aussage."""
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _month_range(end_year: int, end_month: int, months: int) -> list[tuple[int, int]]:
    """Die `months` Monate bis einschliesslich `end_year`-`end_month`,
    chronologisch aufsteigend."""
    result: list[tuple[int, int]] = []
    year, month = end_year, end_month
    for _ in range(months):
        result.append((year, month))
        month -= 1
        if month < 1:
            month = 12
            year -= 1
    result.reverse()
    return result


async def _compute_cashflow_months(
    db: AsyncSession, household_id: UUID, end_year: int, end_month: int, months: int
) -> list[CashflowMonthData]:
    """Berechnet Einnahmen/Ausgaben/Netto fuer die `months` Monate bis
    einschliesslich `end_year`-`end_month`, chronologisch aufsteigend.

    Eine einzige gruppierte Abfrage statt `months` Aufrufe von
    get_budget_summary (die jeweils mehrere Aggregatabfragen absetzen und
    nebenbei eine budgets-Zeile anlegen). Die Rechenregeln sind identisch und
    absichtlich hier verdoppelt statt aus get_budget_summary geholt - der
    Gleichstand ist durch einen Test abgesichert
    (tests/test_analytics.py::test_cashflow_matches_budget_summary):

      Einnahme  = Splits auf Kategorien mit default_type='income'
                  PLUS positive Zufluesse ganz ohne Kategorie.
                  Ein positiver Split auf einer AUSGABEN-Kategorie ist eine
                  Erstattung, keine Einnahme - er senkt dort den Verbrauch.
      Ausgabe   = negierte Summe der kategorisierten Splits auf
                  Nicht-Einnahmekategorien, die auch im Budget als Verbrauch
                  zaehlen.

    Der zweite Halbsatz traegt die beiden Ausschluesse, die get_budget_summary
    ueber seine Kategorieauswahl vornimmt und die hier sonst fehlten:

      * `default_type = 'transfer'` ist nie Verbrauch. Eine Depoteinzahlung
        oder ein Kreditkartenausgleich ist Geld, das den Haushalt nur wechselt.
      * `is_budgeted = false` blendet die Kategorie aus, solange sie in genau
        diesem Monat weder Planbetrag noch Uebertrag hat.

    Ohne sie zeigten Uebersichts- und Budgetseite fuer denselben Monat zwei
    verschiedene Ausgabensummen.
    """
    months_to_fetch = _month_range(end_year, end_month, months)

    first_year, first_month = months_to_fetch[0]
    start_date = date(first_year, first_month, 1)
    end_last_day = calendar.monthrange(end_year, end_month)[1]
    end_date = date(end_year, end_month, end_last_day)

    rows = await db.execute(
        text(
            f"""
            select extract(year from t.booked_date)::int as year_no,
                   extract(month from t.booked_date)::int as month_no,
                   coalesce(sum(s.amount_minor) filter (
                       where {CASHFLOW_INCOME_PREDICATE}
                   ), 0) as income_minor,
                   coalesce(-sum(s.amount_minor) filter (
                       where {CASHFLOW_EXPENSE_PREDICATE}
                   ), 0) as expense_minor
            from transaction_splits s
            join transactions t on t.id = s.transaction_id
            join accounts a on a.id = t.account_id
            left join categories c on c.id = s.category_id
            where t.household_id = :household_id
              and a.on_budget = true
              and not t.is_archived
              and not t.is_transfer
              and t.booked_date >= :start_date
              and t.booked_date <= :end_date
            group by 1, 2
            """
        ),
        {
            "household_id": str(household_id),
            "start_date": start_date,
            "end_date": end_date,
        },
    )
    by_month = {
        (int(r.year_no), int(r.month_no)): (int(r.income_minor), int(r.expense_minor))
        for r in rows
    }

    result: list[CashflowMonthData] = []
    for y, m in months_to_fetch:
        inc, exp = by_month.get((y, m), (0, 0))
        result.append(
            CashflowMonthData(
                year=y, month=m, income_minor=inc, expense_minor=exp, net_minor=inc - exp
            )
        )
    return result


async def _compute_fixed_costs_months(
    db: AsyncSession, household_id: UUID, end_year: int, end_month: int, months: int
) -> list[FixedCostsMonthData]:
    """Monatliche Fixkosten aus `contract_amount_history`, aufgeschluesselt
    nach Vertragsart, chronologisch aufsteigend.

    Quelle ist bewusst der Betragsverlauf und NICHT `transaction_splits`: die
    Frage lautet "was war in diesem Monat vereinbart", nicht "was wurde
    gebucht". Nur so ist "die Fixkosten sind um 8 % gestiegen, getrieben von
    Strom und Versicherungen" exakt beantwortbar statt geschaetzt - eine
    Jahrespraemie faellt gebucht in genau einem Monat an, vereinbart ist sie in
    allen zwoelf. Deshalb kann _compute_cashflow_months das nicht liefern und
    der Analytics-Explorer, der ebenfalls nur ueber Buchungen laeuft, auch
    nicht.

    Massgeblich ist die zum MONATSLETZTEN gueltige Preisstufe. Eine
    Preiserhoehung zum 15. wirkt damit ab genau diesem Monat.

    Der Statusfilter ist absichtlich weiter als der des Budgets
    (budget_service._FIXED_COSTS_STATUSES): ein inzwischen beendeter oder
    pausierter Vertrag hat in den Monaten, in denen er lief, Geld gekostet.
    Wer ihn rueckwirkend herausrechnete, wuerde die Vergangenheit nach dem
    heutigen Status umschreiben und jede Steigerung kleiner aussehen lassen,
    als sie war. Was nach `ends_on` liegt, faellt dagegen heraus - und ein
    beendeter Vertrag hat immer ein `ends_on`: sowohl der Statuswechsel
    (routes/contracts.change_status) als auch die Automatik nach ausgebliebenen
    Zahlungen (contract_matching_service.apply_outcome) tragen es nach. Ohne das
    stiege die Kurve nur noch, weil die offene Preisstufe jeden Monat traefe.
    """
    months_to_fetch = _month_range(end_year, end_month, months)
    first_year, first_month = months_to_fetch[0]

    # Vorjahresmonate mitziehen, statt sie einzeln nachzuladen.
    series_start = contract_service.add_months(
        date(first_year, first_month, 1), -_FIXED_COSTS_YOY_LOOKBACK_MONTHS
    )
    series_end = date(end_year, end_month, 1)

    rows = await db.execute(
        text(
            "with month_grid as ("
            "  select gs::date as month_start, "
            "         (gs + interval '1 month' - interval '1 day')::date as month_end "
            # cast(...) statt ::timestamp: SQLAlchemy erkennt einen
            # Platzhalter nicht mehr als solchen, wenn direkt ein ':' folgt.
            "  from generate_series(cast(:series_start as timestamp), "
            "    cast(:series_end as timestamp), interval '1 month') as gs) "
            # distinct on schuetzt gegen ueberlappende Stufen: es zaehlt die
            # zuletzt begonnene, die am Monatsletzten noch gilt.
            "select distinct on (g.month_start, h.contract_id) "
            "  g.month_start as month_start, h.contract_id as contract_id, "
            "  c.contract_type as contract_type, c.frequency as frequency, "
            "  h.amount_minor as amount_minor "
            "from month_grid g "
            "join contract_amount_history h "
            "  on h.household_id = :household_id "
            " and h.valid_from <= g.month_end "
            " and (h.valid_to is null or h.valid_to >= g.month_end) "
            "join contracts c "
            "  on c.id = h.contract_id "
            " and c.household_id = :household_id "
            " and c.direction = 'expense' "
            " and c.status <> 'not_a_contract' "
            " and (c.ends_on is null or c.ends_on >= g.month_start) "
            "order by g.month_start, h.contract_id, h.valid_from desc"
        ),
        {
            "household_id": str(household_id),
            "series_start": series_start,
            "series_end": series_end,
        },
    )

    by_month_type: dict[tuple[int, int], dict[str, int]] = {}
    counts: dict[tuple[int, int], int] = {}
    for row in rows:
        key = (row.month_start.year, row.month_start.month)
        # Je Vertrag normieren und erst dann summieren - gleiche Reihenfolge
        # wie in budget_service._fixed_costs_snapshot und
        # contract_service.build_summary, sonst weichen die Seiten um
        # Rundungscent voneinander ab.
        value = contract_service.monthly_minor(int(row.amount_minor), row.frequency)
        bucket = by_month_type.setdefault(key, {})
        bucket[row.contract_type] = bucket.get(row.contract_type, 0) + value
        counts[key] = counts.get(key, 0) + 1

    result: list[FixedCostsMonthData] = []
    for year, month in months_to_fetch:
        per_type = by_month_type.get((year, month), {})
        total = sum(per_type.values())
        previous = sum(by_month_type.get((year - 1, month), {}).values())
        result.append(
            FixedCostsMonthData(
                year=year,
                month=month,
                fixed_costs_minor=total,
                contract_count=counts.get((year, month), 0),
                yoy_percent=(
                    round(((total - previous) / previous * 100.0), 1) if previous > 0 else None
                ),
                # Groesster Posten zuerst - die Frage lautet "wovon getrieben".
                by_type=[
                    FixedCostsTypeData(contract_type=name, amount_minor=amount)
                    for name, amount in sorted(per_type.items(), key=lambda i: (-i[1], i[0]))
                ],
            )
        )
    return result


async def _compute_variable_costs(
    db: AsyncSession, household_id: UUID, today: date, months: int
) -> VariableCostsResponse:
    """Woraus bestehen die variablen Kosten - und wie gross sind sie neben den
    Fixkosten?

    Das Gegenstueck zu `_compute_fixed_costs_months`. Die Vorschau nennt eine
    einzige grosse Zahl ("variable Ausgaben ca. 4.000 EUR / Monat"); hier steht,
    welche Kategorien sie ausmachen. Fixkosten sind kaum zu bewegen, variable
    Kosten schon - aber nur, wenn ihre Bestandteile sichtbar sind.

    Drei Entscheidungen, die die Zahlen tragen:

    1. Die Abgrenzung "variabel" ist NICHT hier definiert, sondern von der
       Vorschau uebernommen: `CASHFLOW_EXPENSE_PREDICATE` (dieselbe
       Ausgabendefinition wie Budget und Cashflow) ohne die Kategorien aus
       `household_forecast_service.covered_category_ids`. Was ein Vertrag
       deckt, ist Fixkosten und darf hier nicht ein zweites Mal auftauchen.
       Eine eigene Kopie dieser Regel wuerde frueher oder spaeter driften -
       und dann behaupteten Vorschau und Bericht verschiedene Zahlen.
    2. Betrachtet werden nur VOLLE Monate vor dem laufenden. Der laufende Monat
       ist erst halb gebucht; er zoege den Durchschnitt nach unten und liesse
       jede Sparanstrengung erfolgreicher aussehen, als sie ist.
    3. Die Fixkostenreihe kommt aus `_compute_fixed_costs_months`, also aus
       `contract_amount_history` - VEREINBARTE Betraege gegen GEBUCHTE variable
       Ausgaben. Beides aus Buchungen zu ziehen waere hier falsch: eine
       Jahrespraemie faellt gebucht in genau einem Monat an und wuerde diesen
       einen Monat als Fixkostenspitze ausweisen, obwohl sie in allen zwoelf
       gilt.
    """
    # Der letzte volle Monat ist der Vormonat des laufenden.
    last_full = date(today.year, today.month, 1) - timedelta(days=1)
    months_to_fetch = _month_range(last_full.year, last_full.month, months)
    first_year, first_month = months_to_fetch[0]
    start_date = date(first_year, first_month, 1)

    covered = await household_forecast_service.covered_category_ids(db, household_id)

    rows = await db.execute(
        text(
            f"""
            select extract(year from t.booked_date)::int as year_no,
                   extract(month from t.booked_date)::int as month_no,
                   s.category_id as category_id,
                   c.name as category_name,
                   g.name as group_name,
                   coalesce(-sum(s.amount_minor), 0) as spent_minor,
                   count(distinct t.id) as transaction_count
            from transaction_splits s
            join transactions t on t.id = s.transaction_id
            join accounts a on a.id = t.account_id
            left join categories c on c.id = s.category_id
            left join category_groups g on g.id = c.group_id
            where t.household_id = :household_id
              and a.on_budget = true
              and not t.is_archived
              and not t.is_transfer
              and t.booked_date >= :start_date
              and t.booked_date <= :end_date
              and {CASHFLOW_EXPENSE_PREDICATE}
              and (s.category_id is null
                   or not (s.category_id = any(cast(:covered as uuid[]))))
              and {household_forecast_service.CONTRACT_BOUND_EXPENSE_SQL}
            group by 1, 2, 3, 4, 5
            """
        ),
        {
            "household_id": str(household_id),
            "start_date": start_date,
            "end_date": last_full,
            "covered": covered,
            "statuses": list(contract_service._COUNTING_STATUSES),
        },
    )

    # Schluessel ist die Kategorie-UUID als Text; None steht fuer "ohne
    # Kategorie" und ist eine eigene Zeile.
    labels: dict[str | None, tuple[str, str | None]] = {}
    totals: dict[str | None, int] = {}
    counts: dict[str | None, int] = {}
    trends: dict[str | None, dict[tuple[int, int], int]] = {}
    month_totals: dict[tuple[int, int], int] = {}
    for row in rows:
        key = str(row.category_id) if row.category_id is not None else None
        labels.setdefault(
            key,
            (
                str(row.category_name) if row.category_name is not None else _UNCATEGORIZED_LABEL,
                str(row.group_name) if row.group_name is not None else None,
            ),
        )
        month_key = (int(row.year_no), int(row.month_no))
        amount = int(row.spent_minor)
        totals[key] = totals.get(key, 0) + amount
        # Eine Buchung mit zwei Splits derselben Kategorie zaehlt einmal
        # (count distinct je Monat); ueber Monate hinweg ist die Summe korrekt,
        # weil eine Buchung genau ein booked_date hat.
        counts[key] = counts.get(key, 0) + int(row.transaction_count)
        bucket = trends.setdefault(key, {})
        bucket[month_key] = bucket.get(month_key, 0) + amount
        month_totals[month_key] = month_totals.get(month_key, 0) + amount

    fixed_months = await _compute_fixed_costs_months(
        db, household_id, last_full.year, last_full.month, months
    )
    fixed_by_month = {(item.year, item.month): item.fixed_costs_minor for item in fixed_months}

    series = [
        VariableCostMonth(
            year=year,
            month=month,
            label=f"{_MONTH_ABBR[month - 1]} {year}",
            variable_minor=month_totals.get((year, month), 0),
            fixed_minor=fixed_by_month.get((year, month), 0),
        )
        for year, month in months_to_fetch
    ]

    # Kategoriedurchschnitte ueber eine laufende Summe statt je Kategorie
    # einzeln gerundet: dadurch ergeben die Kategorien in der Summe GENAU
    # `monthly_average_minor`. Einzeln gerundet fehlten oder entstuenden ein
    # paar Cent, und die Tabelle widerspraeche ihrer eigenen Kopfzeile.
    ordered = sorted(totals.items(), key=lambda item: (-item[1], labels[item[0]][0]))
    running_total = 0
    running_average = 0
    averages: dict[str | None, int] = {}
    for key, total in ordered:
        running_total += total
        next_average = _round_minor(Decimal(running_total) / Decimal(months))
        averages[key] = next_average - running_average
        running_average = next_average
    monthly_average = running_average

    categories = [
        VariableCostCategory(
            category_id=UUID(key) if key is not None else None,
            category_name=labels[key][0],
            group_name=labels[key][1],
            monthly_average_minor=averages[key],
            share_percent=(
                round(averages[key] / monthly_average * 100.0, 1) if monthly_average > 0 else 0.0
            ),
            transaction_count=counts.get(key, 0),
            trend=[trends.get(key, {}).get(month_key, 0) for month_key in months_to_fetch],
        )
        for key, _total in ordered
    ]
    categories.sort(key=lambda item: (-item.monthly_average_minor, item.category_name))

    return VariableCostsResponse(
        generated_on=today,
        months=months,
        monthly_average_minor=monthly_average,
        monthly_fixed_average_minor=_round_minor(
            Decimal(sum(item.fixed_minor for item in series)) / Decimal(months)
        ),
        categories=categories,
        series=series,
    )


@router.get("/overview", response_model=OverviewReportResponse)
async def fetch_overview_report(
    session_ctx: HouseholdSession,
) -> OverviewReportResponse:
    db, household_id = session_ctx
    today = date.today()
    cur_year = today.year
    cur_month = today.month

    summary = await get_budget_summary(db, household_id, cur_year, cur_month)

    cat_items = []
    tot_spent = summary.total_spent_minor
    for grp in summary.groups:
        for c in grp.categories:
            if c.spent_minor > 0:
                pct = round((c.spent_minor / tot_spent * 100.0), 1) if tot_spent > 0 else 0.0
                cat_items.append(
                    CategoryReportData(
                        category_id=c.category_id,
                        category_name=c.category_name,
                        amount_minor=c.spent_minor,
                        percentage=pct,
                    )
                )

    cat_items.sort(key=lambda x: x.amount_minor, reverse=True)
    top_cats = cat_items[:5]

    cashflow_6m = await _compute_cashflow_months(db, household_id, cur_year, cur_month, 6)

    inc = summary.total_income_minor
    exp = summary.total_spent_minor
    net = inc - exp

    inbox = await get_review_inbox(db, household_id)

    # Kein zweiter Datenpfad fuer die Fixkosten: sie stehen bereits in der
    # oben geholten Zusammenfassung.
    forecast = await contract_service.build_forecast(
        db, household_id, today=today, until=today + timedelta(days=_UPCOMING_DUE_DAYS)
    )

    return OverviewReportResponse(
        current_year=cur_year,
        current_month=cur_month,
        total_income_minor=inc,
        total_expense_minor=exp,
        net_savings_minor=net,
        savings_rate_percent=summary.savings_rate_percent,
        budget_remaining_minor=summary.total_remaining_minor,
        open_review_count=inbox.total_count,
        fixed_costs_monthly_minor=summary.fixed_costs_monthly_minor,
        available_after_fixed_minor=summary.available_after_fixed_minor,
        fixed_costs_ratio_percent=summary.fixed_costs_ratio_percent,
        upcoming_due_count=len(forecast),
        top_expense_categories=top_cats,
        cashflow_last_6_months=cashflow_6m,
    )


@router.get("/cashflow-trend", response_model=CashflowTrendResponse)
async def fetch_cashflow_trend(
    session_ctx: HouseholdSession,
    months: Annotated[int, Query(ge=1, le=36)] = 24,
) -> CashflowTrendResponse:
    db, household_id = session_ctx
    today = date.today()
    trend = await _compute_cashflow_months(db, household_id, today.year, today.month, months)
    return CashflowTrendResponse(months=trend)


@router.get("/fixed-costs-trend", response_model=FixedCostsTrendResponse)
async def fetch_fixed_costs_trend(
    session_ctx: HouseholdSession,
    months: Annotated[int, Query(ge=1, le=36)] = 24,
) -> FixedCostsTrendResponse:
    db, household_id = session_ctx
    today = date.today()
    trend = await _compute_fixed_costs_months(db, household_id, today.year, today.month, months)
    return FixedCostsTrendResponse(months=trend)


@router.get("/variable-costs", response_model=VariableCostsResponse)
async def fetch_variable_costs(
    session_ctx: HouseholdSession,
    months: Annotated[int, Query(ge=1, le=36)] = 12,
) -> VariableCostsResponse:
    db, household_id = session_ctx
    return await _compute_variable_costs(db, household_id, date.today(), months)
