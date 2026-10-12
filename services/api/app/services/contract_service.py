"""Fachlogik der Vertragsverwaltung (docs/VERTRAEGE-FIXKOSTEN-PLAN.md, Phase 1).

Der Router baut die SET-Fragmente fuer PATCH selbst - gleiches Muster wie
`routes/categories.py` und `routes/rules.py`. Alles, was mehr als ein
Statement braucht (Zusammenfassung, Betragsverlauf, Zusammenfuehren), liegt hier.

Zur Nebenlaeufigkeit: die Transaktion umschliesst den gesamten Request
(`auth/dependencies.get_household_session`), deshalb committet hier nichts.
"""

import calendar
import json
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Final, cast
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.contract import (
    Contract,
    ContractAmountEntry,
    ContractAmountEvidenceResponse,
    ContractAmountRecalculationResult,
    ContractAmountStageEvidence,
    ContractAmountStagePlan,
    ContractAssignmentPreview,
    ContractAssignmentSource,
    ContractCashflowPeriod,
    ContractCashflowResponse,
    ContractCategorySummary,
    ContractDuplicateCandidate,
    ContractDuplicateCheck,
    ContractEnding,
    ContractEvent,
    ContractEvidenceTransaction,
    ContractFreeTransactionFilter,
    ContractFreeTransactionsResponse,
    ContractIncomeKind,
    ContractIncomeKindBucket,
    ContractIncomePersonBucket,
    ContractIncomeSummary,
    ContractIncomeTrendPoint,
    ContractKeySource,
    ContractMergePreview,
    ContractMergeUndoPreview,
    ContractOccurrence,
    ContractProviderSuggestion,
    ContractReassignPreview,
    ContractSettings,
    ContractSettingsUpdate,
    ContractSummary,
    ContractTermDeadline,
    ContractTermSoon,
    ContractTypeSummary,
    ContractUpcoming,
    ContractYearlyCost,
)
from app.services import rules_service
from app.services.insurance_deadline_service import (
    NoticeTerms,
    compute_insurance_deadline,
    min_term_end_on,
)

CONTRACT_ENTITY_TYPE: Final = "contract"

CONTRACT_NOT_FOUND: Final = "contract_not_found"
CONTRACT_MERGE_TARGET_IN_SOURCES: Final = "contract_merge_target_in_sources"
CONTRACT_MERGE_STALE: Final = "contract_merge_stale"
CONTRACT_STATUS_INVALID: Final = "contract_status_invalid"
CONTRACT_AMOUNT_HISTORY_CONFLICT: Final = "contract_amount_history_conflict"
CONTRACT_PROVIDER_TEMPLATE_UNKNOWN: Final = "contract_provider_template_unknown"
CONTRACT_MERGE_OPERATION_NOT_FOUND: Final = "contract_merge_operation_not_found"
CONTRACT_MERGE_UNDO_CONFLICT: Final = "contract_merge_undo_conflict"

_CONTRACT_COLUMNS: Final = (
    "id, household_id, name, provider_label, contract_type, payee_id, account_id, "
    "category_id, contract_key, contract_key_source, direction, amount_minor, frequency, "
    "due_rule, due_day, next_due_date, first_seen_date, last_seen_date, tolerance_days, "
    "tolerance_amount_minor, status, cancelled_on, ends_on, min_term_months, "
    "cancellation_period_days, starts_on, renewal_term_months, provider_template_key, "
    "installments_total, installments_paid, source, "
    "detection_confidence, missed_periods, last_matched_at, note, reserve_enabled, "
    "reserve_started_on, reserve_starting_amount_minor, income_kind, person_id, "
    "version, created_at"
)

# Vollstaendiger Satz fuer das Audit-Log und die Merge-Snapshots - dort sollen
# auch die Felder stehen, die die API nicht ausliefert. merged_into_id/
# merged_at gehoeren dazu, damit ein wiederhergestellter Quellvertrag beim
# Undo automatisch wieder "un-merged" ist (Abschnitt 19.7).
_SNAPSHOT_COLUMNS: Final = (
    _CONTRACT_COLUMNS + ", legacy_recurring_rule_id, legacy_subscription_id, "
    "merged_into_id, merged_at"
)

# Spalten, die _restore_contract_snapshot beim Undo zurueckschreibt - alles
# ausser id/household_id (Schluessel) und version/created_at (vom Trigger
# bzw. der Datenbank verwaltet; die Wiederherstellung selbst zaehlt version
# ganz regulaer weiter hoch, das ist eine eigene, sichtbare Aenderung).
_RESTORABLE_CONTRACT_COLUMNS: Final = tuple(
    column.strip()
    for column in _SNAPSHOT_COLUMNS.split(",")
    if column.strip() not in ("id", "household_id", "version", "created_at")
)

# Perioden je Jahr. Die Monatsnormierung rechnet ueber das Jahr statt ueber
# Faktoren wie 52/12, damit Woche und Jahr denselben Rundungsweg nehmen.
_PERIODS_PER_YEAR: Final[dict[str, float]] = {
    "weekly": 52.0,
    "monthly": 12.0,
    "quarterly": 4.0,
    "semiannual": 2.0,
    "yearly": 1.0,
}

_MONTHS_PER_PERIOD: Final[dict[str, int]] = {
    "monthly": 1,
    "quarterly": 3,
    "semiannual": 6,
    "yearly": 12,
}

# Statuswechsel, die fachlich sinnvoll sind. Alles andere lehnt die API ab,
# statt einen widerspruechlichen Zustand zu speichern.
_ALLOWED_STATUS_TRANSITIONS: Final[dict[str, frozenset[str]]] = {
    "active": frozenset({"paused", "cancelled", "ended", "not_a_contract"}),
    "paused": frozenset({"active", "cancelled", "ended", "not_a_contract"}),
    "cancelled": frozenset({"active", "paused", "ended"}),
    "ended": frozenset({"active", "paused"}),
    "not_a_contract": frozenset({"active"}),
}


# ---------------------------------------------------------------------------
# Rechenhilfen
# ---------------------------------------------------------------------------


def monthly_minor(amount_minor: int, frequency: str) -> int:
    """Auf einen Monat normierter Betrag."""
    return round(amount_minor * _PERIODS_PER_YEAR[frequency] / 12.0)


def yearly_minor(amount_minor: int, frequency: str) -> int:
    return round(amount_minor * _PERIODS_PER_YEAR[frequency])


def add_months(anchor: date, months: int, *, day: int | None = None) -> date:
    """Monatsarithmetik mit der Regel `min(Zieltag, Monatsletzter)`.

    Einzige Stelle dieser Rechnung: Faelligkeit und Kuendigungsfrist muessen
    denselben Weg nehmen, sonst weichen sie am 29., 30. und 31. auseinander
    (Referenzfall 31.01. -> 28.02.).
    """
    total = anchor.month - 1 + months
    year = anchor.year + total // 12
    month = total % 12 + 1
    last_day = calendar.monthrange(year, month)[1]
    target_day = day if day is not None else anchor.day
    return date(year, month, min(target_day, last_day))


def next_due_date(
    current: date,
    frequency: str,
    *,
    due_rule: str = "day_of_month",
    due_day: int | None = None,
) -> date:
    """Naechste Faelligkeit.

    Der Zieltag kommt aus `due_day`, nicht aus dem Vormonat - sonst wandert eine
    Faelligkeit am 31. ueber den Februar dauerhaft auf den 28. Bei `month_end`
    bleibt sie am jeweiligen Monatsletzten.
    """
    if frequency == "weekly":
        return current + timedelta(days=7)

    months = _MONTHS_PER_PERIOD[frequency]
    if due_rule == "month_end":
        # 31 laeuft durch min(...) immer auf den Monatsletzten.
        return add_months(current, months, day=31)
    return add_months(current, months, day=due_day)


def previous_due_date(
    current: date,
    frequency: str,
    *,
    due_rule: str = "day_of_month",
    due_day: int | None = None,
) -> date:
    """Vorherige Faelligkeit - der Rueckwaertsgang zu `next_due_date`.

    Gebraucht von der Kalender-Cashflowansicht: `contracts.next_due_date` steht
    nach einem Abgleich bereits im naechsten Monat, die schon abgebuchte Rate
    des laufenden Monats liegt davor. Ohne diesen Schritt fehlte sie im
    laufenden Kalendermonat und dessen Summe waere kleiner als die
    Fixkostensumme, die zwei Zeilen darueber steht.

    `add_months` traegt negative Monate: Pythons Floor-Division rechnet den
    Jahresuebertrag korrekt zurueck.
    """
    if frequency == "weekly":
        return current - timedelta(days=7)

    months = _MONTHS_PER_PERIOD[frequency]
    if due_rule == "month_end":
        return add_months(current, -months, day=31)
    return add_months(current, -months, day=due_day)


# ---------------------------------------------------------------------------
# Kuendigungsfrist
# ---------------------------------------------------------------------------

# Vorlaufstufen der Erinnerung. Bewusst Konstanten und keine Einstellung: zwei
# Stufen sind eine fachliche Aussage ("einmal rechtzeitig, einmal dringend"),
# kein Geschmack.
_TERM_ENDING_STAGES: Final = (7, 30)

# Deckel fuer Altvertraege, die seit vielen Verlaengerungen laufen. Zusammen mit
# dem CHECK renewal_term_months between 1 and 120 kann die Schleife weder ewig
# noch auf der Stelle laufen.
_MAX_RENEWALS: Final = 40


def compute_term_deadline(
    contract: Contract,
    today: date,
    notice: NoticeTerms | None = None,
) -> ContractTermDeadline | None:
    """Letzter Kuendigungstermin, oder None wenn es nichts zu erinnern gibt.

    Reine Rechnung ohne Datenbankzugriff, damit dieselbe Antwort in der Liste,
    auf der Detailseite und im naechtlichen Lauf herauskommt. Genau deshalb
    wird `notice` HEREINGEREICHT und nicht hier nachgeladen: ein Nachladen
    braeche die Reinheit, und ueber ganze Vertragslisten waere es ein N+1
    (Plan 8.1, docs/VERSICHERUNGEN-PLAN.md).

    `notice` traegt die BESTAETIGTEN Fristangaben einer Police
    (`insurance_deadline_service.resolve_notice_terms` gibt nur solche heraus).
    Liegen sie vor, gewinnen sie ueber `cancellation_period_days`: zwei Rechner
    ergaeben fuer denselben Vertrag zwei Kuendigungstermine - auf /fixkosten
    einen anderen als auf /versicherungen, und das ist der eine Fehler, den der
    Nutzer sofort als Widerspruch sieht.

    Ist `notice` None - der Regelfall fuer jeden Vertrag ohne Police und fuer
    jede Police mit unbestaetigten Angaben -, laeuft die Funktion Zeile fuer
    Zeile wie zuvor.
    """
    # Wer bereits gekuendigt hat, braucht keine Fristerinnerung mehr - dieselbe
    # Haltung wie beim Abgleich, wo eine Nutzerentscheidung die Automatik
    # aussticht. Steht bewusst VOR der Vorschaltung: ein gekuendigter Vertrag
    # bekommt auch mit Police keine Erinnerung mehr.
    if contract.status not in ("active", "paused") or contract.cancelled_on is not None:
        return None

    if notice is not None:
        return _term_deadline_from_policy(contract, today, notice)

    notice_days = contract.cancellation_period_days

    if contract.min_term_months is None:
        if notice_days is None:
            return None
        # Unbefristet: es gibt keine Frist, die verstreichen kann, nur einen
        # Vorlauf. Deshalb Anzeige ohne Ereignis.
        return ContractTermDeadline(
            kind="open_ended",
            deadline_on=today,
            term_ends_on=today + timedelta(days=notice_days),
            days_left=0,
            renewal_index=0,
        )

    term_start = contract.starts_on or contract.first_seen_date or contract.created_at.date()

    # Ein am 01.01. begonnener Zwoelfmonatsvertrag endet am 31.12., nicht am
    # 01.01. des Folgejahres.
    term_end = add_months(term_start, contract.min_term_months) - timedelta(days=1)
    deadline = term_end - timedelta(days=notice_days or 0)

    renewal_index = 0
    while deadline < today:
        if contract.renewal_term_months is None or renewal_index >= _MAX_RENEWALS:
            # Ohne automatische Verlaengerung ist die Frist verstrichen; der
            # Vertrag laeuft bis term_end aus und endet dann von selbst.
            return None
        term_end = add_months(term_end, contract.renewal_term_months)
        deadline = term_end - timedelta(days=notice_days or 0)
        renewal_index += 1

    # Ein eingetragenes Enddatum ist eine Nutzerangabe und schlaegt die Rechnung.
    if contract.ends_on is not None and contract.ends_on <= term_end:
        return None

    return ContractTermDeadline(
        kind="fixed_term",
        deadline_on=deadline,
        term_ends_on=term_end,
        days_left=(deadline - today).days,
        renewal_index=renewal_index,
    )


def _term_deadline_from_policy(
    contract: Contract, today: date, notice: NoticeTerms
) -> ContractTermDeadline | None:
    """Kuendigungstermin aus den bestaetigten Fristangaben einer Police.

    Rechnet NICHT selbst, sondern uebersetzt: gerechnet wird im
    `insurance_deadline_service`, damit es genau einen Fristenrechner gibt.
    Diese Funktion beantwortet nur, wie sein dreiteiliges Ergebnis in die
    einteilige `ContractTermDeadline` passt.
    """
    # Mindestbindung. Der Beginn der Police schlaegt den des Vertrags, weil die
    # Bindung an der Police vereinbart ist. Gerechnet wird mit `min_term_end_on`
    # aus dem Fristenrechner - dieselbe Zeile liest die Versicherungsansicht,
    # und zwei Kopien der Rechnung waeren zwei Termine fuer eine Police.
    #
    # Kein Rueckfall mehr auf `first_seen_date` oder `created_at`: wann eine
    # Zeile in dieser Anwendung entstand, sagt nichts darueber, wann eine
    # gesetzliche Bindung zu laufen begann. Fehlt der Beginn, gibt es keine
    # berechenbare Bindung (Leitsatz 2).
    min_term_ends_on = min_term_end_on(
        notice.starts_on or contract.starts_on, notice.min_term_months
    )

    resolved = compute_insurance_deadline(
        anchor=notice.anchor,
        deadline_kind=notice.deadline_kind,
        today=today,
        main_due_on=notice.main_due_on,
        notice_period_months=notice.period_months,
        notice_fixed_month=notice.fixed_month,
        notice_fixed_day=notice.fixed_day,
        service_channel=notice.service_channel,
        main_due_source=notice.main_due_source,
        notice_terms_source=notice.notice_terms_source,
        recipient_state=notice.recipient_state,
        min_term_ends_on=min_term_ends_on,
    )

    # Leitsatz 1, hier als Sperre und nicht als Anzeigeregel: ein angenommenes
    # Datum bekommt nie einen Countdown. `resolve_notice_terms` laesst schon
    # nichts Unbestaetigtes durch - diese Zeile faengt ein von Hand gebautes
    # NoticeTerms ab, das sonst ueber /fixkosten einen Push ausloesen koennte.
    if resolved.certainty != "confirmed":
        return None
    if resolved.term_ends_on is None or resolved.safe_service_on is None:
        # "Nicht berechenbar" ist eine Aussage der Police, kein Anlass, auf den
        # Tagesrechner zurueckzufallen: eine Ruerup-Police mit
        # `notice_anchor = 'none'` hat keine ordentliche Frist, und der
        # umgerechnete `cancellation_period_days` erfaende hier eine.
        return None

    # Ein eingetragenes Enddatum ist eine Nutzerangabe und schlaegt die
    # Rechnung - wortgleich zur Fassung ohne Police.
    if contract.ends_on is not None and contract.ends_on <= resolved.term_ends_on:
        return None

    if notice.anchor in ("period_end", "month_end"):
        # Keine Frist, die verstreichen kann: Paragraph 168 Abs. 1 VVG erlaubt
        # die Kuendigung jederzeit zum Schluss der laufenden Periode, und
        # `month_end` rollt jeden Monat weiter. Ein Countdown darauf waere eine
        # Terminserie ohne Anlass - `open_ended` sagt genau das und erzeugt in
        # `scan_term_deadlines` kein Ereignis.
        return ContractTermDeadline(
            kind="open_ended",
            deadline_on=today,
            term_ends_on=resolved.term_ends_on,
            days_left=0,
            renewal_index=0,
        )

    # `deadline_on` ist laut seiner eigenen Zusage der letzte Tag, an dem die
    # Kuendigung BEIM ANBIETER sein muss - also `safe_service_on`, nicht das
    # rechtliche Fristende und nicht das empfohlene Handlungsdatum. Die drei
    # Daten getrennt sieht nur /versicherungen; /fixkosten bekommt das
    # vorsichtigere der beiden Zugangsdaten.
    return ContractTermDeadline(
        kind="fixed_term",
        deadline_on=resolved.safe_service_on,
        term_ends_on=resolved.term_ends_on,
        days_left=(resolved.safe_service_on - today).days,
        # Bewusst 0: die Police rechnet ueber Wiederkehrtermine, nicht ueber
        # gezaehlte Verlaengerungen. Eine Zahl hier waere geraten, und
        # `renewal_index` ist reine Beschriftung.
        renewal_index=0,
    )


def _as_contract(row: Any, *, today: date | None = None) -> Contract:
    data = dict(row._mapping)
    confidence = data.get("detection_confidence")
    data["detection_confidence"] = float(confidence) if confidence is not None else None
    contract = Contract(**data)
    calculation_date = today or date.today()
    contract.term_deadline = compute_term_deadline(contract, calculation_date)
    # Local import avoids a module cycle: the reserve service deliberately
    # reuses this module's canonical due-date arithmetic.
    from app.services.fixed_cost_reserve_service import calculate_fixed_cost_reserve

    reserve = calculate_fixed_cost_reserve(
        contract_id=contract.id,
        contract_name=contract.name,
        direction=contract.direction,
        frequency=contract.frequency,
        period_amount_minor=contract.amount_minor,
        next_due_date_value=contract.next_due_date,
        due_rule=contract.due_rule,
        due_day=contract.due_day,
        reserve_enabled=contract.reserve_enabled,
        reserve_started_on=contract.reserve_started_on,
        reserve_starting_amount_minor=contract.reserve_starting_amount_minor,
        status=contract.status,
        starts_on=contract.starts_on,
        ends_on=contract.ends_on,
        budget_month=calculation_date,
    )
    contract.reserve_monthly_minor = reserve.monthly_reserve_minor if reserve else 0
    return contract


# ---------------------------------------------------------------------------
# Lesen
# ---------------------------------------------------------------------------


async def load_contract(
    session: AsyncSession, household_id: UUID, contract_id: UUID
) -> Contract | None:
    """Liest einen Vertrag - ausser er ist bereits in einen anderen
    zusammengefuehrt worden (`merged_into_id`). Ein zusammengefuehrter Vertrag
    ist damit ueber die normale API unsichtbar und insbesondere nicht mehr
    bearbeitbar oder erneut als Merge-Ziel/-Quelle waehlbar - genau das macht
    `find_merge_by_request_id` weiterhin idempotent, ohne dass die Zeile
    physisch geloescht werden muss (Abschnitt 19.5/19.7)."""
    result = await session.execute(
        text(
            f"select {_CONTRACT_COLUMNS} from contracts "
            "where id = :id and household_id = :household_id and merged_into_id is null"
        ),
        {"id": str(contract_id), "household_id": str(household_id)},
    )
    row = result.first()
    return _as_contract(row) if row is not None else None


async def require_contract(
    session: AsyncSession, household_id: UUID, contract_id: UUID
) -> Contract:
    contract = await load_contract(session, household_id, contract_id)
    if contract is None:
        # Bewusst 404 statt 403 bei fremdem Haushalt: die Existenz einer fremden
        # Ressource soll nicht verraten werden (gleiche Linie wie category_service).
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=CONTRACT_NOT_FOUND)
    return contract


async def fetch_contract_snapshot(
    session: AsyncSession, household_id: UUID, contract_id: UUID
) -> dict[str, Any] | None:
    result = await session.execute(
        text(
            f"select {_SNAPSHOT_COLUMNS} from contracts "
            "where id = :id and household_id = :household_id"
        ),
        {"id": str(contract_id), "household_id": str(household_id)},
    )
    row = result.first()
    return dict(row._mapping) if row is not None else None


async def list_contracts(
    session: AsyncSession,
    household_id: UUID,
    *,
    statuses: list[str] | None = None,
    contract_type: str | None = None,
    direction: str | None = None,
    search: str | None = None,
) -> list[Contract]:
    # merged_into_id is null wie in load_contract: eine zusammengefuehrte
    # Quelle taucht in keiner Liste mehr auf.
    conditions = ["household_id = :household_id", "merged_into_id is null"]
    params: dict[str, Any] = {"household_id": str(household_id)}

    if statuses:
        conditions.append("status = any(cast(:statuses as text[]))")
        params["statuses"] = statuses
    if contract_type:
        conditions.append("contract_type = :contract_type")
        params["contract_type"] = contract_type
    if direction:
        conditions.append("direction = :direction")
        params["direction"] = direction
    if search:
        conditions.append(
            "(name ilike :search or provider_label ilike :search or note ilike :search)"
        )
        params["search"] = f"%{search}%"

    result = await session.execute(
        text(
            f"select {_CONTRACT_COLUMNS} from contracts where "
            + " and ".join(conditions)
            + " order by next_due_date asc, name asc"
        ),
        params,
    )
    return [_as_contract(row) for row in result]


async def list_amount_history(
    session: AsyncSession, household_id: UUID, contract_id: UUID
) -> list[ContractAmountEntry]:
    result = await session.execute(
        text(
            "select id, contract_id, amount_minor, valid_from, valid_to, delta_minor, "
            "change_reason, source_transaction_id from contract_amount_history "
            "where household_id = :household_id and contract_id = :contract_id "
            "order by valid_from asc, created_at asc"
        ),
        {"household_id": str(household_id), "contract_id": str(contract_id)},
    )
    return [ContractAmountEntry(**row._mapping) for row in result]


async def list_occurrences(
    session: AsyncSession, household_id: UUID, contract_id: UUID, *, limit: int = 120
) -> list[ContractOccurrence]:
    result = await session.execute(
        text(
            "select id, contract_id, due_date, status, transaction_id, booked_date, "
            "amount_minor, amount_delta_minor, days_delta, match_score "
            "from contract_occurrences "
            "where household_id = :household_id and contract_id = :contract_id "
            "order by due_date desc limit :limit"
        ),
        {
            "household_id": str(household_id),
            "contract_id": str(contract_id),
            "limit": limit,
        },
    )
    items: list[ContractOccurrence] = []
    for row in result:
        data = dict(row._mapping)
        score = data.get("match_score")
        data["match_score"] = float(score) if score is not None else None
        items.append(ContractOccurrence(**data))
    return items


async def list_events(
    session: AsyncSession, household_id: UUID, contract_id: UUID, *, limit: int = 60
) -> list[ContractEvent]:
    result = await session.execute(
        text(
            "select id, contract_id, kind, occurred_on, payload, "
            "acknowledged_at is not null as acknowledged from contract_events "
            "where household_id = :household_id and contract_id = :contract_id "
            "order by occurred_on desc, created_at desc limit :limit"
        ),
        {
            "household_id": str(household_id),
            "contract_id": str(contract_id),
            "limit": limit,
        },
    )
    return [ContractEvent(**row._mapping) for row in result]


# ---------------------------------------------------------------------------
# Zusammenfassung
# ---------------------------------------------------------------------------

# Gekuendigte Vertraege kosten bis zum Enddatum weiter Geld und zaehlen deshalb
# mit; pausierte, beendete und verneinte nicht.
_COUNTING_STATUSES: Final = ("active", "cancelled")

# Rangliste der teuersten Vertraege. Zehn Zeilen sind eine Seite, die man liest;
# eine vollstaendige Liste steht ohnehin unter "Vertraege".
_YEARLY_RANK_LIMIT: Final = 10

# Vorschau der Kuendigungstermine. Deutlich weiter als die 60 Tage von
# `ending_soon`: eine Frist von drei Monaten ist bei Versicherungen der
# Regelfall, und wer sie erst in 60 Tagen sieht, hat sie bereits verpasst.
_TERM_SOON_DAYS: Final = 120


async def build_summary(
    session: AsyncSession, household_id: UUID, *, today: date, upcoming_days: int = 30
) -> ContractSummary:
    contracts = await list_contracts(session, household_id)

    monthly_expense = 0
    monthly_income = 0
    yearly_expense = 0
    yearly_income = 0
    counts = {"active": 0, "paused": 0, "cancelled": 0, "ended": 0, "not_a_contract": 0}
    per_type: dict[str, dict[str, int]] = {}
    # Fuehrende Gruppierung fuer die Oberflaeche (siehe ContractCategorySummary):
    # dieselbe Zaehlschleife wie per_type, nur nach der frei vergebenen
    # Kategorie statt der festen Vertragsart. None buendelt unkategorisierte
    # Vertraege statt sie zu verwerfen - das ist genauso eine Auswertung wert.
    per_category: dict[UUID | None, dict[str, int]] = {}
    yearly_ranking: list[ContractYearlyCost] = []

    for contract in contracts:
        counts[contract.status] = counts.get(contract.status, 0) + 1
        if contract.status not in _COUNTING_STATUSES:
            continue
        if contract.ends_on is not None and contract.ends_on < today:
            continue
        # Ein Vertrag mit starts_on in der Zukunft kostet diesen Monat noch
        # nichts (docs/MOBILE-APP-REDESIGN-PLAN.md, Abschnitt 7 "Verträge,
        # Datenkorrektur"): er bleibt ueber list_contracts() in der vollen
        # Liste sichtbar - dort steht starts_on und traegt "Beginnt am ..." -,
        # zaehlt aber nicht in Summen, Kategorien oder der Rangliste mit.
        # budget_service.fixed_costs_snapshot haelt sich fuer die periodengerechte
        # Monatsvorschau bereits an dieselbe Regel (starts_on <= Monatsende).
        if contract.starts_on is not None and contract.starts_on > today:
            continue

        month_value = monthly_minor(contract.amount_minor, contract.frequency)
        year_value = yearly_minor(contract.amount_minor, contract.frequency)
        if contract.direction == "expense":
            monthly_expense += month_value
            yearly_expense += year_value
            yearly_ranking.append(
                ContractYearlyCost(
                    contract_id=contract.id,
                    name=contract.name,
                    contract_type=contract.contract_type,
                    frequency=contract.frequency,
                    amount_minor=contract.amount_minor,
                    monthly_minor=month_value,
                    yearly_minor=year_value,
                )
            )
        else:
            monthly_income += month_value
            yearly_income += year_value

        bucket = per_type.setdefault(
            contract.contract_type, {"count": 0, "monthly": 0, "yearly": 0}
        )
        bucket["count"] += 1
        bucket["monthly"] += month_value
        bucket["yearly"] += year_value

        bucket_category = per_category.setdefault(
            contract.category_id, {"count": 0, "monthly": 0, "yearly": 0}
        )
        bucket_category["count"] += 1
        bucket_category["monthly"] += month_value
        bucket_category["yearly"] += year_value

    by_type = [
        ContractTypeSummary(
            contract_type=cast(Any, key),
            count=value["count"],
            monthly_minor=value["monthly"],
            yearly_minor=value["yearly"],
        )
        for key, value in sorted(per_type.items(), key=lambda item: -item[1]["monthly"])
    ]

    by_category = [
        ContractCategorySummary(
            category_id=key,
            count=value["count"],
            monthly_minor=value["monthly"],
            yearly_minor=value["yearly"],
        )
        for key, value in sorted(per_category.items(), key=lambda item: -item[1]["monthly"])
    ]

    horizon = today + timedelta(days=upcoming_days)
    upcoming = [
        ContractUpcoming(
            contract_id=contract.id,
            name=contract.name,
            contract_type=contract.contract_type,
            direction=contract.direction,
            due_date=contract.next_due_date,
            amount_minor=contract.amount_minor,
        )
        for contract in contracts
        if contract.status in _COUNTING_STATUSES
        and contract.next_due_date <= horizon
        # Noch nicht begonnene Vertraege gehoeren nicht in "demnaechst faellig" -
        # ihre erste Faelligkeit ist keine echte Buchungserwartung, solange sie
        # nicht laeuft (gleiche starts_on-Regel wie oben).
        and (contract.starts_on is None or contract.starts_on <= today)
    ]
    upcoming.sort(key=lambda item: (item.due_date, item.name))

    ending_horizon = today + timedelta(days=60)
    ending_soon = [
        ContractEnding(
            contract_id=contract.id,
            name=contract.name,
            contract_type=contract.contract_type,
            ends_on=contract.ends_on,
            amount_minor=contract.amount_minor,
        )
        for contract in contracts
        if contract.ends_on is not None
        and contract.status in _COUNTING_STATUSES
        and today <= contract.ends_on <= ending_horizon
        and (contract.starts_on is None or contract.starts_on <= today)
    ]
    ending_soon.sort(key=lambda item: (item.ends_on, item.name))

    # Teuerste zuerst - nach Jahreskosten, nicht nach Monatsbetrag: eine
    # jaehrliche Praemie von 600 EUR ist teurer als ein Abo fuer 9,99 EUR im
    # Monat, stuende in einer Monatsrangliste aber weit darunter.
    yearly_ranking.sort(key=lambda item: (-item.yearly_minor, item.name))

    term_deadlines: list[ContractTermSoon] = []
    for contract in contracts:
        # Bewusst neu gerechnet statt `contract.term_deadline` gelesen: die
        # Zusammenfassung bekommt ihren Stichtag von aussen, das Feld am
        # Vertrag steht auf dem Tag des Servers.
        deadline = compute_term_deadline(contract, today)
        if deadline is None or deadline.kind != "fixed_term":
            continue
        if not 0 <= deadline.days_left <= _TERM_SOON_DAYS:
            continue
        term_deadlines.append(
            ContractTermSoon(
                contract_id=contract.id,
                name=contract.name,
                contract_type=contract.contract_type,
                amount_minor=contract.amount_minor,
                deadline_on=deadline.deadline_on,
                term_ends_on=deadline.term_ends_on,
                days_left=deadline.days_left,
            )
        )
    term_deadlines.sort(key=lambda item: (item.days_left, item.name))

    return ContractSummary(
        monthly_expense_minor=monthly_expense,
        yearly_expense_minor=yearly_expense,
        monthly_income_minor=monthly_income,
        yearly_income_minor=yearly_income,
        monthly_net_minor=monthly_income - monthly_expense,
        active_count=counts["active"],
        paused_count=counts["paused"],
        cancelled_count=counts["cancelled"],
        ended_count=counts["ended"],
        by_type=by_type,
        by_category=by_category,
        upcoming=upcoming[:20],
        ending_soon=ending_soon,
        yearly_by_contract=yearly_ranking[:_YEARLY_RANK_LIMIT],
        term_deadlines=term_deadlines,
    )


async def build_forecast(
    session: AsyncSession, household_id: UUID, *, today: date, until: date
) -> list[ContractUpcoming]:
    """Erwartete Faelligkeiten bis `until`, mehrfach je Vertrag.

    Erste Zukunftsansicht des Produkts: die Berichte kennen bisher nur
    Vergangenheit.
    """
    contracts = await list_contracts(
        session, household_id, statuses=list(_COUNTING_STATUSES)
    )
    entries: list[ContractUpcoming] = []

    for contract in contracts:
        cursor = contract.next_due_date
        # Eine lange nicht abgeglichene Faelligkeit kann in der Vergangenheit
        # liegen; sie wird auf heute vorgezogen statt uebersprungen.
        guard = 0
        while cursor < today and guard < 240:
            cursor = next_due_date(
                cursor,
                contract.frequency,
                due_rule=contract.due_rule,
                due_day=contract.due_day,
            )
            guard += 1
        while cursor <= until and guard < 240:
            if contract.ends_on is not None and cursor > contract.ends_on:
                break
            entries.append(
                ContractUpcoming(
                    contract_id=contract.id,
                    name=contract.name,
                    contract_type=contract.contract_type,
                    direction=contract.direction,
                    due_date=cursor,
                    amount_minor=contract.amount_minor,
                )
            )
            cursor = next_due_date(
                cursor,
                contract.frequency,
                due_rule=contract.due_rule,
                due_day=contract.due_day,
            )
            guard += 1

    entries.sort(key=lambda item: (item.due_date, item.name))
    return entries


# ---------------------------------------------------------------------------
# Cashflow je Kalenderzeitraum
# ---------------------------------------------------------------------------

_MONTH_NAMES: Final = (
    "Januar",
    "Februar",
    "März",
    "April",
    "Mai",
    "Juni",
    "Juli",
    "August",
    "September",
    "Oktober",
    "November",
    "Dezember",
)

# Deckel gegen eine Endlosschleife bei widerspruechlichen Vertragsdaten. Grosszuegig
# bemessen: ein woechentlicher Vertrag hat in 24 Monaten ueber 100 Faelligkeiten,
# und ein lange nicht abgeglichener Vertrag muss zusaetzlich von seinem alten
# next_due_date bis ins Fenster laufen.
_CASHFLOW_STEP_GUARD: Final = 600


def _period_bounds(
    today: date, granularity: str, periods: int
) -> list[tuple[date, date, str]]:
    """Kalenderraster ab dem LAUFENDEN Monat bzw. Quartal.

    Anker ist der Monats- bzw. Quartalserste und nicht `today`: nur so enthaelt
    die erste Spalte einen vollen Kalenderzeitraum und ist mit der
    Fixkostensumme und der Fixkostenquote im Budget vergleichbar. Ein bei
    `today` beginnendes Fenster ist genau der Fehler, den die alte
    Tagesprognose hatte.
    """
    step = 3 if granularity == "quarter" else 1
    first_month = (today.month - 1) // 3 * 3 + 1 if granularity == "quarter" else today.month
    anchor = date(today.year, first_month, 1)

    bounds: list[tuple[date, date, str]] = []
    for index in range(periods):
        start = add_months(anchor, index * step, day=1)
        end = add_months(start, step, day=1) - timedelta(days=1)
        label = (
            f"Q{(start.month - 1) // 3 + 1} {start.year}"
            if granularity == "quarter"
            else f"{_MONTH_NAMES[start.month - 1]} {start.year}"
        )
        bounds.append((start, end, label))
    return bounds


def due_dates_in_window(
    *,
    next_due_date_value: date,
    frequency: str,
    due_rule: str,
    due_day: int | None,
    starts_on: date | None,
    ends_on: date | None,
    window_start: date,
    window_end: date,
) -> list[date]:
    """Alle Faelligkeiten im Fenster - auch die schon abgebuchten.

    Nimmt Einzelfelder statt eines `Contract` entgegen: budget_service fuellt
    sie direkt aus einer schlanken SQL-Zeile, ohne ein volles Vertragsobjekt
    zu konstruieren (Kategorie-Forecast im Monatsbudget).

    `next_due_date` steht nach einem Abgleich bereits hinter der letzten
    verbuchten Rate. Fuer ein Kalenderraster genuegt es deshalb nicht, ab dort
    vorwaerts zu laufen: die Rate vom 2. des laufenden Monats fehlte im
    laufenden Monat, und dessen Summe waere kleiner als die Fixkostensumme
    daneben. Der Zeiger geht darum erst hinter den Fensteranfang zurueck und
    von dort wieder vorwaerts.
    """
    cursor = next_due_date_value
    steps = 0
    while cursor >= window_start and steps < _CASHFLOW_STEP_GUARD:
        cursor = previous_due_date(cursor, frequency, due_rule=due_rule, due_day=due_day)
        steps += 1

    dates: list[date] = []
    while steps < _CASHFLOW_STEP_GUARD:
        cursor = next_due_date(cursor, frequency, due_rule=due_rule, due_day=due_day)
        steps += 1
        if cursor > window_end:
            break
        if ends_on is not None and cursor > ends_on:
            break
        if cursor < window_start:
            continue
        # Ohne gepflegtes `starts_on` gilt ein Vertrag als schon immer laufend -
        # gleiche Annahme wie budget_service.fixed_costs_snapshot.
        if starts_on is not None and cursor < starts_on:
            continue
        dates.append(cursor)
    return dates


async def build_cashflow(
    session: AsyncSession,
    household_id: UUID,
    *,
    today: date,
    granularity: str = "month",
    periods: int = 6,
) -> ContractCashflowResponse:
    """Vertrags-Cashflow je Kalendermonat oder -quartal.

    Loest die rollierende Tagesprognose ab. In "die naechsten 30 Tage" fiel ein
    monatlicher Vertrag je nach Faelligkeitstag ein- oder zweimal, in "die
    naechsten 90 Tage" drei- oder viermal; die Summe schwankte mit dem Kalender
    und war mit keiner anderen Zahl der Anwendung vergleichbar. Im
    Kalenderraster erscheint jeder monatliche Vertrag je Monat genau einmal.
    """
    bounds = _period_bounds(today, granularity, periods)
    window_start = bounds[0][0]
    window_end = bounds[-1][1]

    contracts = await list_contracts(
        session, household_id, statuses=list(_COUNTING_STATUSES)
    )

    buckets: list[list[ContractUpcoming]] = [[] for _ in bounds]
    for contract in contracts:
        for due in due_dates_in_window(
            next_due_date_value=contract.next_due_date,
            frequency=contract.frequency,
            due_rule=contract.due_rule,
            due_day=contract.due_day,
            starts_on=contract.starts_on,
            ends_on=contract.ends_on,
            window_start=window_start,
            window_end=window_end,
        ):
            # Lineare Suche statt Datumsarithmetik: bei hoechstens 24 Perioden
            # ist sie billiger als die Sonderfaelle einer Quartalsformel.
            for index, (start, end, _label) in enumerate(bounds):
                if start <= due <= end:
                    buckets[index].append(
                        ContractUpcoming(
                            contract_id=contract.id,
                            name=contract.name,
                            contract_type=contract.contract_type,
                            direction=contract.direction,
                            due_date=due,
                            amount_minor=contract.amount_minor,
                        )
                    )
                    break

    result: list[ContractCashflowPeriod] = []
    cumulative = 0
    for (start, end, label), items in zip(bounds, buckets, strict=True):
        items.sort(key=lambda item: (item.due_date, item.name))
        expense = sum(item.amount_minor for item in items if item.direction == "expense")
        income = sum(item.amount_minor for item in items if item.direction == "income")
        remaining_expense = sum(
            item.amount_minor
            for item in items
            if item.direction == "expense" and item.due_date >= today
        )
        remaining_income = sum(
            item.amount_minor
            for item in items
            if item.direction == "income" and item.due_date >= today
        )
        cumulative += income - expense
        result.append(
            ContractCashflowPeriod(
                period_start=start,
                period_end=end,
                label=label,
                expense_minor=expense,
                income_minor=income,
                net_minor=income - expense,
                remaining_expense_minor=remaining_expense,
                remaining_income_minor=remaining_income,
                remaining_net_minor=remaining_income - remaining_expense,
                cumulative_net_minor=cumulative,
                items=items,
            )
        )

    return ContractCashflowResponse(
        granularity=cast(Any, granularity), generated_on=today, periods=result
    )


# Zwei Jahre. Kuerzer waere fuer die eine Frage, um die es hier geht, zu kurz:
# eine befristete Einnahme (Elterngeld, Unterhalt, auslaufende Vermietung)
# endet typischerweise in diesem Fenster, und genau ihr Wegfall gegenueber
# den weiterlaufenden Fixkosten ist das, was die Entwicklung zeigen soll.
_INCOME_TREND_MONTHS: Final = 24


@dataclass(frozen=True)
class _IncomeRow:
    """Ein Vertrag, reduziert auf das, was die Auswertung braucht."""

    direction: str
    monthly_minor: int
    income_kind: ContractIncomeKind | None
    person_id: UUID | None
    person_name: str | None
    person_is_active: bool
    starts_on: date | None
    ends_on: date | None

    def runs_in(self, period_start: date, period_end: date) -> bool:
        """Laeuft der Vertrag in diesem Kalendermonat?

        Ohne gepflegtes `starts_on` gilt er als schon immer laufend - dieselbe
        Annahme wie in `due_dates_in_window` und `budget_service`.
        """
        if self.starts_on is not None and self.starts_on > period_end:
            return False
        return self.ends_on is None or self.ends_on >= period_start


async def build_income_summary(
    session: AsyncSession,
    household_id: UUID,
    *,
    today: date,
    months: int = _INCOME_TREND_MONTHS,
) -> ContractIncomeSummary:
    """Einnahmen je Person und je Art, dazu 24 Monate Entwicklung gegen die
    Fixkosten (Liquiditaetsplan, Stufe 6).

    Normiert wird ausschliesslich ueber `monthly_minor`, und zwar je Vertrag
    vor jeder Summenbildung. Eine jaehrliche Einnahme mit 12 zu multiplizieren
    und danach zu teilen ergaebe je nach Reihenfolge andere Rundungscent als
    Budget, Berichte und Vorschau - dieselbe Zahl haette dann in der App zwei
    Werte (Plan 5.2).

    Die Entwicklung laeuft ab dem LAUFENDEN Kalendermonat vorwaerts, nicht ab
    heute: nur ein voller Kalendermonat ist mit der Fixkostensumme daneben
    vergleichbar (gleiche Begruendung wie `_period_bounds`).
    """
    result = await session.execute(
        text(
            "select c.direction, c.amount_minor, c.frequency, c.income_kind, "
            "c.person_id, c.starts_on, c.ends_on, p.name as person_name, "
            "p.is_active as person_is_active from contracts c "
            # Der Join geht ueber BEIDE Schluesselspalten, weil der
            # Fremdschluessel zusammengesetzt ist - ein Join nur ueber die ID
            # koennte bei einer fremden UUID einen fremden Namen liefern.
            "left join household_people p "
            "on p.id = c.person_id and p.household_id = c.household_id "
            "where c.household_id = :household_id "
            "and c.status = any(cast(:statuses as text[])) "
            "and c.merged_into_id is null"
        ),
        {"household_id": str(household_id), "statuses": list(_COUNTING_STATUSES)},
    )

    rows = [
        _IncomeRow(
            direction=str(row.direction),
            monthly_minor=monthly_minor(int(row.amount_minor), row.frequency),
            income_kind=row.income_kind,
            person_id=UUID(str(row.person_id)) if row.person_id is not None else None,
            person_name=row.person_name,
            # Ein Vertrag ohne Person ist nicht "inaktiv" - die Kennzeichnung
            # gilt nur fuer geloeschte Personen.
            person_is_active=bool(row.person_is_active) if row.person_id is not None else True,
            starts_on=row.starts_on,
            ends_on=row.ends_on,
        )
        for row in result
    ]

    income_rows = [row for row in rows if row.direction == "income"]

    person_totals: dict[UUID | None, list[_IncomeRow]] = {}
    for row in income_rows:
        person_totals.setdefault(row.person_id, []).append(row)

    by_person = [
        ContractIncomePersonBucket(
            person_id=person_id,
            person_name=members[0].person_name,
            person_is_active=members[0].person_is_active,
            contract_count=len(members),
            monthly_minor=sum(member.monthly_minor for member in members),
            by_kind=_kind_buckets(members),
        )
        for person_id, members in person_totals.items()
    ]
    # Ohne Zuordnung immer zuletzt: das ist die Restgruppe, keine Person.
    by_person.sort(
        key=lambda bucket: (
            bucket.person_id is None,
            -bucket.monthly_minor,
            (bucket.person_name or "").lower(),
        )
    )

    bounds = _period_bounds(today, "month", months)
    trend: list[ContractIncomeTrendPoint] = []
    for period_start, period_end, label in bounds:
        income = sum(
            row.monthly_minor
            for row in rows
            if row.direction == "income" and row.runs_in(period_start, period_end)
        )
        fixed = sum(
            row.monthly_minor
            for row in rows
            if row.direction == "expense" and row.runs_in(period_start, period_end)
        )
        trend.append(
            ContractIncomeTrendPoint(
                period_start=period_start,
                label=label,
                income_minor=income,
                fixed_minor=fixed,
                net_minor=income - fixed,
            )
        )

    return ContractIncomeSummary(
        generated_on=today,
        monthly_income_minor=sum(row.monthly_minor for row in income_rows),
        monthly_fixed_minor=sum(
            row.monthly_minor for row in rows if row.direction == "expense"
        ),
        by_person=by_person,
        by_kind=_kind_buckets(income_rows),
        trend=trend,
    )


def _kind_buckets(rows: list[_IncomeRow]) -> list[ContractIncomeKindBucket]:
    grouped: dict[ContractIncomeKind | None, list[_IncomeRow]] = {}
    for row in rows:
        grouped.setdefault(row.income_kind, []).append(row)
    buckets = [
        ContractIncomeKindBucket(
            income_kind=kind,
            contract_count=len(members),
            monthly_minor=sum(member.monthly_minor for member in members),
        )
        for kind, members in grouped.items()
    ]
    # "Noch keine Art" zuletzt - es ist eine offene Aufgabe, keine Kategorie.
    buckets.sort(key=lambda bucket: (bucket.income_kind is None, -bucket.monthly_minor))
    return buckets


# ---------------------------------------------------------------------------
# Schreiben
# ---------------------------------------------------------------------------


async def record_event(
    session: AsyncSession,
    household_id: UUID,
    contract_id: UUID,
    kind: str,
    *,
    occurred_on: date | None = None,
    payload: dict[str, Any] | None = None,
    on_conflict_do_nothing: bool = False,
) -> bool:
    """Schreibt ein Vertragsereignis; liefert, ob eine Zeile entstanden ist.

    `on_conflict_do_nothing` traegt den strukturellen Dedup der
    Fristerinnerung (`contract_events_term_ending_uniq`): der wiederholte Lauf
    am naechsten Morgen soll dieselbe Erinnerung nicht erneut schreiben, statt
    das in der Schleife nachzubauen. Der `household_id` steht ausdruecklich im
    INSERT - der Worker laeuft ohne RLS, `default current_household_id()`
    greift dort nicht.
    """
    result = await session.execute(
        text(
            "insert into contract_events (household_id, contract_id, kind, occurred_on, payload) "
            "values (:household_id, :contract_id, :kind, coalesce(:occurred_on, current_date), "
            "cast(:payload as jsonb))"
            + (" on conflict do nothing" if on_conflict_do_nothing else "")
            + " returning id"
        ),
        {
            "household_id": str(household_id),
            "contract_id": str(contract_id),
            "kind": kind,
            "occurred_on": occurred_on,
            "payload": _json(payload or {}),
        },
    )
    return result.first() is not None


async def scan_term_deadlines(
    session: AsyncSession, household_id: UUID, *, today: date
) -> int:
    """Schreibt faellige `term_ending`-Ereignisse; liefert deren Zahl.

    Laeuft nach dem Abgleich, damit `status` und `ends_on` bereits
    fortgeschrieben sind - ein im selben Lauf beendeter Vertrag soll keine
    Fristerinnerung mehr bekommen. `today` kommt von aussen und wird nicht hier
    gelesen: der Aufrufer bestimmt den Stichtag, sonst faellt eine Stufe an der
    Tagesgrenze zwischen Prozess- und Serverzeitzone um einen Tag daneben.

    Committet nicht - der Aufrufer klammert (API-Request bzw. Worker-Haushalt).
    """
    contracts = await list_contracts(session, household_id, statuses=["active", "paused"])
    written = 0

    for contract in contracts:
        deadline = compute_term_deadline(contract, today)
        # Nur befristete Vertraege: bei einem unbefristeten kann keine Frist
        # verstreichen, eine Erinnerung waere reines Rauschen.
        if deadline is None or deadline.kind != "fixed_term" or deadline.days_left < 0:
            continue

        # Kleinste passende Stufe. Bewusst nicht "days_left == stage": ein heute
        # angelegter Vertrag mit Frist in zwoelf Tagen bekaeme sonst nie ein
        # Ereignis. Die Wiederholung verhindert der Unique-Index.
        stage = next((item for item in _TERM_ENDING_STAGES if deadline.days_left <= item), None)
        if stage is None:
            continue

        # Flach halten: ContractEvent.payload laesst nur Skalare zu.
        created = await record_event(
            session,
            household_id,
            contract.id,
            "term_ending",
            occurred_on=today,
            payload={
                "stage": str(stage),
                "deadline": deadline.deadline_on.isoformat(),
                "term_ends_on": deadline.term_ends_on.isoformat(),
                "days_left": deadline.days_left,
                "renewal_term_months": contract.renewal_term_months,
            },
            on_conflict_do_nothing=True,
        )
        if created:
            written += 1

    return written


def _json(value: dict[str, Any]) -> str:
    return json.dumps(value, default=str, ensure_ascii=False)


def assert_status_transition(current: str, target: str) -> None:
    if current == target:
        return
    if target not in _ALLOWED_STATUS_TRANSITIONS.get(current, frozenset()):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=CONTRACT_STATUS_INVALID
        )


async def open_amount_stage(
    session: AsyncSession,
    household_id: UUID,
    contract_id: UUID,
    *,
    amount_minor: int,
    valid_from: date,
    change_reason: str,
    source_transaction_id: UUID | None = None,
) -> None:
    """Schliesst die offene Preisstufe und oeffnet eine neue.

    Der partielle Unique-Index `contract_amount_history_open_uniq` laesst nur
    eine offene Stufe je Vertrag zu; das Schliessen muss deshalb zuerst
    passieren.
    """
    open_row = (
        await session.execute(
            text(
                "select id, amount_minor, valid_from from contract_amount_history "
                "where household_id = :household_id and contract_id = :contract_id "
                "and valid_to is null"
            ),
            {"household_id": str(household_id), "contract_id": str(contract_id)},
        )
    ).first()

    previous_amount: int | None = None
    if open_row is not None:
        if open_row.valid_from > valid_from:
            # Eine neue Stufe vor der laufenden waere kein Verlauf mehr, sondern
            # eine Ueberschreibung der Geschichte.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=CONTRACT_AMOUNT_HISTORY_CONFLICT,
            )
        previous_amount = int(open_row.amount_minor)
        if open_row.valid_from == valid_from:
            await session.execute(
                text("delete from contract_amount_history where id = :id"),
                {"id": str(open_row.id)},
            )
            previous_amount = None
        else:
            await session.execute(
                text(
                    "update contract_amount_history set valid_to = :valid_to where id = :id"
                ),
                {"id": str(open_row.id), "valid_to": valid_from - timedelta(days=1)},
            )

    await session.execute(
        text(
            "insert into contract_amount_history (household_id, contract_id, amount_minor, "
            "valid_from, delta_minor, change_reason, source_transaction_id) "
            "values (:household_id, :contract_id, :amount_minor, :valid_from, :delta, "
            ":change_reason, :source_transaction_id)"
        ),
        {
            "household_id": str(household_id),
            "contract_id": str(contract_id),
            "amount_minor": amount_minor,
            "valid_from": valid_from,
            "delta": None if previous_amount is None else amount_minor - previous_amount,
            "change_reason": change_reason,
            "source_transaction_id": (
                str(source_transaction_id) if source_transaction_id else None
            ),
        },
    )


# ---------------------------------------------------------------------------
# Duplikatpruefung
# ---------------------------------------------------------------------------

# 5 % Betragsnaehe: genug, um eine Rundungsdifferenz oder eine kleine
# Preisanpassung zu erfassen, zu wenig, um zwei echte Vertraege zu verwechseln.
_DUPLICATE_AMOUNT_RATIO: Final = 0.05
_DUPLICATE_NAME_SIMILARITY: Final = 0.4


async def find_duplicate_candidates(
    session: AsyncSession, household_id: UUID, payload: ContractDuplicateCheck
) -> list[ContractDuplicateCandidate]:
    """Sucht bestehende Vertraege, die derselbe sein koennten.

    Wird vor dem Anlegen aufgerufen, damit der Nutzer den Treffer sieht, statt
    hinterher einen zweiten Eintrag desselben Vertrags in der Summe zu haben.
    """
    tolerance = (
        round(payload.amount_minor * _DUPLICATE_AMOUNT_RATIO)
        if payload.amount_minor is not None
        else None
    )
    result = await session.execute(
        text(
            "select id, name, provider_label, amount_minor, frequency, status, "
            "case "
            "  when cast(:payee_id as uuid) is not null and payee_id = cast(:payee_id as uuid) "
            "    then 'same_payee' "
            "  when similarity(lower(name), lower(:name)) >= :name_similarity "
            "    then 'similar_name' "
            "  else 'similar_amount' "
            "end as reason "
            "from contracts "
            "where household_id = :household_id "
            "and status in ('active', 'paused', 'cancelled') "
            "and (cast(:exclude_id as uuid) is null or id <> cast(:exclude_id as uuid)) "
            "and ("
            "  (cast(:payee_id as uuid) is not null and payee_id = cast(:payee_id as uuid)) "
            "  or similarity(lower(name), lower(:name)) >= :name_similarity "
            "  or (cast(:amount_minor as bigint) is not null "
            "      and cast(:frequency as text) is not null "
            "      and frequency = :frequency "
            "      and abs(amount_minor - cast(:amount_minor as bigint)) <= :tolerance) "
            ") "
            "order by name asc limit 5"
        ),
        {
            "household_id": str(household_id),
            "name": payload.name,
            "name_similarity": _DUPLICATE_NAME_SIMILARITY,
            "payee_id": str(payload.payee_id) if payload.payee_id else None,
            "exclude_id": str(payload.exclude_id) if payload.exclude_id else None,
            "amount_minor": payload.amount_minor,
            "frequency": payload.frequency,
            "tolerance": tolerance or 0,
        },
    )
    return [ContractDuplicateCandidate(**row._mapping) for row in result]


# ---------------------------------------------------------------------------
# Anbieterkatalog
# ---------------------------------------------------------------------------

# Mehr Vorschlaege als das Formular anzeigen kann, waeren keine Hilfe mehr.
_PROVIDER_SUGGESTION_LIMIT: Final = 8

_PROVIDER_COLUMNS: Final = (
    "t.semantic_key, t.provider_name, t.match_patterns, t.contract_type, "
    "t.default_category_key, t.typical_frequency, t.typical_min_term_months, "
    "t.typical_cancellation_days, t.priority"
)


def provider_key(label: str | None) -> str:
    """Vergleichsschluessel fuer Anbieternamen.

    Dieselbe Normalform wie `contract_matching_service.payee_key` und
    ausdruecklich ueber dieselbe Funktion: der Katalog muss den Anbieter genau
    dann finden, wenn ihn auch der Abgleichsmotor findet. Der direkte Weg ueber
    `contract_matching_service` scheidet aus, weil das Modul seinerseits hier
    einhaengt.
    """
    if not label:
        return ""
    return rules_service._recurring_payee_key(label)


def _provider_matches(needle: str, candidates: list[str]) -> bool:
    """Trifft `needle` einen der Anbieterschluessel?

    Zwei Faelle, beide notwendig: der Nutzer tippt den Anfang eines
    Anbieternamens ("net" -> Netflix), oder er fuegt einen ganzen Bankstring ein
    ("musterfunk deutschland gmbh" -> Musterfunk). Verglichen wird auf Wortgrenzen,
    sonst traefe das Muster "tk" jede Zeichenkette mit diesen zwei Buchstaben.
    """
    padded = f" {needle} "
    return any(
        candidate and (f" {candidate} " in padded or candidate.startswith(needle))
        for candidate in candidates
    )


async def suggest_providers(
    session: AsyncSession, household_id: UUID, query: str
) -> list[ContractProviderSuggestion]:
    """Anbietervorschlaege fuer das Anlegen-Formular.

    Der Katalog ist klein und global; er wird vollstaendig geladen und in Python
    gefiltert. Ein `ilike` in SQL waere eine zweite Normalisierung neben
    `payee_key()` - und damit die sichere Art, genau die Anbieter zu verfehlen,
    die der Abgleichsmotor selbst erkennt.
    """
    needle = provider_key(query)
    if not needle:
        return []

    result = await session.execute(
        text(
            f"select {_PROVIDER_COLUMNS}, c.id as category_id "
            "from contract_provider_templates t "
            # Die Zielkategorie ist haushaltsabhaengig: derselbe Katalogeintrag
            # zeigt in jedem Haushalt auf dessen eigene Kategorie-UUID.
            "left join categories c on c.household_id = :household_id "
            "  and c.semantic_key = t.default_category_key and not c.is_archived "
            "where t.is_active order by t.priority asc, t.provider_name asc"
        ),
        {"household_id": str(household_id)},
    )

    suggestions: list[ContractProviderSuggestion] = []
    for row in result:
        candidates = [*list(row.match_patterns or []), provider_key(row.provider_name)]
        if not _provider_matches(needle, candidates):
            continue
        suggestions.append(
            ContractProviderSuggestion(
                semantic_key=row.semantic_key,
                provider_name=row.provider_name,
                contract_type=cast(Any, row.contract_type),
                default_category_key=row.default_category_key,
                category_id=row.category_id,
                typical_frequency=cast(Any, row.typical_frequency),
                typical_min_term_months=row.typical_min_term_months,
                typical_cancellation_days=row.typical_cancellation_days,
            )
        )
        if len(suggestions) >= _PROVIDER_SUGGESTION_LIMIT:
            break

    return suggestions


async def assert_provider_template(session: AsyncSession, semantic_key: str | None) -> None:
    """Prueft den Katalogschluessel, bevor er in `contracts` landet.

    Ohne diese Pruefung antwortete ein Tippfehler im Schluessel mit einem
    Fremdschluesselfehler und damit HTTP 500 statt einer Fachmeldung.
    """
    if semantic_key is None:
        return
    result = await session.execute(
        text(
            "select 1 from contract_provider_templates "
            "where semantic_key = :semantic_key and is_active"
        ),
        {"semantic_key": semantic_key},
    )
    if result.first() is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=CONTRACT_PROVIDER_TEMPLATE_UNKNOWN,
        )


# ---------------------------------------------------------------------------
# Zusammenfuehren
# ---------------------------------------------------------------------------


async def _load_merge_partners(
    session: AsyncSession, household_id: UUID, target_id: UUID, source_ids: list[UUID]
) -> tuple[Contract, list[Contract]]:
    if target_id in source_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=CONTRACT_MERGE_TARGET_IN_SOURCES
        )
    target = await require_contract(session, household_id, target_id)
    sources: list[Contract] = []
    for source_id in source_ids:
        sources.append(await require_contract(session, household_id, source_id))
    return target, sources


async def build_merge_preview(
    session: AsyncSession, household_id: UUID, target_id: UUID, source_ids: list[UUID]
) -> ContractMergePreview:
    target, sources = await _load_merge_partners(session, household_id, target_id, source_ids)
    all_ids = [str(target_id)] + [str(item.id) for item in sources]

    counts = (
        await session.execute(
            text(
                "select "
                "(select count(*) from contract_occurrences "
                "  where household_id = :household_id "
                "    and contract_id = any(cast(:sources as uuid[]))) as occurrences, "
                "(select count(*) from contract_amount_history "
                "  where household_id = :household_id "
                "    and contract_id = any(cast(:sources as uuid[]))) as history, "
                "(select count(*) from contract_events "
                "  where household_id = :household_id "
                "    and contract_id = any(cast(:sources as uuid[]))) as events"
            ),
            {
                "household_id": str(household_id),
                "sources": [str(item.id) for item in sources],
            },
        )
    ).one()

    steps_result = await session.execute(
        text(
            "select id, contract_id, amount_minor, valid_from, valid_to, delta_minor, "
            "change_reason, source_transaction_id from contract_amount_history "
            "where household_id = :household_id and contract_id = any(cast(:ids as uuid[])) "
            "order by valid_from asc"
        ),
        {"household_id": str(household_id), "ids": all_ids},
    )
    steps = [ContractAmountEntry(**row._mapping) for row in steps_result]

    blocking: str | None = None
    frequencies = {target.frequency} | {item.frequency for item in sources}
    directions = {target.direction} | {item.direction for item in sources}
    if len(directions) > 1:
        blocking = "contract_merge_direction_mismatch"
    elif len(frequencies) > 1:
        # Kein harter Block: ein Wechsel von jaehrlich auf monatlich ist ein
        # realer Vorgang. Der Nutzer soll ihn aber sehen, bevor er bestaetigt.
        blocking = None

    resulting_amount = steps[-1].amount_minor if steps else target.amount_minor

    return ContractMergePreview(
        target_id=target.id,
        target_name=target.name,
        source_names=[item.name for item in sources],
        moved_occurrences=int(counts.occurrences),
        moved_history_entries=int(counts.history),
        moved_events=int(counts.events),
        resulting_amount_minor=resulting_amount,
        resulting_frequency=target.frequency,
        amount_steps=steps,
        blocking_reason=blocking,
    )


async def find_merge_by_request_id(
    session: AsyncSession, household_id: UUID, target_id: UUID, request_id: str
) -> bool:
    """Idempotenz ohne eigene Tabelle: die Zusammenfuehrung hinterlaesst ein
    Ereignis mit der Anfragekennung."""
    result = await session.execute(
        text(
            "select 1 from contract_events where household_id = :household_id "
            "and contract_id = :contract_id and kind = 'merged' "
            "and payload ->> 'request_id' = :request_id limit 1"
        ),
        {
            "household_id": str(household_id),
            "contract_id": str(target_id),
            "request_id": request_id,
        },
    )
    return result.first() is not None


_MERGE_SNAPSHOT_TABLES: Final[dict[str, str]] = {
    "occurrence": "contract_occurrences",
    "history": "contract_amount_history",
    "event": "contract_events",
}


async def _snapshot_rows(
    session: AsyncSession,
    operation_id: UUID,
    household_id: UUID,
    entity_type: str,
    rows: Any,
) -> None:
    """Haelt den Vorher-Zustand jeder betroffenen Zeile im Aenderungsjournal
    fest, BEVOR irgendetwas mutiert wird (Abschnitt 19.7). `rows` sind
    SQLAlchemy-Ergebniszeilen oder gleichwertige Mappings mit einer `id`."""
    materialized = list(rows)
    if not materialized:
        return
    await session.execute(
        text(
            "insert into contract_merge_changes "
            "(operation_id, household_id, entity_type, entity_id, prior_data) "
            "values (:operation_id, :household_id, :entity_type, :entity_id, "
            "cast(:prior_data as jsonb))"
        ),
        [
            {
                "operation_id": str(operation_id),
                "household_id": str(household_id),
                "entity_type": entity_type,
                "entity_id": str(row.id),
                "prior_data": _json(dict(row._mapping)),
            }
            for row in materialized
        ],
    )


async def _create_merge_operation(
    session: AsyncSession,
    household_id: UUID,
    target_id: UUID,
    source_id_strings: list[str],
    request_id: str,
    actor_user_id: UUID | None,
) -> UUID:
    target_version = (
        await session.execute(
            text("select version from contracts where id = :id and household_id = :household_id"),
            {"id": str(target_id), "household_id": str(household_id)},
        )
    ).scalar_one()
    row = (
        await session.execute(
            text(
                "insert into contract_merge_operations "
                "(household_id, target_id, source_ids, request_id, actor_user_id, "
                "target_version_before) "
                "values (:household_id, :target, cast(:sources as uuid[]), :request_id, "
                ":actor, :version) returning id"
            ),
            {
                "household_id": str(household_id),
                "target": str(target_id),
                "sources": source_id_strings,
                "request_id": request_id,
                "actor": str(actor_user_id) if actor_user_id else None,
                "version": int(target_version),
            },
        )
    ).first()
    assert row is not None
    return UUID(str(row.id))


# ---------------------------------------------------------------------------
# Vertragsschluessel aus den beteiligten Buchungen (Abschnitt 19.6, Leitsatz 1)
# ---------------------------------------------------------------------------

# Deckelung wie in der Erkennung (`_MAX_PURPOSES_PER_PAYEE`): die generische
# Ableitung vergleicht die bereinigten Verwendungszwecke ueber die laengste
# gemeinsame Zeichenkette und ist damit O(n*m).
_MERGE_KEY_MAX_PURPOSES: Final = 40

# Verwendungszweck einer Buchung, genau in der Vorrangfolge, in der ihn auch
# der Abgleich liest (`contract_matching_service._CANDIDATE_COLUMNS`): von Hand
# erfasste Buchungen haben keinen `purpose_raw`, dort ist die Notiz der einzige
# Text.
_MERGE_PURPOSE_TEXT: Final = "coalesce(nullif(btrim(t.purpose_raw), ''), t.note, '')"

# Empfaengertext einer Buchung, dieselbe Vorrangfolge wie im Abgleich.
_MERGE_PAYEE_TEXT: Final = (
    "coalesce(nullif(btrim(p.name), ''), nullif(btrim(t.payee_label), ''), "
    "nullif(btrim(t.payee_raw), ''), '')"
)


async def _payee_window_purposes(
    session: AsyncSession, household_id: UUID, contracts: Sequence[Contract]
) -> list[str]:
    """Zweiter Weg zu den Verwendungszwecken: Empfaenger, Betragsstufe, Zeitraum.

    Der Kandidatenkreis ist bewusst eng gefasst, weil eine einzige fremde
    Buchung hier einen falschen Vertragsschluessel erzeugte - und der behauptet
    im Abgleich eine Identitaet von 1.00, die es nicht gibt:

    * gleicher Empfaenger, geprueft ueber `payee_id` ODER die Namensnormalform
      des Abgleichs. Beides ist noetig: im uebernommenen Altbestand ist
      `transactions.payee_id` durchgehend NULL, waehrend die Vertraege eine
      `payee_id` tragen - ein Join darueber faende nichts,
    * gleiche Richtung,
    * Betrag auf einer der Stufen der beteiligten Vertraege, innerhalb der
      ueblichen Bandbreite. Genau dieser Filter trennt beim selben
      Empfaengernamen "Musterfunk Deutschland GmbH" den Mobilfunkvertrag (99,90)
      von den Festnetzstufen (49,95/39,95),
    * innerhalb des belegten Zeitraums, sofern ihn ALLE Beteiligten kennen. Ein
      einziger Legacy-Vertrag ohne `first_seen_date` macht die Grenze
      unbrauchbar und sie entfaellt dann ganz,
    * nicht bereits einem unbeteiligten Vertrag zugeordnet und nicht durch
      einen manuellen Override anderswohin gebunden (Leitsatz 5).
    """
    if len({item.direction for item in contracts}) != 1:
        # Einnahme und Ausgabe haben keinen gemeinsamen Kandidatenkreis. Die
        # Vorschau meldet den Fall bereits als `blocking_reason`.
        return []

    payee_ids = {str(item.payee_id) for item in contracts if item.payee_id is not None}
    identity_keys = {
        key
        for key in (provider_key(item.provider_label or item.name) for item in contracts)
        if key
    }
    if not payee_ids and not identity_keys:
        return []

    contract_ids = [str(item.id) for item in contracts]
    amounts = {item.amount_minor for item in contracts}
    steps = await session.execute(
        text(
            "select amount_minor from contract_amount_history "
            "where household_id = :household_id and contract_id = any(cast(:ids as uuid[]))"
        ),
        {"household_id": str(household_id), "ids": contract_ids},
    )
    amounts.update(int(row.amount_minor) for row in steps)
    lows: list[int] = []
    highs: list[int] = []
    for amount in sorted(amounts):
        tolerance = rules_service._amount_tolerance(amount)
        lows.append(max(0, abs(amount) - tolerance))
        highs.append(abs(amount) + tolerance)

    # Nur eine LUECKENLOS belegte Spanne taugt als Grenze: fehlt sie auch nur
    # einem Beteiligten, schnitte das Fenster ausgerechnet die aelteren
    # Preisstufen ab, um deretwillen zusammengefuehrt wird.
    known_from = [item.first_seen_date for item in contracts if item.first_seen_date]
    known_to = [item.last_seen_date for item in contracts if item.last_seen_date]
    window_from = min(known_from) if len(known_from) == len(contracts) else None
    window_to = max(known_to) if len(known_to) == len(contracts) else None

    # direction ist ein Literal aus dem Schema, kein Nutzertext - die
    # Fallunterscheidung hier statt im SQL haelt den Index nutzbar.
    sign_condition = (
        "t.amount_minor < 0" if contracts[0].direction == "expense" else "t.amount_minor > 0"
    )

    rows = (
        await session.execute(
            text(
                f"select {_MERGE_PURPOSE_TEXT} as purpose_text, "
                f"{_MERGE_PAYEE_TEXT} as payee_label, t.payee_id "
                "from transactions t "
                "left join payees p on p.id = t.payee_id and p.household_id = t.household_id "
                "where t.household_id = :household_id "
                "and not t.is_archived and not t.is_transfer "
                f"and {sign_condition} "
                "and (cast(:window_from as date) is null "
                "  or t.booked_date >= cast(:window_from as date)) "
                "and (cast(:window_to as date) is null "
                "  or t.booked_date <= cast(:window_to as date)) "
                "and exists (select 1 from "
                "  unnest(cast(:lows as bigint[]), cast(:highs as bigint[])) as band(lo, hi) "
                "  where abs(t.amount_minor) between band.lo and band.hi) "
                # Der Haushaltsfilter im Unterausdruck ist kein Beiwerk: der
                # partielle Unique-Index liegt auf (household_id, transaction_id).
                "and not exists (select 1 from contract_occurrences o "
                "  where o.household_id = :household_id and o.transaction_id = t.id "
                "  and not (o.contract_id = any(cast(:ids as uuid[])))) "
                "and not exists (select 1 from contract_assignment_overrides ov "
                "  where ov.household_id = :household_id and ov.transaction_id = t.id "
                "  and (ov.contract_id is null "
                "    or not (ov.contract_id = any(cast(:ids as uuid[]))))) "
                "order by t.booked_date asc, t.id asc"
            ),
            {
                "household_id": str(household_id),
                "ids": contract_ids,
                "lows": lows,
                "highs": highs,
                "window_from": window_from,
                "window_to": window_to,
            },
        )
    ).all()

    purposes: list[str] = []
    for row in rows:
        same_payee = (
            row.payee_id is not None and str(row.payee_id) in payee_ids
        ) or provider_key(row.payee_label) in identity_keys
        if not same_payee or not row.purpose_text:
            continue
        purposes.append(str(row.purpose_text))
        if len(purposes) >= _MERGE_KEY_MAX_PURPOSES:
            break
    return purposes


async def _merge_key_purposes(
    session: AsyncSession, household_id: UUID, contracts: Sequence[Contract]
) -> list[str]:
    """Verwendungszwecke der Buchungen, die zu Ziel und Quellen gehoeren.

    Erster und belastbarster Weg sind die ueber `contract_occurrences.
    transaction_id` bereits nachgewiesenen Buchungen: dort ist die Zugehoerigkeit
    keine Vermutung, sondern der gespeicherte Stand.

    Im uebernommenen Altbestand haengt dort allerdings nichts. Die drei Zeilen
    desselben Anbieters haben zusammen vier Faelligkeiten, alle mit Status
    `missing` und ohne Buchung - genau deshalb steht die Zusammenfuehrung
    ueberhaupt zur Debatte. Fuer diesen Fall gibt es den zweiten Weg.
    """
    linked = (
        await session.execute(
            text(
                f"select {_MERGE_PURPOSE_TEXT} as purpose_text from contract_occurrences o "
                "join transactions t on t.id = o.transaction_id "
                "  and t.household_id = o.household_id "
                "where o.household_id = :household_id "
                "and o.contract_id = any(cast(:ids as uuid[])) "
                "and o.transaction_id is not null "
                "order by t.booked_date asc, t.id asc"
            ),
            {
                "household_id": str(household_id),
                "ids": [str(item.id) for item in contracts],
            },
        )
    ).all()
    purposes = [str(row.purpose_text) for row in linked if row.purpose_text]
    if purposes:
        return purposes[:_MERGE_KEY_MAX_PURPOSES]
    return await _payee_window_purposes(session, household_id, contracts)


async def _derive_merge_contract_key(
    session: AsyncSession, household_id: UUID, contracts: Sequence[Contract]
) -> tuple[str, ContractKeySource] | None:
    """Der gemeinsame Vertragsschluessel der beteiligten Buchungen - oder None.

    Rein deterministisch und ueber dieselbe Extraktion, mit der die Erkennung
    einen neuen Vertrag anlegt; kein Sprachmodell (Abschnitt 18, Leitsatz 8).

    Geschrieben wird nur ein EINDEUTIGES Ergebnis. Liefern die Buchungen zwei
    verschiedene Referenzen - zwei Vertraege desselben Anbieters, Festnetz
    `DE000200000100000000000000001234567` gegen Mobilfunk
    `DE000200000100000000000000007654321` -, ist die Zusammenfuehrung fachlich
    fragwuerdig. Der Nutzer hat sie bestaetigt, sie laeuft also durch, aber der
    Schluessel bleibt leer.
    """
    # Lazy, weil `contract_detection_service` seinerseits dieses Modul
    # importiert - dasselbe Muster wie in `rules_service`.
    from app.services.contract_detection_service import (
        extract_contract_key,
        extract_mandate_key,
    )

    purposes = await _merge_key_purposes(session, household_id, contracts)
    if not purposes:
        return None

    mandate_keys = {key for key in (extract_mandate_key(item) for item in purposes) if key}
    if len(mandate_keys) > 1:
        return None
    if len(mandate_keys) == 1:
        return mandate_keys.pop(), "mandate"

    # Kein bekanntes Muster in irgendeinem Zweck: die generische Ableitung
    # bleibt zulaessig, weil sie die laengste Zeichenkette liefert, die in
    # JEDEM der Verwendungszwecke steht - Eindeutigkeit ist ihr eingebaut. Aus
    # dem Musterzweig kann sie hier nichts mehr melden (dieselben Muster wie
    # oben); die Pruefung steht trotzdem da, damit dieser Zweig nicht
    # stillschweigend etwas anderes zurueckgibt, wenn sich die Erkennung
    # aendert.
    derived = extract_contract_key(purposes)
    if derived is None or derived[1] != "purpose_pattern":
        return None
    return derived


async def _merge_contract_key_update(
    session: AsyncSession, household_id: UUID, target: Contract, sources: Sequence[Contract]
) -> tuple[str, ContractKeySource] | None:
    """Welchen Vertragsschluessel darf diese Zusammenfuehrung setzen?

    Der Nutzen ist gross: mit Schluessel steigt `identity_score` im Abgleich von
    0.60 auf 1.00, und erst dann wird die naechste Preisaenderung ueberhaupt als
    Treffer akzeptiert statt als "Zahlung ausgeblieben" gemeldet
    (`contract_matching_service.classify_hit` verlangt 0.80). Entsprechend
    vorsichtig sind die Sperren.
    """
    if target.contract_key:
        # Ein vorhandener Schluessel bleibt stehen. Fuer
        # `contract_key_source = 'manual'` verlangt das Leitsatz 5
        # ausdruecklich; fuer einen abgeleiteten gilt es aus Vorsicht ebenso -
        # er stammt aus Erkennung oder Reparaturlauf und kennt seinen Beleg
        # besser als dieser Merge.
        return None
    if any(item.contract_key and item.contract_key_source == "manual" for item in sources):
        # Der Nutzer hat den Schluessel einer Quelle von Hand gesetzt. Eine
        # automatische Ableitung darf dagegen nicht antreten (Leitsatz 5). Ihn
        # zu uebernehmen ist eine eigene Nutzeraktion ("Vertragsschluessel
        # korrigieren") und kein Nebeneffekt des Zusammenfuehrens.
        return None

    derived = await _derive_merge_contract_key(session, household_id, [target, *sources])
    if derived is None:
        return None

    # Ein Schluessel benennt genau einen Vertrag. Traegt ihn im Haushalt bereits
    # ein unbeteiligter, sichtbarer Vertrag, behaupteten nach dem Schreiben zwei
    # Vertraege dieselbe Identitaet von 1.00 - der Abgleich haette dann eine
    # echte Mehrdeutigkeit statt einer Verbesserung.
    taken = (
        await session.execute(
            text(
                "select 1 from contracts where household_id = :household_id "
                "and contract_key = :contract_key and merged_into_id is null "
                "and not (id = any(cast(:ids as uuid[]))) limit 1"
            ),
            {
                "household_id": str(household_id),
                "contract_key": derived[0],
                "ids": [str(target.id), *(str(item.id) for item in sources)],
            },
        )
    ).first()
    if taken is not None:
        return None
    return derived


async def apply_merge(
    session: AsyncSession,
    household_id: UUID,
    target_id: UUID,
    source_ids: list[UUID],
    request_id: str,
    *,
    actor_user_id: UUID | None = None,
) -> tuple[Contract, int, int, UUID]:
    """Fuehrt Quellvertraege in den Zielvertrag ueber - reversibel.

    Reihenfolge ist wichtig, weil zwei Unique-Indizes im Weg stehen:
    `contract_occurrences (contract_id, due_date)` und die eine offene Stufe je
    Vertrag in `contract_amount_history`.

    Anders als vor Phase 5 werden die Quellvertraege nicht mehr geloescht,
    sondern nur ausgeblendet (`merged_into_id`), und jede beruehrte Zeile wird
    vorher in `contract_merge_changes` gesichert - siehe `apply_undo` und
    Abschnitt 19.5/19.7 des Plans.
    """
    target, sources = await _load_merge_partners(session, household_id, target_id, source_ids)
    source_id_strings = [str(item.id) for item in sources]
    all_ids = [str(target_id), *source_id_strings]
    params: dict[str, Any] = {
        "household_id": str(household_id),
        "target": str(target_id),
        "sources": source_id_strings,
    }

    operation_id = await _create_merge_operation(
        session, household_id, target_id, source_id_strings, request_id, actor_user_id
    )

    # 0. Vorher-Snapshot ALLER beruehrbaren Zeilen, bevor irgendetwas
    #    mutiert. Occurrences und die Preistreppe werden unten fuer Ziel UND
    #    Quellen gemeinsam neu aufgebaut (die Treppe kann jede Stufe des
    #    Ziels verschieben, nicht nur die der Quellen) - ein feldweises Delta
    #    muesste denselben Umfang mitfuehren, ein Vollsnapshot ist die
    #    einfachere, robustere Variante (Plan Abschnitt 19.7).
    contract_rows = (
        await session.execute(
            text(
                f"select {_SNAPSHOT_COLUMNS} from contracts "
                "where household_id = :household_id and id = any(cast(:ids as uuid[]))"
            ),
            {"household_id": str(household_id), "ids": all_ids},
        )
    ).all()
    await _snapshot_rows(session, operation_id, household_id, "contract", contract_rows)

    occurrence_rows = (
        await session.execute(
            text(
                "select * from contract_occurrences "
                "where household_id = :household_id and contract_id = any(cast(:ids as uuid[]))"
            ),
            {"household_id": str(household_id), "ids": all_ids},
        )
    ).all()
    await _snapshot_rows(session, operation_id, household_id, "occurrence", occurrence_rows)

    history_rows = (
        await session.execute(
            text(
                "select * from contract_amount_history "
                "where household_id = :household_id and contract_id = any(cast(:ids as uuid[]))"
            ),
            {"household_id": str(household_id), "ids": all_ids},
        )
    ).all()
    await _snapshot_rows(session, operation_id, household_id, "history", history_rows)

    # Nur die Ereignisse der QUELLEN: das sind die einzigen, die der Merge
    # verschiebt. Die Ereignisse des Ziels bleiben unberuehrt und duerfen bei
    # einem spaeteren Undo nicht angefasst werden (append-only Log).
    event_rows = (
        await session.execute(
            text(
                "select * from contract_events "
                "where household_id = :household_id and contract_id = any(cast(:sources as uuid[]))"
            ),
            params,
        )
    ).all()
    await _snapshot_rows(session, operation_id, household_id, "event", event_rows)

    # 0b. Vertragsschluessel aus den beteiligten Buchungen ableiten, SOLANGE die
    #     Zuordnung noch der Vorher-Stand ist: Schritt 1 loescht kollidierende
    #     Faelligkeiten und mit ihnen den Bezug auf einzelne Buchungen.
    #     Rein deterministisch (Abschnitt 18); geschrieben wird erst in
    #     Schritt 4, und nur wenn das Ziel noch keinen Schluessel hat. Der
    #     Vorher-Wert beider Spalten steht bereits im Journal oben - sie sind
    #     Teil von _SNAPSHOT_COLUMNS und damit von
    #     _RESTORABLE_CONTRACT_COLUMNS, das Undo setzt sie also zurueck.
    derived_key = await _merge_contract_key_update(session, household_id, target, sources)
    new_contract_key = derived_key[0] if derived_key is not None else None
    new_contract_key_source = derived_key[1] if derived_key is not None else None

    # 1. Faelligkeitskollisionen aufloesen. Ein bestaetigter Treffer schlaegt
    #    eine offene oder ausgebliebene Faelligkeit derselben Periode.
    await session.execute(
        text(
            "delete from contract_occurrences t "
            "where t.household_id = :household_id and t.contract_id = :target "
            "and t.status <> 'matched' and exists ("
            "  select 1 from contract_occurrences s "
            "  where s.contract_id = any(cast(:sources as uuid[])) "
            "    and s.due_date = t.due_date and s.status = 'matched')"
        ),
        params,
    )
    await session.execute(
        text(
            "delete from contract_occurrences s "
            "where s.household_id = :household_id "
            "and s.contract_id = any(cast(:sources as uuid[])) and exists ("
            "  select 1 from contract_occurrences t "
            "  where t.contract_id = :target and t.due_date = s.due_date)"
        ),
        params,
    )
    # Kollisionen ZWISCHEN den Quellen. Der Schritt davor sieht nur das Ziel;
    # werden zwei oder mehr Quellen zusammengefuehrt, die fuer dieselbe Periode
    # je eine Zeile fuehren (bei erkannten Doppelvertraegen der Normalfall: die
    # Buchung gehoert per contract_occurrences_transaction_uniq genau einem,
    # alle anderen bekommen 'missing'), verletzte das UPDATE unten sonst
    # contract_occurrences_due_uniq und die gesamte Zusammenfuehrung schluege
    # mit HTTP 500 fehl. Behalten wird je Faelligkeitsdatum die aussagekraeftigste
    # Zeile: ein Treffer schlaegt eine vom Nutzer uebersprungene Periode, und die
    # schlaegt eine ausgebliebene Zahlung.
    await session.execute(
        text(
            "delete from contract_occurrences x using ("
            "  select id, row_number() over ("
            "    partition by due_date order by "
            "      case status when 'matched' then 0 when 'skipped' then 1 else 2 end, "
            "      created_at asc, id asc) as rn "
            "  from contract_occurrences "
            "  where household_id = :household_id "
            "  and contract_id = any(cast(:sources as uuid[]))"
            ") ranked where x.id = ranked.id and ranked.rn > 1"
        ),
        params,
    )
    moved_occurrences = cast(
        CursorResult[Any],
        await session.execute(
            text(
                "update contract_occurrences set contract_id = :target "
                "where household_id = :household_id "
                "and contract_id = any(cast(:sources as uuid[]))"
            ),
            params,
        ),
    ).rowcount

    # 2. Alle offenen Preisstufen voruebergehend schliessen, damit der partielle
    #    Unique-Index beim Verschieben nicht greift.
    await session.execute(
        text(
            "update contract_amount_history set valid_to = valid_from "
            "where household_id = :household_id and valid_to is null "
            "and (contract_id = :target or contract_id = any(cast(:sources as uuid[])))"
        ),
        params,
    )
    moved_history = cast(
        CursorResult[Any],
        await session.execute(
            text(
                "update contract_amount_history set contract_id = :target "
                "where household_id = :household_id "
                "and contract_id = any(cast(:sources as uuid[]))"
            ),
            params,
        ),
    ).rowcount

    # 3. Treppe neu bilden: nach Gueltigkeitsbeginn sortieren, jede Stufe endet
    #    einen Tag vor der naechsten, die letzte bleibt offen.
    ladder = (
        await session.execute(
            text(
                "select id, amount_minor, valid_from from contract_amount_history "
                "where household_id = :household_id and contract_id = :target "
                "order by valid_from asc, created_at asc"
            ),
            {"household_id": str(household_id), "target": str(target_id)},
        )
    ).all()

    previous_amount: int | None = None
    for index, row in enumerate(ladder):
        is_last = index == len(ladder) - 1
        # Nie unter den eigenen Beginn: die Uebernahme des Altbestands setzt
        # `valid_from` fuer JEDEN uebernommenen Vertrag auf contracts.created_at
        # (0001_schema.sql), zwei Altvertraege desselben Anbieters starten also
        # am selben Tag. `valid_from - 1` waere dort kleiner als `valid_from`
        # und die Zusammenfuehrung liefe in den CHECK - genau bei dem Fall,
        # fuer den der Duplikatvorschlag gedacht ist. Die Stufe bleibt dann als
        # Eintagsstufe stehen: die Treppe ist damit lesbar, und wann der Preis
        # wirklich gewechselt hat, weiss aus solchen Daten ohnehin niemand.
        valid_to = (
            None
            if is_last
            else max(row.valid_from, ladder[index + 1].valid_from - timedelta(days=1))
        )
        delta = None if previous_amount is None else int(row.amount_minor) - previous_amount
        await session.execute(
            text(
                "update contract_amount_history set valid_to = :valid_to, delta_minor = :delta "
                "where id = :id"
            ),
            {"id": str(row.id), "valid_to": valid_to, "delta": delta},
        )
        previous_amount = int(row.amount_minor)

    # Sicherheitsnetz: eine Stufe, die vor ihrem eigenen Beginn endet, ist kein
    # Zeitraum mehr. Die Treppe oben erzeugt sie nicht mehr (siehe max()), eine
    # aus frueheren Laeufen stehengebliebene Zeile wuerde hier verschwinden.
    await session.execute(
        text(
            "delete from contract_amount_history "
            "where household_id = :household_id and contract_id = :target "
            "and valid_to is not null and valid_to < valid_from"
        ),
        {"household_id": str(household_id), "target": str(target_id)},
    )

    await session.execute(
        text(
            "update contract_events set contract_id = :target "
            "where household_id = :household_id and contract_id = any(cast(:sources as uuid[]))"
        ),
        params,
    )

    # 4. Zielvertrag auf den zusammengefuehrten Stand bringen.
    latest = (
        await session.execute(
            text(
                "select amount_minor from contract_amount_history "
                "where household_id = :household_id and contract_id = :target "
                "and valid_to is null"
            ),
            {"household_id": str(household_id), "target": str(target_id)},
        )
    ).first()

    await session.execute(
        text(
            "update contracts set "
            "amount_minor = coalesce(:amount_minor, amount_minor), "
            "first_seen_date = least(first_seen_date, :first_seen), "
            "last_seen_date = greatest(last_seen_date, :last_seen), "
            # Setzen, nie ueberschreiben. Die Entscheidung faellt in
            # _merge_contract_key_update; der Ausdruck haelt sie auch dann
            # ein, wenn zwischen Ableitung und Schreiben etwas dazwischenkaeme.
            # Die rechte Seite eines UPDATE sieht immer den ALTEN Zeilenstand,
            # beide Spalten entscheiden also anhand desselben `contract_key`.
            "contract_key = coalesce(contract_key, cast(:contract_key as text)), "
            "contract_key_source = case when contract_key is null "
            "  then coalesce(cast(:contract_key_source as text), contract_key_source) "
            "  else contract_key_source end, "
            "missed_periods = 0 "
            "where id = :target and household_id = :household_id"
        ),
        {
            "household_id": str(household_id),
            "target": str(target_id),
            "amount_minor": int(latest.amount_minor) if latest is not None else None,
            "contract_key": new_contract_key,
            "contract_key_source": new_contract_key_source,
            "first_seen": min(
                [item.first_seen_date for item in [target, *sources] if item.first_seen_date],
                default=None,
            ),
            "last_seen": max(
                [item.last_seen_date for item in [target, *sources] if item.last_seen_date],
                default=None,
            ),
        },
    )

    await record_event(
        session,
        household_id,
        target_id,
        "merged",
        payload={
            "request_id": request_id,
            "source_ids": source_id_strings,
            "source_names": [item.name for item in sources],
            "merge_id": str(operation_id),
            # Nachvollziehbarkeit des Schluessels: `null` heisst hier
            # ausdruecklich "aus den beteiligten Buchungen war keine
            # eindeutige Anbieterreferenz ableitbar" und nicht "nicht
            # geprueft".
            "contract_key": new_contract_key,
            "contract_key_source": new_contract_key_source,
        },
    )

    # Weiche Zusammenfuehrung statt Loeschen (Abschnitt 19.5/19.7): die
    # Quellen bleiben als Zeile bestehen, verschwinden aber aus jeder
    # normalen Liste und jedem Abgleichslauf (load_contract, list_contracts,
    # contract_matching_service) und sind ueber require_contract/PATCH nicht
    # mehr erreichbar. Das macht `apply_undo` moeglich, ohne Daten zu kopieren.
    await session.execute(
        text(
            "update contracts set merged_into_id = :target, merged_at = now() "
            "where household_id = :household_id and id = any(cast(:sources as uuid[]))"
        ),
        params,
    )

    merged = await require_contract(session, household_id, target_id)

    # Erst JETZT bekannt: wie oft/ob der eigene Merge-Update die Version
    # bewegt hat (siehe Kommentar an target_version_after in der Migration).
    await session.execute(
        text(
            "update contract_merge_operations set target_version_after = :version "
            "where id = :id"
        ),
        {"version": merged.version, "id": str(operation_id)},
    )

    return merged, moved_occurrences, moved_history, operation_id


# ---------------------------------------------------------------------------
# Zusammenfuehren rueckgaengig machen (Abschnitt 19.7)
# ---------------------------------------------------------------------------


async def _require_merge_operation(
    session: AsyncSession, household_id: UUID, merge_id: UUID
) -> Any:
    result = await session.execute(
        text(
            "select id, target_id, source_ids, request_id, status, "
            "target_version_before, target_version_after, created_at, undone_at "
            "from contract_merge_operations where id = :id and household_id = :household_id"
        ),
        {"id": str(merge_id), "household_id": str(household_id)},
    )
    row = result.first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=CONTRACT_MERGE_OPERATION_NOT_FOUND
        )
    return row


async def _merge_operation_conflicts(
    session: AsyncSession, household_id: UUID, operation: Any
) -> tuple[list[str], int]:
    """Konfliktgruende plus Anzahl neuer Faelligkeiten seit dem Merge.

    Eine Faelligkeit gilt als "neu", wenn ihre `id` nicht im Snapshot der
    Operation steht - ein spaeterer Abgleichslauf legt bestehende Zeilen per
    `on conflict ... do update` an (Abschnitt 5.4), eine wirklich neue
    Faelligkeit bekommt also zwangslaeufig eine neue `id`. Der Vergleich
    braucht deshalb keine Zeitstempel und ist robust gegen einen Uhrenversatz
    zwischen Merge und Abgleich.
    """
    reasons: list[str] = []

    if operation.status == "undone":
        reasons.append("already_undone")
        return reasons, 0

    target = await load_contract(session, household_id, UUID(str(operation.target_id)))
    if target is None:
        reasons.append("target_missing")
        return reasons, 0

    # target_version_after ist die Version unmittelbar NACH dem Merge (siehe
    # apply_merge) - robust dagegen, dass der Merge selbst die Version 0-mal
    # statt genau 1-mal bumpt (least()/greatest() auf bislang NULLen aendern
    # nichts, der Versionstrigger bumpt nur bei echter Differenz).
    if operation.target_version_after is None or target.version != int(
        operation.target_version_after
    ):
        reasons.append("target_changed_since_merge")

    new_occurrences = (
        await session.execute(
            text(
                "select count(*) from contract_occurrences o "
                "where o.household_id = :household_id and o.contract_id = :target "
                "and not exists ("
                "  select 1 from contract_merge_changes c "
                "  where c.operation_id = :operation_id and c.entity_type = 'occurrence' "
                "  and c.entity_id = o.id"
                ")"
            ),
            {
                "household_id": str(household_id),
                "target": str(operation.target_id),
                "operation_id": str(operation.id),
            },
        )
    ).scalar_one()
    if new_occurrences:
        reasons.append("new_activity_since_merge")

    return reasons, int(new_occurrences)


async def build_undo_preview(
    session: AsyncSession, household_id: UUID, merge_id: UUID
) -> ContractMergeUndoPreview:
    operation = await _require_merge_operation(session, household_id, merge_id)
    conflicts, new_occurrence_count = await _merge_operation_conflicts(
        session, household_id, operation
    )

    target_row = (
        await session.execute(
            text("select name from contracts where id = :id and household_id = :household_id"),
            {"id": str(operation.target_id), "household_id": str(household_id)},
        )
    ).first()
    source_ids = [UUID(str(item)) for item in operation.source_ids]
    source_rows = (
        await session.execute(
            text(
                "select id, name from contracts "
                "where household_id = :household_id and id = any(cast(:ids as uuid[]))"
            ),
            {"household_id": str(household_id), "ids": [str(item) for item in source_ids]},
        )
    ).all()
    source_names_by_id = {UUID(str(row.id)): row.name for row in source_rows}

    return ContractMergeUndoPreview(
        merge_id=UUID(str(operation.id)),
        target_id=UUID(str(operation.target_id)),
        target_name=target_row.name if target_row is not None else "",
        source_ids=source_ids,
        source_names=[source_names_by_id.get(item, "") for item in source_ids],
        already_undone=operation.status == "undone",
        can_undo=not conflicts,
        conflict_reasons=cast(Any, conflicts),
        new_occurrence_count=new_occurrence_count,
        merged_at=operation.created_at,
    )


async def _restore_snapshot_rows(
    session: AsyncSession, household_id: UUID, operation_id: UUID, entity_type: str
) -> int:
    """Loescht die aktuellen Zeilen mit den im Journal gesicherten IDs und
    fuegt sie unveraendert wieder ein - gezielt ueber die Snapshot-IDs, damit
    eine seither entstandene, unbeteiligte Zeile (z. B. eine neue Faelligkeit
    des Ziels) nicht versehentlich mitgeloescht wird."""
    table = _MERGE_SNAPSHOT_TABLES[entity_type]
    result = await session.execute(
        text(
            "delete from "
            + table
            + " where household_id = :household_id and id in ("
            "  select entity_id from contract_merge_changes "
            "  where operation_id = :operation_id and entity_type = :entity_type"
            ")"
        ),
        {
            "household_id": str(household_id),
            "operation_id": str(operation_id),
            "entity_type": entity_type,
        },
    )
    deleted = cast(CursorResult[Any], result).rowcount

    if entity_type == "occurrence":
        insert_sql = (
            "insert into contract_occurrences "
            "(id, household_id, contract_id, due_date, status, transaction_id, booked_date, "
            "amount_minor, amount_delta_minor, days_delta, match_score, matched_at, created_at) "
            "select (p.prior_data->>'id')::uuid, (p.prior_data->>'household_id')::uuid, "
            "(p.prior_data->>'contract_id')::uuid, (p.prior_data->>'due_date')::date, "
            "p.prior_data->>'status', (p.prior_data->>'transaction_id')::uuid, "
            "(p.prior_data->>'booked_date')::date, (p.prior_data->>'amount_minor')::bigint, "
            "(p.prior_data->>'amount_delta_minor')::bigint, (p.prior_data->>'days_delta')::int, "
            "(p.prior_data->>'match_score')::numeric, (p.prior_data->>'matched_at')::timestamptz, "
            "(p.prior_data->>'created_at')::timestamptz "
            "from contract_merge_changes p "
            "where p.operation_id = :operation_id and p.entity_type = 'occurrence'"
        )
    elif entity_type == "history":
        insert_sql = (
            "insert into contract_amount_history "
            "(id, household_id, contract_id, amount_minor, valid_from, valid_to, delta_minor, "
            "change_reason, source_transaction_id, created_at) "
            "select (p.prior_data->>'id')::uuid, (p.prior_data->>'household_id')::uuid, "
            "(p.prior_data->>'contract_id')::uuid, (p.prior_data->>'amount_minor')::bigint, "
            "(p.prior_data->>'valid_from')::date, (p.prior_data->>'valid_to')::date, "
            "(p.prior_data->>'delta_minor')::bigint, p.prior_data->>'change_reason', "
            "(p.prior_data->>'source_transaction_id')::uuid, "
            "(p.prior_data->>'created_at')::timestamptz "
            "from contract_merge_changes p "
            "where p.operation_id = :operation_id and p.entity_type = 'history'"
        )
    else:
        insert_sql = (
            "insert into contract_events "
            "(id, household_id, contract_id, kind, occurred_on, payload, acknowledged_at, "
            "notified_at, created_at) "
            "select (p.prior_data->>'id')::uuid, (p.prior_data->>'household_id')::uuid, "
            "(p.prior_data->>'contract_id')::uuid, p.prior_data->>'kind', "
            "(p.prior_data->>'occurred_on')::date, coalesce(p.prior_data->'payload', '{}'::jsonb), "
            "(p.prior_data->>'acknowledged_at')::timestamptz, "
            "(p.prior_data->>'notified_at')::timestamptz, "
            "(p.prior_data->>'created_at')::timestamptz "
            "from contract_merge_changes p "
            "where p.operation_id = :operation_id and p.entity_type = 'event'"
        )

    await session.execute(text(insert_sql), {"operation_id": str(operation_id)})
    return deleted



# Postgres-Zieltyp je wiederherstellbarer Spalte - dieselbe Reihenfolge/Menge
# wie _RESTORABLE_CONTRACT_COLUMNS. jsonb->>'spalte' liefert immer text, jede
# Spalte braucht deshalb ihren eigenen expliziten Cast.
_CONTRACT_COLUMN_TYPES: Final[dict[str, str]] = {
    "name": "text",
    "provider_label": "text",
    "contract_type": "text",
    "payee_id": "uuid",
    "account_id": "uuid",
    "category_id": "uuid",
    "contract_key": "text",
    "contract_key_source": "text",
    "direction": "text",
    "amount_minor": "bigint",
    "frequency": "text",
    "due_rule": "text",
    "due_day": "int",
    "next_due_date": "date",
    "first_seen_date": "date",
    "last_seen_date": "date",
    "tolerance_days": "int",
    "tolerance_amount_minor": "bigint",
    "status": "text",
    "cancelled_on": "date",
    "ends_on": "date",
    "min_term_months": "int",
    "cancellation_period_days": "int",
    "starts_on": "date",
    "renewal_term_months": "int",
    "provider_template_key": "text",
    "installments_total": "int",
    "installments_paid": "int",
    "source": "text",
    "detection_confidence": "numeric",
    "missed_periods": "int",
    "last_matched_at": "timestamptz",
    "note": "text",
    "reserve_enabled": "boolean",
    "reserve_started_on": "date",
    "reserve_starting_amount_minor": "bigint",
    "income_kind": "text",
    "person_id": "uuid",
    "legacy_recurring_rule_id": "uuid",
    "legacy_subscription_id": "uuid",
    "merged_into_id": "uuid",
    "merged_at": "timestamptz",
}


async def _restore_contract_snapshots(
    session: AsyncSession, household_id: UUID, operation_id: UUID
) -> None:
    """Schreibt die veraenderlichen Felder von Ziel- UND Quellvertraegen aus
    dem Snapshot zurueck. Fuer die Quellen heisst das insbesondere:
    `merged_into_id`/`merged_at` kehren auf `null` zurueck - sie sind damit
    automatisch wieder normal sichtbar, ohne Sonderfall im Code."""
    assignments = ", ".join(
        f"{column} = (p.prior_data->>'{column}')::{_CONTRACT_COLUMN_TYPES[column]}"
        for column in _RESTORABLE_CONTRACT_COLUMNS
    )
    await session.execute(
        text(
            f"update contracts set {assignments} "
            "from contract_merge_changes p "
            "where p.operation_id = :operation_id and p.entity_type = 'contract' "
            "and contracts.id = p.entity_id and contracts.household_id = :household_id"
        ),
        {"operation_id": str(operation_id), "household_id": str(household_id)},
    )


async def apply_undo(
    session: AsyncSession,
    household_id: UUID,
    merge_id: UUID,
    *,
    actor_user_id: UUID | None,
) -> tuple[Contract, int]:
    operation = await _require_merge_operation(session, household_id, merge_id)

    if operation.status == "undone":
        # Idempotent: der Zustand ist bereits wiederhergestellt, es gibt
        # nichts erneut zu mutieren.
        return await require_contract(session, household_id, UUID(str(operation.target_id))), 0

    conflicts, _ = await _merge_operation_conflicts(session, household_id, operation)
    if conflicts:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=CONTRACT_MERGE_UNDO_CONFLICT
        )

    await _restore_contract_snapshots(session, household_id, operation.id)
    await _restore_snapshot_rows(session, household_id, operation.id, "occurrence")
    await _restore_snapshot_rows(session, household_id, operation.id, "history")
    await _restore_snapshot_rows(session, household_id, operation.id, "event")

    await session.execute(
        text(
            "update contract_merge_operations set status = 'undone', undone_at = now(), "
            "undone_by = :actor where id = :id"
        ),
        {"id": str(operation.id), "actor": str(actor_user_id) if actor_user_id else None},
    )

    source_ids = [UUID(str(item)) for item in operation.source_ids]
    await record_event(
        session,
        household_id,
        UUID(str(operation.target_id)),
        "merge_undone",
        payload={
            "operation_id": str(operation.id),
            "source_ids": [str(item) for item in source_ids],
        },
    )

    restored = await require_contract(session, household_id, UUID(str(operation.target_id)))
    return restored, len(source_ids)


# ---------------------------------------------------------------------------
# Manuelle Buchungszuordnung ohne Merge (Abschnitt 19.7)
# ---------------------------------------------------------------------------

CONTRACT_TRANSACTION_NOT_FOUND: Final = "contract_transaction_not_found"


async def _load_transaction_for_reassign(
    session: AsyncSession, household_id: UUID, transaction_id: UUID
) -> Any:
    result = await session.execute(
        text(
            "select id, booked_date, amount_minor, "
            "coalesce(payee_label, payee_raw) as payee_label "
            "from transactions where id = :id and household_id = :household_id "
            "and not is_archived"
        ),
        {"id": str(transaction_id), "household_id": str(household_id)},
    )
    row = result.first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=CONTRACT_TRANSACTION_NOT_FOUND
        )
    return row


async def _current_occurrence_contract(
    session: AsyncSession, household_id: UUID, transaction_id: UUID
) -> Any:
    result = await session.execute(
        text(
            "select o.contract_id, c.name from contract_occurrences o "
            "join contracts c on c.id = o.contract_id and c.household_id = o.household_id "
            "where o.household_id = :household_id and o.transaction_id = :transaction_id"
        ),
        {"household_id": str(household_id), "transaction_id": str(transaction_id)},
    )
    return result.first()


async def build_reassign_preview(
    session: AsyncSession,
    household_id: UUID,
    transaction_id: UUID,
    target_contract_id: UUID | None,
) -> ContractReassignPreview:
    tx = await _load_transaction_for_reassign(session, household_id, transaction_id)
    current = await _current_occurrence_contract(session, household_id, transaction_id)

    target_name: str | None = None
    if target_contract_id is not None:
        target = await require_contract(session, household_id, target_contract_id)
        target_name = target.name

    return ContractReassignPreview(
        transaction_id=transaction_id,
        booked_date=tx.booked_date,
        amount_minor=int(tx.amount_minor),
        payee_label=tx.payee_label,
        current_contract_id=UUID(str(current.contract_id)) if current is not None else None,
        current_contract_name=current.name if current is not None else None,
        target_contract_id=target_contract_id,
        target_contract_name=target_name,
        affects_occurrence=current is not None,
    )


async def apply_reassign(
    session: AsyncSession,
    household_id: UUID,
    transaction_id: UUID,
    target_contract_id: UUID | None,
    *,
    actor_user_id: UUID | None,
) -> UUID:
    """Ordnet eine Buchung verbindlich einem bestehenden Vertrag zu, loest sie
    ausdruecklich von jedem Vertrag, oder haengt sie um.

    Schreibt zuerst den Override (bindend fuer Erkennung, Reparaturlauf und
    Rescan - Abschnitt 19.6, Leitsatz 5), raeumt dann die alte Faelligkeit ab
    und legt bei einer Zielzuordnung sofort die neue an, statt auf den
    naechsten Abgleichslauf zu warten: der Nutzer hat die Entscheidung gerade
    getroffen und soll sie sofort am Vertrag sehen.
    """
    tx = await _load_transaction_for_reassign(session, household_id, transaction_id)
    if target_contract_id is not None:
        # 404, falls falscher Haushalt oder bereits zusammengefuehrt.
        await require_contract(session, household_id, target_contract_id)

    override_row = (
        await session.execute(
            text(
                "insert into contract_assignment_overrides "
                "(household_id, transaction_id, contract_id, set_by) "
                "values (:household_id, :transaction_id, :contract_id, :actor) "
                "on conflict (household_id, transaction_id) do update set "
                "contract_id = excluded.contract_id, set_by = excluded.set_by, "
                "updated_at = now() "
                "returning id"
            ),
            {
                "household_id": str(household_id),
                "transaction_id": str(transaction_id),
                "contract_id": str(target_contract_id) if target_contract_id else None,
                "actor": str(actor_user_id) if actor_user_id else None,
            },
        )
    ).first()
    assert override_row is not None

    # Alte Zuordnung loesen, unabhaengig davon, wo sie gerade sitzt.
    await session.execute(
        text(
            "delete from contract_occurrences "
            "where household_id = :household_id and transaction_id = :transaction_id"
        ),
        {"household_id": str(household_id), "transaction_id": str(transaction_id)},
    )

    if target_contract_id is not None:
        await session.execute(
            text(
                "insert into contract_occurrences "
                "(household_id, contract_id, due_date, status, transaction_id, booked_date, "
                "amount_minor, matched_at) "
                "values (:household_id, :contract_id, :due_date, 'matched', :transaction_id, "
                ":booked_date, :amount_minor, now()) "
                "on conflict (contract_id, due_date) do update set "
                "status = 'matched', transaction_id = excluded.transaction_id, "
                "booked_date = excluded.booked_date, amount_minor = excluded.amount_minor, "
                "matched_at = now()"
            ),
            {
                "household_id": str(household_id),
                "contract_id": str(target_contract_id),
                "due_date": tx.booked_date,
                "transaction_id": str(transaction_id),
                "booked_date": tx.booked_date,
                # MIT Originalvorzeichen - dieselbe Festlegung wie in
                # `contract_matching_service._write_occurrence`. Vorzeichenlos
                # geschrieben stuende eine von Hand zugeordnete Ausgabe in der
                # Faelligkeitsliste neben den erkannten als vermeintliche
                # Einnahme.
                "amount_minor": int(tx.amount_minor),
            },
        )
        await session.execute(
            text(
                "update contracts set last_seen_date = greatest(last_seen_date, :booked_date), "
                "missed_periods = 0 where id = :id and household_id = :household_id"
            ),
            {
                "booked_date": tx.booked_date,
                "id": str(target_contract_id),
                "household_id": str(household_id),
            },
        )

    return UUID(str(override_row.id))


# ---------------------------------------------------------------------------
# Einstellungen
# ---------------------------------------------------------------------------

_DEFAULT_AUTO_END: Final = "3"


async def get_settings(session: AsyncSession, household_id: UUID) -> ContractSettings:
    result = await session.execute(
        text(
            "select auto_end_after_missed, notify_price_change, notify_payment_missing, "
            "notify_term_ending from contract_settings where household_id = :household_id"
        ),
        {"household_id": str(household_id)},
    )
    row = result.first()
    if row is None:
        return ContractSettings(auto_end_after_missed=cast(Any, _DEFAULT_AUTO_END))
    return ContractSettings(
        auto_end_after_missed=cast(Any, row.auto_end_after_missed),
        notify_price_change=bool(row.notify_price_change),
        notify_payment_missing=bool(row.notify_payment_missing),
        notify_term_ending=bool(row.notify_term_ending),
    )


async def put_settings(
    session: AsyncSession, household_id: UUID, settings: ContractSettingsUpdate
) -> ContractSettings:
    """Schreibt die Einstellungen vollstaendig.

    Die drei `notify_*`-Spalten legt Migration 0015 an und der Worker liest sie
    im Sendefilter; ohne diesen Schreibpfad staenden sie dauerhaft auf ihrem
    Default und die Auswahl waere nur auf dem Papier vorhanden.
    """
    await session.execute(
        text(
            "insert into contract_settings (household_id, auto_end_after_missed, "
            "notify_price_change, notify_payment_missing, notify_term_ending) "
            "values (:household_id, :value, :price_change, :payment_missing, :term_ending) "
            "on conflict (household_id) do update set auto_end_after_missed = :value, "
            "notify_price_change = :price_change, "
            "notify_payment_missing = :payment_missing, "
            "notify_term_ending = :term_ending, "
            "updated_at = now()"
        ),
        {
            "household_id": str(household_id),
            "value": settings.auto_end_after_missed,
            "price_change": settings.notify_price_change,
            "payment_missing": settings.notify_payment_missing,
            "term_ending": settings.notify_term_ending,
        },
    )
    return ContractSettings(
        auto_end_after_missed=settings.auto_end_after_missed,
        notify_price_change=settings.notify_price_change,
        notify_payment_missing=settings.notify_payment_missing,
        notify_term_ending=settings.notify_term_ending,
    )


# ---------------------------------------------------------------------------
# Belege je Preisstufe, freie Buchungen, Neuberechnung (Abschnitt 19.7)
# ---------------------------------------------------------------------------
#
# Der Fall, an dem sich dieser Abschnitt messen laesst, steht in 19.1: der
# Festnetzvertrag hat vier zugeordnete Buchungen (15.04.-15.07.), drei aeltere
# Buchungen derselben Mandatsreferenz (15.01., 15.02., 15.03., je 49,95 EUR)
# liegen unzugeordnet daneben, und sein Betragsverlauf besteht aus zwei Zeilen,
# die beide am Uebernahmetag beginnen und keine Quellbuchung nennen. Richtig
# waere 49,95 EUR ab dem 15.01. und 39,95 EUR ab dem 15.04.
#
# Die drei Bausteine hier beantworten der Reihe nach: Was belegt eine Stufe?
# Welche Buchungen sind ueberhaupt noch frei? Und wie sieht die Treppe aus,
# nachdem der Nutzer die fehlenden Buchungen zugeordnet hat?

# Zeichen je Verwendungszweck in einer Belegliste. Die Liste dient dem
# Wiedererkennen; der vollstaendige Zweck steht in der Buchungsansicht.
_EVIDENCE_PURPOSE_CHARS: Final = 140

# Rueckblick der Vorbelegung. Deckungsgleich mit
# `contract_matching_service._BACKFILL_MAX_MONTHS`: weiter zurueck als der
# rueckwirkende Erstlauf schaut, braucht auch die Nachlese nicht zu suchen.
_FREE_LOOKBACK_MONTHS: Final = 36

# Breite des vorbelegten Betragsbandes, als Faktor auf die bekannten
# Preisstufen. Bewusst grosszuegig: die 5-%-Toleranz des Abgleichs und selbst
# das 60-%-Band einer Preisaenderung wuerden genau die Buchungen ausblenden,
# wegen derer diese Suche existiert (ein Abo faellt von 9,99 auf 2,99 EUR,
# das ist Faktor 3,3). Wer es enger will, gibt die Grenzen selbst an.
_FREE_AMOUNT_BAND_FACTOR: Final = 4

FREE_TRANSACTIONS_DEFAULT_LIMIT: Final = 25
FREE_TRANSACTIONS_MAX_LIMIT: Final = 100

# Rechtsformen taugen nicht als Suchwort: "GmbH" findet den halben Kontoauszug.
_LEGAL_FORM_TOKENS: Final = frozenset(
    {
        "ag",
        "bv",
        "co",
        "corp",
        "ev",
        "gbr",
        "gmbh",
        "inc",
        "kg",
        "kgaa",
        "llc",
        "ltd",
        "mbh",
        "nv",
        "ohg",
        "plc",
        "sarl",
        "se",
        "srl",
        "ug",
    }
)

# Kuerzere Worte sind als alleiniges Suchwort zu unspezifisch.
_MIN_SEARCH_TOKEN_CHARS: Final = 4

# Empfaenger- und Zwecktext wie im Abgleich (`_CANDIDATE_COLUMNS`): erst der
# aufgeloeste Zahlungsempfaenger, dann die Rohfelder der Buchung.
_EVIDENCE_PAYEE_TEXT: Final = (
    "coalesce(nullif(btrim(p.name), ''), nullif(btrim(t.payee_label), ''), "
    "nullif(btrim(t.payee_raw), ''))"
)
_EVIDENCE_PURPOSE_TEXT: Final = "coalesce(nullif(btrim(t.purpose_raw), ''), t.note, '')"

_ASSIGNED_TRANSACTIONS_SQL: Final = f"""
select o.due_date, t.id as transaction_id, t.booked_date, t.amount_minor,
       t.account_id, {_EVIDENCE_PAYEE_TEXT} as payee_label,
       {_EVIDENCE_PURPOSE_TEXT} as purpose_text
from contract_occurrences o
join transactions t on t.id = o.transaction_id and t.household_id = o.household_id
left join payees p on p.id = t.payee_id and p.household_id = t.household_id
where o.household_id = :household_id
  and o.contract_id = :contract_id
  and o.transaction_id is not null
  and not t.is_archived
order by t.booked_date asc, t.id asc
"""

# Frei heisst: an keiner Faelligkeit und von keinem Override belegt. Beide
# Bedingungen sind woertlich dieselben wie in
# `contract_matching_service.fetch_candidates` - eine zweite, leicht andere
# Formulierung waere der Weg, auf dem eine ausdruecklich geloeste Buchung hier
# wieder als Vorschlag auftaucht (Leitsatz 5).
_FREE_TRANSACTIONS_WHERE: Final = """
from transactions t
left join payees p on p.id = t.payee_id and p.household_id = t.household_id
where t.household_id = :household_id
  and not t.is_archived
  and not t.is_transfer
  and {sign_condition}
  and t.booked_date between :date_from and :date_to
  and (cast(:amount_min as bigint) is null or abs(t.amount_minor) >= :amount_min)
  and (cast(:amount_max as bigint) is null or abs(t.amount_minor) <= :amount_max)
  and (
    cast(:search as text) is null
    or coalesce(p.name, '') ilike :search_pattern
    or coalesce(t.payee_label, '') ilike :search_pattern
    or coalesce(t.payee_raw, '') ilike :search_pattern
    or coalesce(t.purpose_raw, '') ilike :search_pattern
    or coalesce(t.note, '') ilike :search_pattern
  )
  and (cast(:ids as uuid[]) is null or t.id = any(cast(:ids as uuid[])))
  and not exists (select 1 from contract_occurrences o
                  where o.household_id = :household_id and o.transaction_id = t.id)
  and not exists (select 1 from contract_assignment_overrides ov
                  where ov.household_id = :household_id and ov.transaction_id = t.id)
"""


def _excerpt(value: str | None) -> str | None:
    if not value:
        return None
    return " ".join(value.split())[:_EVIDENCE_PURPOSE_CHARS] or None


def _evidence_transaction(row: Any) -> ContractEvidenceTransaction:
    return ContractEvidenceTransaction(
        transaction_id=UUID(str(row.transaction_id)),
        booked_date=row.booked_date,
        amount_minor=int(row.amount_minor),
        payee_label=row.payee_label,
        purpose_excerpt=_excerpt(row.purpose_text),
        account_id=UUID(str(row.account_id)) if row.account_id is not None else None,
        due_date=getattr(row, "due_date", None),
    )


async def load_assigned_transactions(
    session: AsyncSession, household_id: UUID, contract_id: UUID
) -> list[ContractEvidenceTransaction]:
    """Die Buchungen, die diesem Vertrag heute zugeordnet sind, chronologisch.

    Gelesen wird ueber die Faelligkeiten, die Betraege aber aus `transactions`:
    `contract_occurrences.amount_minor` ist eine Kopie und im Altbestand nicht
    ueberall vorzeichenrichtig gepflegt. Die Buchung selbst ist die Quelle.
    """
    result = await session.execute(
        text(_ASSIGNED_TRANSACTIONS_SQL),
        {"household_id": str(household_id), "contract_id": str(contract_id)},
    )
    return [_evidence_transaction(row) for row in result]


def _stage_contains(stage: ContractAmountEntry, day: date) -> bool:
    if day < stage.valid_from:
        return False
    return stage.valid_to is None or day <= stage.valid_to


async def build_amount_evidence(
    session: AsyncSession, household_id: UUID, contract_id: UUID
) -> ContractAmountEvidenceResponse:
    """Zu jeder Preisstufe die Buchungen, die sie belegen.

    Eine Buchung zaehlt zu jeder Stufe, deren Zeitraum ihren Buchungstag
    enthaelt. Ueberlappende Stufen sind kein Konstrukt, sondern Bestand: die
    beiden uebernommenen Zeilen ueberschneiden sich am Uebernahmetag. Sie hier
    stillschweigend eindeutig zu machen, wuerde genau den Befund verstecken, den
    der Nutzer sehen soll.
    """
    await require_contract(session, household_id, contract_id)
    stages = await list_amount_history(session, household_id, contract_id)
    assigned = await load_assigned_transactions(session, household_id, contract_id)

    covered: set[UUID] = set()
    items: list[ContractAmountStageEvidence] = []
    for stage in stages:
        inside = [item for item in assigned if _stage_contains(stage, item.booked_date)]
        covered.update(item.transaction_id for item in inside)
        has_source = stage.source_transaction_id is not None
        items.append(
            ContractAmountStageEvidence(
                stage=stage,
                transactions=[
                    item.model_copy(
                        update={
                            "is_stage_source": item.transaction_id
                            == stage.source_transaction_id
                        }
                    )
                    for item in inside
                ],
                transaction_count=len(inside),
                has_source_transaction=has_source,
                has_evidence=has_source or bool(inside),
            )
        )

    return ContractAmountEvidenceResponse(
        contract_id=contract_id,
        stages=items,
        transactions_outside_stages=[
            item for item in assigned if item.transaction_id not in covered
        ],
        stages_without_evidence=sum(1 for item in items if not item.has_evidence),
        assigned_transaction_count=len(assigned),
    )


def default_search_text(label: str | None) -> str | None:
    """Das Anbieterwort, mit dem die Vorbelegung sucht.

    Ein einzelnes Wort statt des vollstaendigen Namens: ueber einen
    Zahlungsdienstleister abgerechnete Buchungen tragen als Empfaenger
    "PayPal Europe S.a.r.l. et C", der Anbieter steht nur im Verwendungszweck.
    Ein ILIKE auf "Musterpost Verlag GmbH" findet ihn dort nicht, ein ILIKE auf
    "Musterpost" schon.
    """
    if not label:
        return None
    for token in (part for part in label.replace("-", " ").split() if part):
        cleaned = "".join(char for char in token if char.isalnum())
        if len(cleaned) < _MIN_SEARCH_TOKEN_CHARS:
            continue
        if cleaned.casefold() in _LEGAL_FORM_TOKENS:
            continue
        return cleaned
    return " ".join(label.split()) or None


async def build_free_transaction_filter(
    session: AsyncSession,
    household_id: UUID,
    contract: Contract,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    amount_min_minor: int | None = None,
    amount_max_minor: int | None = None,
    search: str | None = None,
    use_contract_defaults: bool = True,
    today: date | None = None,
) -> ContractFreeTransactionFilter:
    """Mischt die Vorbelegung aus dem Vertrag mit den Angaben des Aufrufers.

    Jede ausdrueckliche Angabe gewinnt; `use_contract_defaults=False` schaltet
    die Ableitung ganz ab und laesst nur den Zeitraum stehen. Beides ist
    Absicht: die Vorbelegung ist eine Starthilfe, kein Filter, an dem die Suche
    scheitern darf - der Nutzer sucht ja gerade deshalb von Hand, weil die
    Automatik nichts gefunden hat.
    """
    day = today or date.today()
    months = day.year * 12 + (day.month - 1) - _FREE_LOOKBACK_MONTHS
    lower = date(months // 12, months % 12 + 1, 1)

    band_min: int | None = None
    band_max: int | None = None
    default_search: str | None = None
    if use_contract_defaults:
        stages = await list_amount_history(session, household_id, contract.id)
        known = {contract.amount_minor} | {stage.amount_minor for stage in stages}
        # Das Band umschliesst alle bereits bekannten Preisstufen; nur so kann
        # eine Suche die Buchungen einer Stufe finden, die im Verlauf schon
        # steht, aber keinen Beleg hat.
        band_min = max(1, min(known) // _FREE_AMOUNT_BAND_FACTOR)
        band_max = max(known) * _FREE_AMOUNT_BAND_FACTOR
        default_search = default_search_text(contract.provider_label or contract.name)

    resolved_search = search if search is not None else default_search
    cleaned_search = " ".join(resolved_search.split()) if resolved_search else None

    return ContractFreeTransactionFilter(
        date_from=date_from if date_from is not None else lower,
        date_to=date_to if date_to is not None else day,
        amount_min_minor=amount_min_minor if amount_min_minor is not None else band_min,
        amount_max_minor=amount_max_minor if amount_max_minor is not None else band_max,
        search=cleaned_search or None,
        from_contract_defaults=use_contract_defaults,
    )


def _free_params(
    household_id: UUID,
    filter_: ContractFreeTransactionFilter,
    transaction_ids: Sequence[UUID] | None,
) -> dict[str, Any]:
    return {
        "household_id": str(household_id),
        "ids": [str(item) for item in transaction_ids] if transaction_ids else None,
        "date_from": filter_.date_from,
        "date_to": filter_.date_to,
        "amount_min": filter_.amount_min_minor,
        "amount_max": filter_.amount_max_minor,
        "search": filter_.search,
        # Prozentzeichen und Unterstrich sind ILIKE-Platzhalter; ohne
        # Maskierung waere eine Suche nach "50%" eine Suche nach allem.
        "search_pattern": (
            "%" + filter_.search.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_") + "%"
            if filter_.search
            else None
        ),
    }


async def search_free_transactions(
    session: AsyncSession,
    household_id: UUID,
    contract: Contract,
    filter_: ContractFreeTransactionFilter,
    *,
    limit: int = FREE_TRANSACTIONS_DEFAULT_LIMIT,
    offset: int = 0,
    transaction_ids: Sequence[UUID] | None = None,
) -> ContractFreeTransactionsResponse:
    """Buchungen des Haushalts, die noch keinem Vertrag gehoeren.

    Die Richtung kommt aus dem Vertrag und ist nicht ueberschreibbar: eine
    Einnahme kann keine Rate eines Ausgabenvertrags sein, und ein
    Vorzeichenfehler waere hier besonders teuer, weil die Zuordnung danach
    verbindlich ist.

    `transaction_ids` schneidet die Treffermenge auf bestimmte Buchungen
    zusammen, ohne eine der Bedingungen zu lockern - der Weg, auf dem eine
    Auswahl geprueft wird, ohne die Vorfilterung zu umgehen.
    """
    sign_condition = (
        "t.amount_minor < 0" if contract.direction == "expense" else "t.amount_minor > 0"
    )
    where = _FREE_TRANSACTIONS_WHERE.format(sign_condition=sign_condition)
    params = _free_params(household_id, filter_, transaction_ids)

    total = int(
        (await session.execute(text(f"select count(*) as total {where}"), params)).scalar_one()
    )
    result = await session.execute(
        text(
            f"select t.id as transaction_id, t.booked_date, t.amount_minor, t.account_id, "
            f"{_EVIDENCE_PAYEE_TEXT} as payee_label, "
            f"{_EVIDENCE_PURPOSE_TEXT} as purpose_text {where} "
            "order by t.booked_date desc, t.id desc limit :limit offset :offset"
        ),
        {**params, "limit": limit, "offset": offset},
    )
    items = [_evidence_transaction(row) for row in result]
    return ContractFreeTransactionsResponse(
        items=items,
        total=total,
        truncated=offset + len(items) < total,
        limit=limit,
        offset=offset,
        applied_filter=filter_,
    )


# ---------------------------------------------------------------------------
# Preistreppe aus den zugeordneten Buchungen
# ---------------------------------------------------------------------------


def _representative_amount(group: Sequence[ContractEvidenceTransaction]) -> int:
    """Der Betrag, der eine Stufe beschreibt: der haeufigste, bei Gleichstand
    der juengste.

    Nicht einfach der erste Beleg. Im Anlassfall ist die erste Buchung nach der
    Preissenkung geringfuegig kleiner (anteiliger erster Monat), die drei
    folgenden sind 39,95 EUR - und 39,95 EUR ist der Preis. Auf die Treppe
    selbst hat die Wahl keinen Einfluss: alle Betraege einer Stufe liegen
    innerhalb der Toleranz ihres ersten Belegs, sonst waeren sie eine eigene
    Stufe.
    """
    counts = Counter(abs(item.amount_minor) for item in group)
    last_index = {abs(item.amount_minor): index for index, item in enumerate(group)}
    return max(counts, key=lambda amount: (counts[amount], last_index[amount]))


def plan_amount_stages(
    transactions: Sequence[ContractEvidenceTransaction],
    *,
    manual_from: date | None = None,
) -> list[ContractAmountStagePlan]:
    """Leitet die Preistreppe aus zugeordneten Buchungen ab.

    Die Stufengrenze ist woertlich dieselbe Regel wie im rueckwirkenden
    Erstlauf (`contract_matching_service._collect_backfill_hits`): eine neue
    Stufe beginnt, sobald eine Buchung weiter vom Betrag des ersten Belegs der
    laufenden Stufe entfernt liegt als dessen Toleranz. Der Unterschied ist die
    Eingangsgroesse - dort die frisch gesuchten Treffer, hier die bereits
    zugeordneten Buchungen.

    Genau dieser Unterschied ist der Zweck der Funktion. Der Erstlauf sucht sich
    seine Buchungen selbst und verlangt dafuer `identity_score >= 0.80`; im
    Anlassfall erreicht er 0,60 und lehnt die guenstigere Zahlung als
    fremd ab. Eine Neuberechnung, die erneut sucht, wuerde also ausgerechnet die
    Buchungen wieder verlieren, die der Nutzer gerade von Hand zugeordnet hat -
    und damit Leitsatz 5 verletzen.

    Die Stufen sind lueckenlos: `valid_to` ist der Vortag der naechsten Stufe,
    nicht der Tag der letzten Buchung. Welche Buchungen eine Stufe tragen,
    beantwortet der Belegendpunkt; ein Loch im Verlauf waere dagegen ein
    Zeitraum ohne gueltigen Preis.
    """
    ordered = sorted(
        (
            item
            for item in transactions
            if manual_from is None or item.booked_date < manual_from
        ),
        key=lambda item: (item.booked_date, str(item.transaction_id)),
    )

    groups: list[list[ContractEvidenceTransaction]] = []
    base: int | None = None
    for item in ordered:
        actual = abs(item.amount_minor)
        if base is None or abs(actual - base) > rules_service._amount_tolerance(base):
            groups.append([item])
            base = actual
        else:
            groups[-1].append(item)

    # Zwei Stufen am selben Tag ergaeben ein leeres Intervall und verletzten
    # `check (valid_to >= valid_from)`. Die spaetere gewinnt - gleiche
    # Entscheidung wie in `_write_backfill_stages`.
    kept = [
        group
        for index, group in enumerate(groups)
        if index + 1 >= len(groups)
        or groups[index + 1][0].booked_date > group[0].booked_date
    ]

    plans: list[ContractAmountStagePlan] = []
    previous: int | None = None
    for index, group in enumerate(kept):
        amount = _representative_amount(group)
        if index + 1 < len(kept):
            valid_to: date | None = kept[index + 1][0].booked_date - timedelta(days=1)
        elif manual_from is not None:
            valid_to = manual_from - timedelta(days=1)
        else:
            valid_to = None
        plans.append(
            ContractAmountStagePlan(
                amount_minor=amount,
                valid_from=group[0].booked_date,
                valid_to=valid_to,
                delta_minor=None if previous is None else amount - previous,
                transaction_count=len(group),
                source_transaction_id=group[0].transaction_id,
            )
        )
        previous = amount
    return plans


async def _manual_stage_floor(
    session: AsyncSession, household_id: UUID, contract_id: UUID
) -> tuple[date | None, int]:
    """Ab wann gehoert der Verlauf dem Nutzer? Plus die Zahl seiner Zeilen."""
    row = (
        await session.execute(
            text(
                "select min(valid_from) as floor_date, count(*) as manual_count "
                "from contract_amount_history where household_id = :household_id "
                "and contract_id = :contract_id and change_reason = 'manual'"
            ),
            {"household_id": str(household_id), "contract_id": str(contract_id)},
        )
    ).one()
    return row.floor_date, int(row.manual_count)


def _plan_matches_existing(
    plans: Sequence[ContractAmountStagePlan], existing: Sequence[ContractAmountEntry]
) -> bool:
    """Ist die Treppe schon genau so gespeichert?

    Macht den zweiten Lauf zu einem echten Nichtereignis: keine geloeschten und
    neu geschriebenen Zeilen, kein neues `created_at`, `changed=false`.
    """
    if len(plans) != len(existing):
        return False
    return all(
        plan.amount_minor == entry.amount_minor
        and plan.valid_from == entry.valid_from
        and plan.valid_to == entry.valid_to
        and plan.delta_minor == entry.delta_minor
        and plan.source_transaction_id == entry.source_transaction_id
        and entry.change_reason == "backfill"
        for plan, entry in zip(plans, existing, strict=True)
    )


async def recalculate_amount_history(
    session: AsyncSession, household_id: UUID, contract_id: UUID
) -> ContractAmountRecalculationResult:
    """Schreibt den Betragsverlauf aus den zugeordneten Buchungen neu.

    Der Gegenstand ist der Zustand aus 19.1: zwei Zeilen vom Uebernahmetag, die
    nichts belegen. Ordnet der Nutzer die drei fehlenden 49,95-EUR-Buchungen zu
    und ruft diese Neuberechnung auf, entsteht 49,95 EUR ab dem 15.01. und
    39,95 EUR ab dem 15.04.

    **Was mit `change_reason='manual'` passiert: nichts.** Manuelle Zeilen
    werden weder geloescht noch verschoben noch ueberschrieben, und sie bilden
    eine Untergrenze - ab dem `valid_from` der aeltesten manuellen Zeile
    schreibt die Neuberechnung gar nichts mehr, die letzte hergeleitete Stufe
    endet am Vortag. Begruendung: eine manuelle Korrektur ist eine Aussage des
    Nutzers ueber den Preis ab diesem Tag, und Leitsatz 5 stellt sie ueber jede
    automatische Ableitung. Die Vergangenheit davor darf die Automatik dagegen
    ergaenzen - dort hat der Nutzer nichts gesagt, und genau dort steht der
    unbrauchbare Migrationsbestand. Wer eine manuelle Zeile los werden will,
    loescht sie ausdruecklich; ein Rechenlauf tut das nicht fuer ihn.

    Automatisch entstandene Zeilen ('initial', 'detected_change', 'backfill')
    unterhalb dieser Grenze werden ersetzt. Ohne das Ersetzen bliebe die
    unbrauchbare 'initial'-Zeile neben der richtigen Treppe stehen.

    Ohne Belege wird nichts geloescht: ein Vertrag ohne zugeordnete Buchungen
    behaelt seinen Verlauf, statt ihn gegen eine leere Liste zu tauschen.

    Bewusst NICHT angefasst: `contracts.amount_minor` (der Sollbetrag ist eine
    Nutzerangabe und traegt die Fixkostensumme), Ereignisse und Fehlzaehler -
    dieselbe Zurueckhaltung, die der rueckwirkende Erstlauf sich auferlegt
    ("bewusst OHNE Ereignisse und ohne Fehlzaehler").
    """
    contract = await require_contract(session, household_id, contract_id)
    assigned = await load_assigned_transactions(session, household_id, contract_id)
    manual_from, manual_count = await _manual_stage_floor(session, household_id, contract_id)
    plans = plan_amount_stages(assigned, manual_from=manual_from)

    existing = await list_amount_history(session, household_id, contract_id)
    derived_existing = [
        entry
        for entry in existing
        if entry.change_reason != "manual"
        and (manual_from is None or entry.valid_from < manual_from)
    ]

    if not plans or _plan_matches_existing(plans, derived_existing):
        return ContractAmountRecalculationResult(
            contract_id=contract_id,
            changed=False,
            stages_written=0,
            stages_removed=0,
            manual_stages_kept=manual_count,
            amount_stages=existing,
            first_seen_date=contract.first_seen_date,
            last_seen_date=contract.last_seen_date,
            resulting_amount_minor=plans[-1].amount_minor if plans else None,
            contract_amount_minor=contract.amount_minor,
        )

    removed = cast(
        CursorResult[Any],
        await session.execute(
            text(
                "delete from contract_amount_history where household_id = :household_id "
                "and contract_id = :contract_id and change_reason <> 'manual' "
                "and (cast(:manual_from as date) is null or valid_from < :manual_from)"
            ),
            {
                "household_id": str(household_id),
                "contract_id": str(contract_id),
                "manual_from": manual_from,
            },
        ),
    ).rowcount

    for plan in plans:
        await session.execute(
            text(
                "insert into contract_amount_history (household_id, contract_id, "
                "amount_minor, valid_from, valid_to, delta_minor, change_reason, "
                "source_transaction_id) values (:household_id, :contract_id, :amount_minor, "
                ":valid_from, cast(:valid_to as date), cast(:delta_minor as bigint), "
                "'backfill', cast(:source_transaction_id as uuid))"
            ),
            {
                "household_id": str(household_id),
                "contract_id": str(contract_id),
                "amount_minor": plan.amount_minor,
                "valid_from": plan.valid_from,
                "valid_to": plan.valid_to,
                "delta_minor": plan.delta_minor,
                "source_transaction_id": str(plan.source_transaction_id),
            },
        )

    # Zeitraum nur ausweiten, nie einengen - gleiche Regel wie im
    # rueckwirkenden Erstlauf. Eine Teilmenge der Buchungen darf einen bekannten
    # Zeitraum nicht verkuerzen.
    first_seen = min(item.booked_date for item in assigned)
    last_seen = max(item.booked_date for item in assigned)
    updated = (
        await session.execute(
            text(
                "update contracts set "
                "first_seen_date = least(first_seen_date, cast(:first_seen as date)), "
                "last_seen_date = greatest(last_seen_date, cast(:last_seen as date)) "
                "where id = :contract_id and household_id = :household_id "
                "returning first_seen_date, last_seen_date"
            ),
            {
                "household_id": str(household_id),
                "contract_id": str(contract_id),
                "first_seen": first_seen,
                "last_seen": last_seen,
            },
        )
    ).one()

    return ContractAmountRecalculationResult(
        contract_id=contract_id,
        changed=True,
        stages_written=len(plans),
        stages_removed=int(removed),
        manual_stages_kept=manual_count,
        amount_stages=await list_amount_history(session, household_id, contract_id),
        first_seen_date=updated.first_seen_date,
        last_seen_date=updated.last_seen_date,
        resulting_amount_minor=plans[-1].amount_minor,
        contract_amount_minor=contract.amount_minor,
    )


# ---------------------------------------------------------------------------
# Vorschau des Zuordnens (Abschnitt 19.7, "Fehlzuordnung ohne Merge")
# ---------------------------------------------------------------------------

_PREVIEW_TRANSACTIONS_SQL: Final = f"""
select t.id as transaction_id, t.booked_date, t.amount_minor, t.account_id,
       {_EVIDENCE_PAYEE_TEXT} as payee_label,
       {_EVIDENCE_PURPOSE_TEXT} as purpose_text,
       o.contract_id as current_contract_id, c.name as current_contract_name
from transactions t
left join payees p on p.id = t.payee_id and p.household_id = t.household_id
left join contract_occurrences o
       on o.transaction_id = t.id and o.household_id = t.household_id
left join contracts c on c.id = o.contract_id and c.household_id = o.household_id
where t.household_id = :household_id
  and t.id = any(cast(:ids as uuid[]))
  and not t.is_archived
order by t.booked_date asc, t.id asc
"""


async def build_assignment_preview(
    session: AsyncSession,
    household_id: UUID,
    contract_id: UUID,
    transaction_ids: Sequence[UUID],
) -> ContractAssignmentPreview:
    """Quelle, Ziel, betroffene Buchungen und die Auswirkung auf Zeitraum und
    Preisverlauf - vor dem Umhaengen und ohne jeden Schreibvorgang.

    Bewusst fuer mehrere Buchungen zugleich: die drei fehlenden Buchungen des
    uebernommenen Vertrags sind ein Vorgang. Einzeln vorgeschaut ergaebe jede fuer
    sich eine andere Treppe, und keine davon waere die, die am Ende entsteht.
    """
    contract = await require_contract(session, household_id, contract_id)
    stages_before = await list_amount_history(session, household_id, contract.id)
    assigned = await load_assigned_transactions(session, household_id, contract.id)
    assigned_ids = {item.transaction_id for item in assigned}

    result = await session.execute(
        text(_PREVIEW_TRANSACTIONS_SQL),
        {
            "household_id": str(household_id),
            "ids": [str(item) for item in transaction_ids],
        },
    )
    rows = result.fetchall()

    requested: list[ContractEvidenceTransaction] = []
    sources: dict[UUID | None, tuple[str | None, int]] = {}
    for row in rows:
        requested.append(_evidence_transaction(row))
        source_id = (
            UUID(str(row.current_contract_id)) if row.current_contract_id is not None else None
        )
        name, count = sources.get(source_id, (row.current_contract_name, 0))
        sources[source_id] = (name, count + 1)

    found = {item.transaction_id for item in requested}
    combined = assigned + [item for item in requested if item.transaction_id not in assigned_ids]
    manual_from, _ = await _manual_stage_floor(session, household_id, contract.id)

    booked_dates = [item.booked_date for item in combined]

    return ContractAssignmentPreview(
        contract_id=contract.id,
        contract_name=contract.name,
        transactions=requested,
        sources=[
            ContractAssignmentSource(
                contract_id=source_id, contract_name=name, transaction_count=count
            )
            for source_id, (name, count) in sources.items()
        ],
        unknown_transaction_ids=[item for item in transaction_ids if item not in found],
        first_seen_date_before=contract.first_seen_date,
        first_seen_date_after=min(
            [contract.first_seen_date, *booked_dates]
            if contract.first_seen_date is not None
            else booked_dates,
            default=None,
        ),
        last_seen_date_before=contract.last_seen_date,
        last_seen_date_after=max(
            [contract.last_seen_date, *booked_dates]
            if contract.last_seen_date is not None
            else booked_dates,
            default=None,
        ),
        amount_stages_before=stages_before,
        amount_stages_after=plan_amount_stages(combined, manual_from=manual_from),
        stages_without_evidence_before=sum(
            1
            for stage in stages_before
            if stage.source_transaction_id is None
            and not any(_stage_contains(stage, item.booked_date) for item in assigned)
        ),
    )
