"""Service-Logik fuer Budgetplanung, Plan/Ist-Berechnung, 50/30/20 und Jahresmatrix."""

import calendar
from datetime import UTC, date, datetime
from typing import Final, Literal, NamedTuple
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.budgets import (
    AnnualMatrixMonthData,
    AnnualMatrixResponse,
    AnnualMatrixRow,
    BudgetAllocationInput,
    BudgetSummary,
    CategoryBudgetStatus,
    CategoryGroupBudgetStatus,
    CloseMonthResponse,
    ContractForecastSource,
    FixedCostReserveItem,
)
from app.services import category_service, contract_service, reserve_service
from app.services.fixed_cost_reserve_service import (
    RESERVE_FREQUENCIES,
    fetch_fixed_cost_reserves,
)

# Ein gekuendigter Vertrag kostet bis zu seinem Enddatum weiter Geld und zaehlt
# deshalb mit; pausierte, beendete und verneinte nicht. Bewusst dieselbe Menge
# wie contract_service._COUNTING_STATUSES - zwei Listen waeren zwei Wahrheiten.
_FIXED_COSTS_STATUSES: Final = ("active", "cancelled")


async def get_or_create_budget(
    session: AsyncSession, household_id: UUID, year: int, month: int
) -> tuple[UUID, Literal["open", "closed"], datetime | None]:
    res = await session.execute(
        text(
            "select id, status, closed_at from budgets "
            "where household_id = :household_id and year = :year and month = :month"
        ),
        {"household_id": str(household_id), "year": year, "month": month},
    )
    row = res.first()
    if row:
        return row.id, row.status, row.closed_at

    ins_res = await session.execute(
        text(
            "insert into budgets (household_id, year, month, method, status) "
            "values (:household_id, :year, :month, 'tracking', 'open') "
            "returning id, status, closed_at"
        ),
        {"household_id": str(household_id), "year": year, "month": month},
    )
    new_row = ins_res.one()
    return new_row.id, new_row.status, new_row.closed_at


class FixedCosts(NamedTuple):
    expense_monthly_minor: int
    income_monthly_minor: int
    contract_count: int


async def booked_income_minor(
    session: AsyncSession, household_id: UUID, start_date: date, end_date: date
) -> int:
    """Gebuchte Einnahmen des Zeitraums - Bezugsgroesse jeder Quote.

    Oeffentlich, weil ausser dem Budget auch das Fixkostenziel dieselbe
    Bezugsgroesse braucht: zwei Abfragen ergaeben zwei Quoten unter einem Namen.

    Der LEFT JOIN ist notwendig, damit unkategorisierte Splits nicht komplett
    herausfallen.
    """
    result = await session.execute(
        text(
            "select coalesce(sum(s.amount_minor), 0) as inc_minor "
            "from transaction_splits s "
            "join transactions t on t.id = s.transaction_id "
            "join accounts a on a.id = t.account_id "
            "left join categories c on c.id = s.category_id "
            "where t.household_id = :household_id "
            "and a.on_budget = true "
            "and not t.is_archived "
            "and not t.is_transfer "
            "and t.booked_date >= :start_date and t.booked_date <= :end_date "
            "and (c.default_type = 'income' "
            "or (s.category_id is null and s.amount_minor > 0))"
        ),
        {
            "household_id": str(household_id),
            "start_date": start_date,
            "end_date": end_date,
        },
    )
    return int(result.scalar() or 0)


async def fixed_costs_snapshot(
    session: AsyncSession, household_id: UUID, month_start: date, month_end: date
) -> FixedCosts:
    """Auf einen Monat normierte Vertragsbetraege fuer den BUDGETMONAT.

    Bezugszeitraum ist der ganze Monat und nicht `today`: sonst zeigte der
    Januarbericht im Juni die heutige Vertragslage. Bewusst NICHT ueber
    contract_service.build_summary geloest - das laedt alle Vertraege als
    Pydantic-Objekte und rechnet fest auf `today`.

    Ein Vertrag zaehlt, wenn er IRGENDWANN in diesem Monat lief. Der Anker ist
    deshalb der Monatserste fuer `ends_on` und der Monatsletzte fuer
    `starts_on`, nicht beides der Monatsletzte: ein zum 9. gekuendigter Vertrag
    wird in diesem Monat noch einmal abgebucht und kostet ihn voll. Mit dem
    Monatsletzten als Anker waere er schon am Monatsersten aus Kennzahl,
    verfuegbarem Rest und Fixkostenquote verschwunden - der Plan
    (docs/VERTRAEGE-FIXKOSTEN-PLAN.md, Abschnitt 12) verlangt ausdruecklich das
    Gegenteil, und GET /v1/reports/fixed-costs-trend rechnet ebenso.

    Genauigkeit fuer die Vergangenheit: ohne gepflegtes `starts_on` gilt ein
    Vertrag als schon immer laufend. `first_seen_date` waere kein besserer
    Anker, es haengt am Importzeitraum. Die exakte Zeitreihe liefert
    GET /v1/reports/fixed-costs-trend aus contract_amount_history.
    """
    result = await session.execute(
        text(
            "select amount_minor, frequency, direction from contracts "
            "where household_id = :household_id "
            "and status = any(cast(:statuses as text[])) "
            "and merged_into_id is null "
            "and (starts_on is null or starts_on <= :month_end) "
            "and (ends_on is null or ends_on >= :month_start)"
        ),
        {
            "household_id": str(household_id),
            "statuses": list(_FIXED_COSTS_STATUSES),
            "month_start": month_start,
            "month_end": month_end,
        },
    )

    expense_monthly = 0
    income_monthly = 0
    expense_count = 0
    for row in result:
        # Je Vertrag normieren und erst dann summieren - genau wie
        # contract_service.build_summary. Wuerde hier die Summe je Frequenz
        # normiert, wichen Vertragsseite und Budgetseite um Rundungscent ab.
        value = contract_service.monthly_minor(int(row.amount_minor), row.frequency)
        if row.direction == "expense":
            expense_monthly += value
            expense_count += 1
        else:
            income_monthly += value

    return FixedCosts(expense_monthly, income_monthly, expense_count)


async def find_budget(
    session: AsyncSession, household_id: UUID, year: int, month: int
) -> tuple[UUID | None, Literal["open", "closed"], datetime | None]:
    """Wie get_or_create_budget, legt aber nichts an: ohne Zeile (None,
    'open', None) - der Monat hat dann weder Planbetraege noch Uebertraege."""
    row = (
        await session.execute(
            text(
                "select id, status, closed_at from budgets "
                "where household_id = :household_id and year = :year and month = :month"
            ),
            {"household_id": str(household_id), "year": year, "month": month},
        )
    ).first()
    if row is None:
        return None, "open", None
    return row.id, row.status, row.closed_at


async def get_budget_summary(
    session: AsyncSession,
    household_id: UUID,
    year: int,
    month: int,
    *,
    create_missing: bool = True,
) -> BudgetSummary:
    """Budgetuebersicht eines Monats.

    create_missing=False liest nur: fehlt die budgets-Zeile des Monats, wird
    sie NICHT angelegt (KI-Feld, nur lesend - E5; dort laeuft die Abfrage in
    einer READ-ONLY-Transaktion)."""
    budget_id: UUID | None
    if create_missing:
        budget_id, status, closed_at = await get_or_create_budget(
            session, household_id, year, month
        )
    else:
        budget_id, status, closed_at = await find_budget(session, household_id, year, month)
    budget_param = str(budget_id) if budget_id is not None else None

    start_date = date(year, month, 1)
    last_day = calendar.monthrange(year, month)[1]
    end_date = date(year, month, last_day)

    # 1. Alle Kategoriegruppen und Kategorien holen
    groups_res = await session.execute(
        text(
            "select id, name, sort_order from category_groups "
            "where household_id = :household_id order by sort_order asc, name asc"
        ),
        {"household_id": str(household_id)},
    )
    groups = groups_res.all()

    # Grundmenge sind die BUDGETIERTEN aktiven Kategorien VEREINIGT mit allen
    # uebrigen, die in genau diesem Budget noch einen Planbetrag oder Uebertrag
    # tragen (Kategorisierungsplan, "Historie, Budgets und Berichte"). Wuerde
    # hier nur `not c.is_archived` stehen, verschwaende eine Archivierung
    # rueckwirkend geplante Betraege, Uebertraege und gebuchte Ist-Ausgaben aus
    # den Summen - die Monatssumme aendert sich dann durch eine reine
    # Stammdatenpflege.
    #
    # Zwei Ausschluesse haelt die Abfrage dagegen konsequent durch:
    #
    # 1. `default_type = 'transfer'` kommt nie ins Budget. Eine interne
    #    Umbuchung, ein Kreditkartenausgleich oder eine Depoteinzahlung ist
    #    keine Ausgabe, sondern Geld, das den Haushalt nur wechselt. Der
    #    Ausschluss haengt bewusst NICHT an `is_budgeted`, damit auch eine von
    #    Hand angelegte Transferkategorie nicht als Verbrauch gezaehlt wird.
    # 2. `is_budgeted = false` blendet die Kategorie aus, solange sie in diesem
    #    Monat weder Planbetrag noch Uebertrag hat. Das ist der manuelle
    #    Schalter aus der Kategorienverwaltung ("Im Budget anzeigen"); das
    #    Standardpaket setzt ihn fuer alle Einnahmekategorien auf false. Ein
    #    einmal gesetzter Planbetrag haelt die Zeile weiter sichtbar, sonst
    #    liesse sie sich nach dem Umschalten nicht mehr auf 0 zuruecksetzen.
    #
    # Beides veraendert Ist-Summe und Sparquote, und zwar gewollt: was nicht
    # budgetiert wird, ist auch kein Verbrauch.
    cats_res = await session.execute(
        text(
            "select c.id, c.group_id, c.name, c.budget_bucket, c.default_type, "
            "c.sort_order, c.is_archived, coalesce(ba.planned_minor, 0) as planned_minor, "
            "ba.rollover_rule "
            "from categories c "
            "left join budget_allocations ba "
            "on ba.category_id = c.id and ba.budget_id = :budget_id "
            "where c.household_id = :household_id "
            "and c.default_type is distinct from 'transfer' "
            "and ((c.is_budgeted and not c.is_archived) "
            "     or ba.category_id is not null "
            "     or exists (select 1 from budget_rollovers br "
            "                where br.budget_id = :budget_id and br.category_id = c.id) "
            "     or (c.is_budgeted and exists "
            "         (select 1 from transaction_splits s "
            "          join transactions t on t.id = s.transaction_id "
            "          join accounts a on a.id = t.account_id "
            "          where s.category_id = c.id "
            "          and t.household_id = :household_id "
            "          and a.on_budget = true "
            "          and not t.is_archived and not t.is_transfer "
            "          and t.booked_date >= :start_date "
            "          and t.booked_date <= :end_date))) "
            "order by c.sort_order asc, c.name asc"
        ),
        {
            "household_id": str(household_id),
            "budget_id": budget_param,
            "start_date": start_date,
            "end_date": end_date,
        },
    )
    categories = cats_res.all()

    # 2. Ist-Ausgaben & Einnahmen pro Kategorie im Monat aus On-Budget-Konten summieren
    actuals_res = await session.execute(
        text(
            "select s.category_id, sum(s.amount_minor) as total_minor "
            "from transaction_splits s "
            "join transactions t on t.id = s.transaction_id "
            "join accounts a on a.id = t.account_id "
            "where t.household_id = :household_id "
            "and a.on_budget = true "
            "and not t.is_archived "
            "and not t.is_transfer "
            "and t.booked_date >= :start_date and t.booked_date <= :end_date "
            "and s.category_id is not null "
            "group by s.category_id"
        ),
        {
            "household_id": str(household_id),
            "start_date": start_date,
            "end_date": end_date,
        },
    )
    # sum() ueber bigint liefert in Postgres numeric - hier konsequent nach int
    # zurueckholen, sonst rechnen Sparquote und Prozentwerte spaeter Decimal
    # gegen float und brechen mit TypeError ab.
    actuals_map = {row.category_id: int(row.total_minor) for row in actuals_res}

    # 2b. Unkategorisierte negative Splits (category_id is null) auf
    # On-Budget-Konten - typischerweise eine frisch importierte Buchung, die
    # noch keiner Kategorie zugewiesen wurde. Sie gehoert zu keiner Kategorie
    # und damit auch zu keiner Gruppe und keinem 50/30/20-Bucket, darf den
    # Budgetrest aber nicht kuenstlich erhoehen (docs/MOBILE-APP-REDESIGN-PLAN.md,
    # Abschnitt 2 und 6). Sie zaehlt deshalb NUR additiv auf Gesamtebene in
    # total_spent_minor - derselbe Split zaehlt im Cashflow-/Uebersichtsbericht
    # ueber cashflow_semantics.CASHFLOW_EXPENSE_PREDICATE konsistent mit.
    uncategorized_res = await session.execute(
        text(
            "select coalesce(-sum(s.amount_minor), 0) as uncategorized_spent_minor "
            "from transaction_splits s "
            "join transactions t on t.id = s.transaction_id "
            "join accounts a on a.id = t.account_id "
            "where t.household_id = :household_id "
            "and a.on_budget = true "
            "and not t.is_archived "
            "and not t.is_transfer "
            "and t.booked_date >= :start_date and t.booked_date <= :end_date "
            "and s.category_id is null "
            "and s.amount_minor < 0"
        ),
        {
            "household_id": str(household_id),
            "start_date": start_date,
            "end_date": end_date,
        },
    )
    uncategorized_spent_minor = int(uncategorized_res.scalar() or 0)

    # 3. Rollover-Betraege holen
    rollovers_res = await session.execute(
        text(
            "select category_id, sum(amount_minor) as roll_minor "
            "from budget_rollovers where budget_id = :budget_id "
            "group by category_id"
        ),
        {"budget_id": budget_param},
    )
    rollover_map = {row.category_id: int(row.roll_minor) for row in rollovers_res}

    # 3b. Ist-Ausgaben & Einnahmen des VORMONATS - dieselbe Abfrage wie oben,
    # nur um einen Monat verschoben. Traegt die Vergleichsspalte im
    # Monatsbudget ("was war letzten Monat"), die beim Setzen eines neuen
    # Planbetrags Vergleichbarkeit schafft.
    if month == 1:
        prev_year, prev_month = year - 1, 12
    else:
        prev_year, prev_month = year, month - 1
    prev_start_date = date(prev_year, prev_month, 1)
    prev_last_day = calendar.monthrange(prev_year, prev_month)[1]
    prev_end_date = date(prev_year, prev_month, prev_last_day)

    prev_actuals_res = await session.execute(
        text(
            "select s.category_id, sum(s.amount_minor) as total_minor "
            "from transaction_splits s "
            "join transactions t on t.id = s.transaction_id "
            "join accounts a on a.id = t.account_id "
            "where t.household_id = :household_id "
            "and a.on_budget = true "
            "and not t.is_archived "
            "and not t.is_transfer "
            "and t.booked_date >= :start_date and t.booked_date <= :end_date "
            "and s.category_id is not null "
            "group by s.category_id"
        ),
        {
            "household_id": str(household_id),
            "start_date": prev_start_date,
            "end_date": prev_end_date,
        },
    )
    prev_actuals_map = {row.category_id: int(row.total_minor) for row in prev_actuals_res}

    # 3c. Vertrags-Forecast je Kategorie: voller erwarteter Betrag jedes
    # Vertrags mit dieser Kategorie, der im Budgetmonat faellig ist -
    # UNABHAENGIG davon, ob die Buchung schon gelandet ist. Anders als bei
    # fixed_costs_snapshot (auf einen Monat NORMIERTER Betrag ueber alle
    # Vertraege) zaehlt hier die tatsaechliche Faelligkeit im Kalendermonat,
    # z. B. die volle Jahrespraemie einer Versicherung im Faelligkeitsmonat.
    # Bewusst NICHT um bereits Gebuchtes bereinigt: Forecast und Ist sind zwei
    # unabhaengige Vergleichszahlen wie Plan und Ist auch - wenn beide
    # uebereinstimmen, bestaetigt das nur, dass alles wie erwartet lief, und
    # eine Abweichung (Preisaenderung, ausstehende Zahlung) ist die
    # eigentlich interessante Information.
    forecast_contracts_res = await session.execute(
        text(
            "select id, name, category_id, amount_minor, frequency, direction, due_rule, "
            "due_day, next_due_date, starts_on, ends_on from contracts "
            "where household_id = :household_id "
            "and status = any(cast(:statuses as text[])) "
            "and merged_into_id is null "
            "and category_id is not null "
            "and (starts_on is null or starts_on <= :end_date) "
            "and (ends_on is null or ends_on >= :start_date)"
        ),
        {
            "household_id": str(household_id),
            "statuses": list(_FIXED_COSTS_STATUSES),
            "start_date": start_date,
            "end_date": end_date,
        },
    )
    forecast_map: dict[UUID, int] = {}
    # Je Faelligkeit ein Eintrag (nicht je Vertrag) - erklaert dem Hover im
    # Frontend auch, WARUM ein woechentlicher Vertrag hier doppelt zaehlt.
    forecast_sources: dict[UUID, list[ContractForecastSource]] = {}
    for row in forecast_contracts_res:
        due_dates = contract_service.due_dates_in_window(
            next_due_date_value=row.next_due_date,
            frequency=row.frequency,
            due_rule=row.due_rule,
            due_day=row.due_day,
            starts_on=row.starts_on,
            ends_on=row.ends_on,
            window_start=start_date,
            window_end=end_date,
        )
        for due in due_dates:
            forecast_map[row.category_id] = forecast_map.get(row.category_id, 0) + row.amount_minor
            forecast_sources.setdefault(row.category_id, []).append(
                ContractForecastSource(
                    contract_id=row.id,
                    contract_name=row.name,
                    amount_minor=row.amount_minor,
                    due_date=due,
                )
            )

    # 3d. Kategorien mit IRGENDWANN einem Planbetrag, einer Buchung oder einem
    # verknuepften Vertrag - bewusst ohne Zeitraum, sonst verschwindet eine
    # nur jaehrlich faellige Kategorie (siehe contract_forecast_minor oben) in
    # den ruhigen Monaten aus dem "jemals Bewegung"-Filter, den sie eigentlich
    # retten soll. Der Vertragszweig faengt zusaetzlich das erste Jahr ab: ein
    # gerade erst angelegter Jahresvertrag, dessen Faelligkeit noch nie
    # verstrichen ist, hat weder Planbetrag noch Buchung, ist aber trotzdem
    # keine tote Kategorie.
    history_res = await session.execute(
        text(
            "select category_id from budget_allocations "
            "where household_id = :household_id and planned_minor <> 0 "
            "union "
            # Der Join auf transactions ist noetig, weil der Split der Buchung
            # SELBST gehoert und mit ihr archiviert wird - anders als eine
            # Verknuepfung, die auf sie zeigt und beim Loeschen geloest wird
            # (transaction_detach_service). Ohne ihn blieb eine Kategorie, deren
            # Buchungen alle geloescht wurden, als reine Nullzeile in der Stufe
            # "jemals aktiv" stehen: Plan, Uebertrag und Ist rechneten korrekt
            # ohne sie, nur die Sichtbarkeit sagte etwas anderes.
            "select s.category_id from transaction_splits s "
            "join transactions t on t.id = s.transaction_id "
            "where s.household_id = :household_id and s.category_id is not null "
            "and not t.is_archived "
            "union "
            "select category_id from contracts "
            "where household_id = :household_id and category_id is not null "
            "and merged_into_id is null"
        ),
        {"household_id": str(household_id)},
    )
    history_category_ids = {row.category_id for row in history_res}

    # 4. Gesamteinnahmen berechnen.
    # Einnahme ist ausschliesslich (a) alles auf einer Kategorie mit
    # default_type = 'income' - inklusive negativer Korrekturen wie
    # Gehaltsrueckforderungen - und (b) jeder positive Zufluss ohne Kategorie.
    # Ein positiver Split auf einer AUSGABEN-Kategorie ist dagegen eine
    # Erstattung: er senkt dort den Verbrauch (siehe spent unten) und ist
    # keine Einnahme, sonst wuerde derselbe Betrag doppelt wirken.
    total_income = await booked_income_minor(session, household_id, start_date, end_date)

    group_data_list: list[CategoryGroupBudgetStatus] = []
    tot_planned = 0
    tot_rollover = 0
    tot_spent = 0
    # Einnahmeseite strikt parallel, aber getrennt gefuehrt (siehe
    # schemas/budgets.py): ein Einnahmeplan darf nie in tot_planned landen,
    # sonst wird er ueber tot_remaining zu freiem Budget.
    tot_planned_income = 0
    tot_rollover_income = 0
    tot_income_actual = 0

    bucket_needs_spent = 0
    bucket_wants_spent = 0
    bucket_savings_spent = 0
    bucket_unassigned_spent = 0
    bucket_needs_planned = 0
    bucket_wants_planned = 0
    bucket_savings_planned = 0
    bucket_unassigned_planned = 0

    for g in groups:
        cat_items: list[CategoryBudgetStatus] = []
        g_planned = 0
        g_rollover = 0
        g_spent = 0
        g_income_actual = 0
        g_planned_income = 0
        g_rollover_income = 0

        g_cats = [c for c in categories if c.group_id == g.id]
        # Gruppen, aus denen die Filter oben alles herausgenommen haben
        # (typisch: "Transfers", oder "Einnahmen" ohne Planbetrag), tauchen gar
        # nicht erst auf. Eine Gruppenzeile mit lauter Nullen ist keine
        # Information, sondern nur eine weitere Zeile zum Ueberlesen.
        if not g_cats:
            continue

        for c in g_cats:
            raw_act = actuals_map.get(c.id, 0)
            roll = rollover_map.get(c.id, 0)
            planned = c.planned_minor
            # Gleiche Seite wie der jeweilige Istwert unten (income_actual
            # bzw. spent) - nur aus dem Vormonat statt dem Budgetmonat.
            prev_raw_act = prev_actuals_map.get(c.id, 0)
            prev_month_actual = prev_raw_act if c.default_type == "income" else -prev_raw_act

            if c.default_type == "income":
                # Einnahmekategorien haben keinen Verbrauch. Ihr (positiver)
                # Ist-Betrag wird strikt getrennt gefuehrt und fliesst weder in
                # spent noch in die 50/30/20-Buckets ein - sonst zaehlte das
                # Gehalt als Ausgabe und die Sparquote wuerde negativ.
                spent = 0
                income_actual = raw_act
                # "Rest" heisst hier: noch ausstehende Einnahme gegenueber Plan.
                remaining = planned + roll - income_actual
                pct = round((income_actual / planned * 100.0), 1) if planned > 0 else 0.0
                g_income_actual += income_actual
                # Eigene Summen, damit die Gruppenzeile "Einnahmen" den
                # eingetragenen Plan zeigen kann, ohne dass er in
                # g_planned/g_remaining als Budgettopf mitlaeuft.
                g_planned_income += planned
                g_rollover_income += roll
            else:
                # Ausgaben sind negativ gespeichert; spent ist der positive
                # Nettoverbrauch. Eine Erstattung (positiver Split auf einer
                # Ausgabenkategorie) senkt ihn und kann ihn negativ machen.
                spent = -raw_act
                income_actual = 0
                remaining = planned + roll - spent
                pct = round((spent / planned * 100.0), 1) if planned > 0 else 0.0

                # Plan und Ist wandern in denselben Topf, sonst waeren Ziel
                # und Ist einer 50/30/20-Zeile aus verschiedenen Mengen
                # gebildet. Der else-Zweig faengt bewusst nicht nur NULL,
                # sondern jeden unbekannten Wert ab - nur so kann die
                # Invariante "die vier Toepfe ergeben die Gesamtsumme" nicht
                # durch einen neuen Bucket-Namen still kaputtgehen.
                if c.budget_bucket == "needs":
                    bucket_needs_spent += spent
                    bucket_needs_planned += planned
                elif c.budget_bucket == "wants":
                    bucket_wants_spent += spent
                    bucket_wants_planned += planned
                elif c.budget_bucket == "savings":
                    bucket_savings_spent += spent
                    bucket_savings_planned += planned
                else:
                    bucket_unassigned_spent += spent
                    bucket_unassigned_planned += planned

                g_planned += planned
                g_rollover += roll
                g_spent += spent

            cat_items.append(
                CategoryBudgetStatus(
                    category_id=c.id,
                    category_name=c.name,
                    group_id=g.id,
                    group_name=g.name,
                    budget_bucket=c.budget_bucket,
                    default_type=c.default_type,
                    rollover_rule=c.rollover_rule or "none",
                    is_archived=c.is_archived,
                    planned_minor=planned,
                    rollover_minor=roll,
                    spent_minor=spent,
                    income_actual_minor=income_actual,
                    remaining_minor=remaining,
                    percent_used=pct,
                    prev_month_actual_minor=prev_month_actual,
                    contract_forecast_minor=forecast_map.get(c.id, 0),
                    contract_forecast_sources=forecast_sources.get(c.id, []),
                    has_activity_ever=c.id in history_category_ids,
                )
            )

        # Der Uebertrag gehoert zum verfuegbaren Budget und muss deshalb auch
        # auf Gruppen- und Gesamtebene in den Rest einfliessen.
        g_remaining = g_planned + g_rollover - g_spent
        # Spiegelbild fuer die Einnahmeseite: "Rest" heisst hier noch
        # ausstehende Einnahme, nicht uebriges Budget.
        g_income_remaining = g_planned_income + g_rollover_income - g_income_actual
        tot_planned += g_planned
        tot_rollover += g_rollover
        tot_spent += g_spent
        tot_planned_income += g_planned_income
        tot_rollover_income += g_rollover_income
        tot_income_actual += g_income_actual

        group_data_list.append(
            CategoryGroupBudgetStatus(
                group_id=g.id,
                group_name=g.name,
                sort_order=g.sort_order,
                total_planned_minor=g_planned,
                total_rollover_minor=g_rollover,
                total_spent_minor=g_spent,
                total_remaining_minor=g_remaining,
                total_income_actual_minor=g_income_actual,
                total_planned_income_minor=g_planned_income,
                total_rollover_income_minor=g_rollover_income,
                total_income_remaining_minor=g_income_remaining,
                categories=cat_items,
            )
        )

    # Unkategorisiertes kommt hier und NUR hier hinzu: es ist additiv auf
    # Gesamtebene (Invariante total_spent = sum(group.total_spent) +
    # uncategorized_spent), bleibt aber aus jeder Gruppen- und Bucket-Summe
    # heraus - keine der Zeilen oben hat davon etwas gesehen.
    tot_spent += uncategorized_spent_minor
    tot_remaining = tot_planned + tot_rollover - tot_spent
    # Gegenstueck fuer die Einnahmeseite. Gegengerechnet wird tot_income_actual
    # (nur Einnahmekategorien) und NICHT total_income, das zusaetzlich
    # unkategorisierte Zufluesse enthaelt - die gehoeren zu keinem Plan und
    # wuerden hier eine ausstehende Gehaltszahlung als eingegangen ausweisen.
    # So bleibt ausserdem summary == sum(groups) exakt erfuellt.
    tot_income_remaining = tot_planned_income + tot_rollover_income - tot_income_actual
    savings_rate = (
        round(((total_income - tot_spent) / total_income * 100.0), 1) if total_income > 0 else 0.0
    )

    # Anteile der drei Buckets an den GEBUCHTEN Ausgaben (nicht am Einkommen) -
    # sie summieren sich daher immer auf 100 %.
    tot_buckets = bucket_needs_spent + bucket_wants_spent + bucket_savings_spent
    needs_pct = round((bucket_needs_spent / tot_buckets * 100.0), 1) if tot_buckets > 0 else 0.0
    wants_pct = round((bucket_wants_spent / tot_buckets * 100.0), 1) if tot_buckets > 0 else 0.0
    savings_pct = round((bucket_savings_spent / tot_buckets * 100.0), 1) if tot_buckets > 0 else 0.0

    # Fixkosten aus Vertraegen - eine reine ZUSATZangabe. Sie fliesst in keine
    # der Zahlen oben ein, und das ist keine Nachlaessigkeit, sondern die
    # Trennlinie des Budgetkerns: Plan/Ist, Sparquote und 50/30/20 rechnen
    # ausschliesslich mit GEBUCHTEN Betraegen. Ein Vertrag ist eine Erwartung;
    # sobald seine Rate abgebucht ist, steckt sie bereits in total_spent_minor.
    # Wer beides addiert, zaehlt dasselbe Geld doppelt - deshalb bitte auch
    # spaeter nicht "vereinheitlichen".
    fixed = await fixed_costs_snapshot(session, household_id, start_date, end_date)
    # Kalkulatorische Spartoepfe sind eine dritte, rein additive Ebene. Als
    # Basis dient der echte Monatsueberschuss (Ist-Einnahmen minus Ist-Ausgaben),
    # nicht total_remaining_minor, das einen Kategorieplan gegen Ist misst.
    # Keine bestehende Summe wird hier veraendert oder doppelt gezaehlt.
    reserve_overview = await reserve_service.get_reserve_overview(
        session,
        household_id,
        year=year,
        month=month,
    )
    goal_reserve_monthly = reserve_overview.goal_reserve_recommended_minor
    goal_reserve_count = sum(
        1
        for pot in reserve_overview.pots
        if pot.kind == "goal" and pot.is_included_in_planning and pot.recommended_minor > 0
    )
    fixed_reserves = await fetch_fixed_cost_reserves(
        session, household_id, budget_month=start_date
    )
    fixed_reserve_monthly = reserve_overview.fixed_reserve_recommended_minor
    # Die Anzahl muss dieselbe Menge beschreiben wie der Betrag daneben, also
    # die Ruecklagentoepfe. len(fixed_reserves) waere die theoretische
    # Modellrechnung, die zusaetzlich starts_on und reserve_started_on gegen den
    # Budgetmonat prueft: ein Vertrag mit einem spaeteren Ruecklagenstart faellt
    # dort heraus, liefert im Topf aber sehr wohl eine Empfehlung - die
    # Oberflaeche zeigte dann "0 geglaettete Kosten" neben einem Abzug groesser
    # als null.
    fixed_reserve_count = sum(1 for pot in reserve_overview.pots if pot.kind == "contract")

    # The forward plan uses only genuinely recurring weekly/monthly expense
    # contracts here. Enabled non-monthly contracts live exclusively in the
    # reserve layer above; opt-out means they remain visible in legacy fixed
    # cost/forecast statistics but are intentionally not smoothed into this
    # capacity verdict.
    regular_res = await session.execute(
        text(
            "select amount_minor, frequency "
            "from contracts where household_id = :household_id "
            "and merged_into_id is null and direction = 'expense' "
            "and frequency in ('weekly', 'monthly') "
            "and status = any(cast(:statuses as text[])) "
            "and (starts_on is null or starts_on <= :month_end) "
            "and (ends_on is null or ends_on >= :month_start)"
        ),
        {
            "household_id": str(household_id),
            "statuses": list(_FIXED_COSTS_STATUSES),
            "month_start": start_date,
            "month_end": end_date,
        },
    )
    regular_fixed = sum(
        contract_service.monthly_minor(int(row.amount_minor), row.frequency)
        for row in regular_res
    )

    counted_categories_res = await session.execute(
        text(
            "select distinct category_id from contracts "
            "where household_id = :household_id and merged_into_id is null "
            "and category_id is not null and direction = 'expense' "
            "and status = any(cast(:statuses as text[])) "
            "and (starts_on is null or starts_on <= :month_end) "
            "and (ends_on is null or ends_on >= :month_start) "
            "and (frequency in ('weekly', 'monthly') or "
            "(reserve_enabled = true and frequency = any(cast(:reserve_frequencies as text[]))))"
        ),
        {
            "household_id": str(household_id),
            "statuses": list(_FIXED_COSTS_STATUSES),
            "reserve_frequencies": list(RESERVE_FREQUENCIES),
            "month_start": start_date,
            "month_end": end_date,
        },
    )
    counted_category_ids = {row.category_id for row in counted_categories_res}

    # Conservative reconciliation rule: if an expense category is linked to
    # any counted contract, its entire category allocation is not subtracted a
    # second time. Likewise a generic savings bucket is skipped once concrete
    # goal reserves exist. The excluded sums make this policy transparent.
    variable_planned = 0
    excluded_contract_plans = 0
    excluded_goal_savings = 0
    for category in categories:
        if category.default_type == "income":
            continue
        planned_value = int(category.planned_minor)
        if category.id in counted_category_ids:
            excluded_contract_plans += planned_value
        elif category.budget_bucket == "savings" and goal_reserve_count > 0:
            excluded_goal_savings += planned_value
        else:
            variable_planned += planned_value

    planning_income = fixed.income_monthly_minor if fixed.income_monthly_minor > 0 else total_income
    planning_income_source: Literal["actual", "contract"] = (
        "contract" if fixed.income_monthly_minor > 0 else "actual"
    )
    total_calculational_reserves = fixed_reserve_monthly + goal_reserve_monthly
    planning_before_goals = (
        planning_income - regular_fixed - fixed_reserve_monthly - variable_planned
    )
    planning_truly_free = planning_before_goals - goal_reserve_monthly

    # Bezugsgroesse der Quote sind die Ist-Einnahmen - auch dann, wenn sie
    # negativ sind (ein Erstattungsueberschuss ist trotzdem ein Ist-Wert und
    # kein "es wurde noch nichts gebucht"). Nur wenn im Monat ueberhaupt keine
    # Bewegung stattfand (total_income exakt 0, typisch am Monatsanfang oder in
    # einem Zukunftsmonat), tritt der Planwert aus den Einnahmevertraegen an
    # ihre Stelle - eine Quote von 0 % waere sonst jeden Monatsersten die
    # falsche Entwarnung. Welcher Weg genommen wurde, steht in
    # fixed_costs_income_basis, damit die Oberflaeche es kennzeichnen kann.
    if total_income != 0:
        income_basis = total_income
        basis_source: Literal["actual", "contract"] = "actual"
    else:
        income_basis = fixed.income_monthly_minor
        basis_source = "contract"
    # Die Quote ist nur fuer eine POSITIVE Bezugsgroesse sinnvoll - eine
    # negative oder fehlende Basis liefert keinen Prozentwert (bewusst nicht
    # geaendert, siehe Aufgabenbeschreibung).
    fixed_ratio = (
        round((fixed.expense_monthly_minor / income_basis * 100.0), 1)
        if income_basis > 0
        else 0.0
    )

    return BudgetSummary(
        budget_id=budget_id,
        year=year,
        month=month,
        status=status,
        method="tracking",
        closed_at=closed_at,
        total_income_minor=total_income,
        total_planned_minor=tot_planned,
        total_rollover_minor=tot_rollover,
        total_spent_minor=tot_spent,
        total_remaining_minor=tot_remaining,
        total_planned_income_minor=tot_planned_income,
        total_rollover_income_minor=tot_rollover_income,
        total_income_remaining_minor=tot_income_remaining,
        uncategorized_spent_minor=uncategorized_spent_minor,
        bucket_classified_spent_minor=tot_buckets,
        savings_rate_percent=savings_rate,
        bucket_needs_minor=bucket_needs_spent,
        bucket_wants_minor=bucket_wants_spent,
        bucket_savings_minor=bucket_savings_spent,
        bucket_unassigned_spent_minor=bucket_unassigned_spent,
        bucket_needs_planned_minor=bucket_needs_planned,
        bucket_wants_planned_minor=bucket_wants_planned,
        bucket_savings_planned_minor=bucket_savings_planned,
        bucket_unassigned_planned_minor=bucket_unassigned_planned,
        bucket_needs_percent=needs_pct,
        bucket_wants_percent=wants_pct,
        bucket_savings_percent=savings_pct,
        surplus_before_goal_reserves_minor=planning_before_goals,
        goal_reserves_monthly_minor=goal_reserve_monthly,
        free_after_goal_reserves_minor=planning_truly_free,
        goal_reserve_count=goal_reserve_count,
        planning_expected_income_minor=planning_income,
        planning_income_basis=planning_income_source,
        regular_fixed_costs_monthly_minor=regular_fixed,
        fixed_cost_reserves_monthly_minor=fixed_reserve_monthly,
        fixed_cost_reserve_count=fixed_reserve_count,
        fixed_cost_reserve_items=[FixedCostReserveItem(**item.__dict__) for item in fixed_reserves],
        variable_planned_budget_minor=variable_planned,
        variable_planned_excluded_contract_minor=excluded_contract_plans,
        variable_planned_excluded_goal_savings_minor=excluded_goal_savings,
        total_calculational_reserves_minor=total_calculational_reserves,
        planning_truly_free_minor=planning_truly_free,
        fixed_costs_monthly_minor=fixed.expense_monthly_minor,
        fixed_costs_income_monthly_minor=fixed.income_monthly_minor,
        income_basis_minor=income_basis,
        available_after_fixed_minor=income_basis - fixed.expense_monthly_minor,
        fixed_costs_income_basis=basis_source,
        fixed_costs_ratio_percent=fixed_ratio,
        fixed_costs_contract_count=fixed.contract_count,
        groups=group_data_list,
    )


async def update_budget_allocations(
    session: AsyncSession,
    household_id: UUID,
    year: int,
    month: int,
    allocations: list[BudgetAllocationInput],
) -> None:
    budget_id, status, _ = await get_or_create_budget(session, household_id, year, month)
    if status == "closed":
        raise ValueError("Ein abgeschlossener Monat kann nicht mehr bearbeitet werden.")

    for alloc in allocations:
        # Jede Kategorie-ID laeuft durch den zentralen Resolver (Haushalt,
        # Aktivstatus, Merge-Kanonisierung). Ausnahme: eine bereits vorhandene
        # Budgetzeile darf weiter gepflegt werden, auch wenn ihre Kategorie
        # inzwischen archiviert wurde - sonst waere ein abgeschlossener bzw.
        # historischer Monat nicht mehr korrigierbar. Neue Zeilen verlangen
        # dagegen eine aktive Kategorie.
        existing = await session.execute(
            text(
                "select 1 from budget_allocations "
                "where budget_id = :budget_id and category_id = :category_id "
                "and household_id = :household_id"
            ),
            {
                "budget_id": str(budget_id),
                "category_id": str(alloc.category_id),
                "household_id": str(household_id),
            },
        )
        if existing.first() is None:
            cat_id = await category_service.resolve_category_id(
                session, household_id, alloc.category_id, allow_none=False
            )
            # Eine Transferkategorie wird von get_budget_summary grundsaetzlich
            # nicht ausgewiesen. Ein Planbetrag darauf waere unsichtbar und
            # spaeter weder auffindbar noch korrigierbar - deshalb hier
            # ablehnen statt still wegschreiben.
            type_res = await session.execute(
                text("select default_type from categories where id = :category_id"),
                {"category_id": str(cat_id)},
            )
            if type_res.scalar() == "transfer":
                raise ValueError(
                    "Transferkategorien führen kein Budget: "
                    "eine Umbuchung ist keine Ausgabe."
                )
        else:
            cat_id = alloc.category_id

        planned = alloc.planned_minor
        roll_rule = alloc.rollover_rule
        note = alloc.note

        await session.execute(
            text(
                "insert into budget_allocations "
                "(household_id, budget_id, category_id, planned_minor, rollover_rule, note) "
                "values "
                "(:household_id, :budget_id, :category_id, :planned, :roll_rule, :note) "
                "on conflict (budget_id, category_id) do update set "
                "planned_minor = excluded.planned_minor, "
                "rollover_rule = excluded.rollover_rule, "
                "note = excluded.note, "
                "updated_at = now()"
            ),
            {
                "household_id": str(household_id),
                "budget_id": str(budget_id),
                "category_id": str(cat_id),
                "planned": planned,
                "roll_rule": roll_rule,
                "note": note,
            },
        )


async def close_month(
    session: AsyncSession, household_id: UUID, year: int, month: int, user_id: UUID
) -> CloseMonthResponse:
    budget_id, status, _ = await get_or_create_budget(session, household_id, year, month)
    if status == "closed":
        raise ValueError("Der Monat ist bereits abgeschlossen.")

    now_utc = datetime.now(UTC)
    await session.execute(
        text(
            "update budgets set status = 'closed', closed_at = :closed_at, closed_by = :user_id "
            "where id = :budget_id"
        ),
        {"closed_at": now_utc, "user_id": str(user_id), "budget_id": str(budget_id)},
    )

    summary = await get_budget_summary(session, household_id, year, month)

    next_year = year + 1 if month == 12 else year
    next_month = 1 if month == 12 else month + 1
    next_budget_id, _, _ = await get_or_create_budget(session, household_id, next_year, next_month)

    created_rollovers = 0
    for grp in summary.groups:
        for cat in grp.categories:
            # Nur Ausgabenkategorien fuehren einen Budgettopf, der uebertragen
            # werden kann. Bei Einnahmekategorien bedeutet remaining_minor
            # "noch ausstehende Einnahme" - das ist kein Budgetrest.
            if cat.default_type == "income":
                continue
            # Eine archivierte Kategorie behaelt ihre historischen Uebertraege,
            # bekommt aber keinen neuen Monat mehr (Kategorisierungsplan:
            # "Neue Monate uebernehmen die archivierte Kategorie nicht"). Der
            # Trigger reject_archived_category_reference wuerde den Insert
            # ohnehin ablehnen - hier wird er gar nicht erst versucht.
            if cat.is_archived:
                continue
            if cat.rollover_rule == "accumulate" and cat.remaining_minor != 0:
                await session.execute(
                    text(
                        "insert into budget_rollovers "
                        "(household_id, budget_id, category_id, amount_minor, reason) "
                        "values (:household_id, :budget_id, :category_id, :amount, :reason)"
                    ),
                    {
                        "household_id": str(household_id),
                        "budget_id": str(next_budget_id),
                        "category_id": str(cat.category_id),
                        "amount": cat.remaining_minor,
                        "reason": f"Uebertrag aus {year:04d}-{month:02d}",
                    },
                )
                created_rollovers += 1

    return CloseMonthResponse(
        budget_id=budget_id,
        status="closed",
        closed_at=now_utc,
        rollovers_created=created_rollovers,
    )


async def get_annual_matrix(
    session: AsyncSession, household_id: UUID, year: int
) -> AnnualMatrixResponse:
    # Wie in get_budget_summary, nur jahresbezogen: budgetierte aktive
    # Kategorien vereinigt mit allen, die im Berichtsjahr noch geplant oder
    # uebertragen wurden. Sonst fiele eine im Dezember archivierte Kategorie
    # rueckwirkend aus der Jahresmatrix heraus und die Jahressumme waere
    # kleiner als die Summe der zwoelf Monatsabschluesse. Transferkategorien
    # und nicht budgetierte Kategorien bleiben nach derselben Regel draussen -
    # die Matrix muss zum Monatsbudget passen, sonst erklaert sie dessen
    # Summen nicht mehr.
    cats_res = await session.execute(
        text(
            "select c.id, c.name as category_name, cg.id as group_id, "
            "cg.name as group_name, cg.sort_order as group_sort_order, "
            "c.sort_order as category_sort_order, "
            "c.budget_bucket, c.default_type, c.is_archived "
            "from categories c "
            "join category_groups cg on cg.id = c.group_id "
            "where c.household_id = :household_id "
            "and c.default_type is distinct from 'transfer' "
            "and ((c.is_budgeted and not c.is_archived) "
            "     or exists (select 1 from budget_allocations ba "
            "                join budgets b on b.id = ba.budget_id "
            "                where ba.category_id = c.id and b.year = :year) "
            "     or exists (select 1 from budget_rollovers br "
            "                join budgets b on b.id = br.budget_id "
            "                where br.category_id = c.id and b.year = :year) "
            "     or (c.is_budgeted and exists "
            "         (select 1 from transaction_splits s "
            "          join transactions t on t.id = s.transaction_id "
            "          where s.category_id = c.id "
            "          and t.household_id = :household_id "
            "          and not t.is_archived "
            "          and extract(year from t.booked_date) = :year))) "
            "order by cg.sort_order asc, c.sort_order asc, c.name asc"
        ),
        {"household_id": str(household_id), "year": year},
    )
    categories = cats_res.all()

    # Ist-Werte und Planwerte des ganzen Jahres in je EINER Abfrage, statt
    # zwoelfmal get_budget_summary aufzurufen (das seinerseits mehrere
    # Aggregatabfragen absetzt und pro Monat eine budgets-Zeile anlegt). Die
    # Berichteseite laedt zwei Jahre und loeste damit rund zwei Dutzend
    # Monatsauswertungen aus.
    #
    # Nebeneffekt, der bewusst entfaellt: der Lesepfad legt keine
    # budgets-Zeilen mehr an. Fuer das Ergebnis macht das keinen Unterschied -
    # ohne Budget gibt es keine Zuweisungen und damit Planwert 0; die
    # Budgetseite legt ihren Monat weiterhin selbst an.
    actuals_res = await session.execute(
        text(
            "select s.category_id, extract(month from t.booked_date)::int as month_no, "
            "sum(s.amount_minor) as total_minor "
            "from transaction_splits s "
            "join transactions t on t.id = s.transaction_id "
            "join accounts a on a.id = t.account_id "
            "where t.household_id = :household_id "
            "and a.on_budget = true "
            "and not t.is_archived "
            "and not t.is_transfer "
            "and extract(year from t.booked_date) = :year "
            "and s.category_id is not null "
            "group by s.category_id, extract(month from t.booked_date)"
        ),
        {"household_id": str(household_id), "year": year},
    )
    # sum() ueber bigint liefert numeric - wie in get_budget_summary sofort
    # nach int zurueckholen, sonst rechnen spaetere Prozentwerte Decimal gegen
    # float.
    actuals_by_month: dict[tuple[str, int], int] = {
        (str(row.category_id), int(row.month_no)): int(row.total_minor)
        for row in actuals_res
    }

    planned_res = await session.execute(
        text(
            "select ba.category_id, b.month as month_no, ba.planned_minor "
            "from budget_allocations ba "
            "join budgets b on b.id = ba.budget_id "
            "where b.household_id = :household_id and b.year = :year"
        ),
        {"household_id": str(household_id), "year": year},
    )
    planned_by_month: dict[tuple[str, int], int] = {
        (str(row.category_id), int(row.month_no)): int(row.planned_minor)
        for row in planned_res
    }

    matrix_rows: list[AnnualMatrixRow] = []
    for c in categories:
        months_data: list[AnnualMatrixMonthData] = []
        tot_plan = 0
        tot_spent = 0
        tot_income_actual = 0

        is_income_category = c.default_type == "income"
        for m in range(1, 13):
            raw_actual = actuals_by_month.get((str(c.id), m), 0)
            planned = planned_by_month.get((str(c.id), m), 0)

            # Dieselbe Trennung wie in get_budget_summary: eine
            # Einnahmekategorie hat keinen Verbrauch, sonst zaehlte das Gehalt
            # als Ausgabe. Ausgaben liegen negativ vor; spent ist der positive
            # Nettoverbrauch, den eine Erstattung senken (und negativ machen)
            # kann.
            spent = 0 if is_income_category else -raw_actual
            income_actual = raw_actual if is_income_category else 0

            tot_plan += planned
            tot_spent += spent
            tot_income_actual += income_actual

            months_data.append(
                AnnualMatrixMonthData(
                    month=m,
                    planned_minor=planned,
                    spent_minor=spent,
                    income_actual_minor=income_actual,
                )
            )

        matrix_rows.append(
            AnnualMatrixRow(
                category_id=c.id,
                category_name=c.category_name,
                group_id=c.group_id,
                group_name=c.group_name,
                # Dieselben Ordnungszahlen, nach denen die Abfrage oben schon
                # sortiert - die Oberflaeche kann daraus Gruppenbloecke bilden,
                # ohne die Kategorienverwaltung ein zweites Mal zu laden.
                group_sort_order=c.group_sort_order,
                category_sort_order=c.category_sort_order,
                budget_bucket=c.budget_bucket,
                default_type=c.default_type,
                is_archived=c.is_archived,
                months=months_data,
                total_planned_minor=tot_plan,
                total_spent_minor=tot_spent,
                total_income_actual_minor=tot_income_actual,
            )
        )

    return AnnualMatrixResponse(year=year, rows=matrix_rows)
