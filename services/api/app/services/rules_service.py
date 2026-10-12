"""Service-Logik fuer Klassifikationsregeln, Payee-Normalisierung und Review-Inbox.

Die Fixkostenerkennung liegt seit der Erkennung v2 nicht mehr hier, sondern in
`contract_detection_service` (docs/VERTRAEGE-FIXKOSTEN-PLAN.md, Abschnitt 6).
Dieses Modul stellt nur noch die Inbox zusammen und projiziert die dort
ermittelten Kandidaten auf die Antwortmodelle.

Zur Importrichtung: `contract_detection_service` wird ausschliesslich INNERHALB
der Funktionen importiert. `contract_matching_service` und `contract_service`
lesen `_recurring_payee_key`, `_amount_tolerance` und
`_CONTRACT_ENDED_CHECK_MIN_MISSED` aus diesem Modul; ein Import der Erkennung
auf Modulebene ergaebe den Kreis rules -> detection -> matching -> rules, der je
nach Ladereihenfolge mit ImportError oder AttributeError beim Start endet.
"""

import re
from collections.abc import Sequence
from datetime import date
from typing import TYPE_CHECKING, Any, NamedTuple, TypedDict
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import extract_sqlstate
from app.schemas.review import (
    ReviewContractAmountStep,
    ReviewContractDuplicatePartner,
    ReviewContractScore,
    ReviewContractSuggestionDetail,
    ReviewContractTransaction,
    ReviewCounterpart,
    ReviewInboxResponse,
    ReviewItemContractAmountChangeCheck,
    ReviewItemContractDuplicate,
    ReviewItemContractEndedCheck,
    ReviewItemContractSuggestion,
    ReviewItemDuplicate,
    ReviewItemTransfer,
    ReviewItemUnclassified,
)
from app.schemas.rules import RuleApplyItem, RuleApplyScope, RuleApplyStatus
from app.services import provenance_service
from app.services.booked_duplicate_service import scan_booked_duplicates

if TYPE_CHECKING:
    from app.services.contract_detection_service import ContractCandidate


class RuleCriterion(TypedDict):
    """Ein Kriterium, das an der treffenden Regel gesetzt war und zugestimmt hat."""

    field: str
    pattern: str


class RuleMatch(TypedDict):
    payee_id: UUID | None
    category_id: UUID | None
    is_transfer: bool
    note: str | None
    # Herkunftsnachweis: welche Regel getroffen hat und woran. Wird an
    # transaction_splits.category_rule_id/category_matched_on geschrieben, damit
    # die Begruendung spaeter lesbar bleibt, auch wenn die Regel inzwischen
    # umbenannt, geaendert oder geloescht wurde.
    rule_id: UUID
    rule_name: str
    matched_on: list[RuleCriterion]


# Ein Fixkostenbetrag darf etwas schwanken, aber niemals beliebig. Die
# Toleranz ist auf 10 EUR gedeckelt und betraegt bei kleineren Zahlungen 5 %
# (mindestens 1 EUR). Damit werden etwa 24,84 EUR und 102,81 EUR desselben
# Zahlungsdienstleisters nicht mehr zu einem erfundenen Durchschnitt vermischt.
_MIN_AMOUNT_TOLERANCE_MINOR = 100
_MAX_AMOUNT_TOLERANCE_MINOR = 1_000
_AMOUNT_TOLERANCE_RATIO = 0.05


def normalize_payee(label: str | None) -> str:
    if not label:
        return ""
    s = label.strip()
    s = re.sub(r"\s+", " ", s)
    return s


def _recurring_payee_key(label: str | None) -> str:
    """Vergleichsschluessel fuer Empfaengernamen aus Bank, Payee oder KI.

    Nur Schreibweise und Satzzeichen werden normalisiert. Aehnlich klingende
    Empfaenger werden nicht heuristisch zusammengelegt; dafuer ist die
    KI-normalisierte ``payee_name`` einer konkreten Buchung zustaendig.
    """
    if not label:
        return ""
    return re.sub(r"[^\w]+", " ", label.casefold(), flags=re.UNICODE).strip()


def _amount_tolerance(amount_minor: int) -> int:
    return min(
        _MAX_AMOUNT_TOLERANCE_MINOR,
        max(_MIN_AMOUNT_TOLERANCE_MINOR, round(abs(amount_minor) * _AMOUNT_TOLERANCE_RATIO)),
    )


async def get_recurring_candidates(
    session: AsyncSession, household_id: UUID, *, limit: int | None = 10
) -> list["ContractCandidate"]:
    """VERALTETER Name, delegiert an `contract_detection_service`.

    Die Erkennung selbst ist vollstaendig dorthin gezogen (Rhythmus,
    Betragstreppe, Vertragsschluessel, Vertrags-Score). Dieser Rumpf bleibt
    fuer die Dauer des Rollouts stehen, damit ein Aufrufer, der den alten Namen
    kennt, nicht ins Leere greift.

    `include_uncertain=False` ist Absicht: der Altpfad hat keine Kennzeichnung
    fuer "unsicher" und wuerde solche Kandidaten als gleichwertig anzeigen.
    """
    from app.services import contract_detection_service

    return await contract_detection_service.get_contract_candidates(
        session, household_id, limit=limit, include_uncertain=False
    )


def contract_suggestion_item(
    candidate: "ContractCandidate",
) -> ReviewItemContractSuggestion:
    """Projiziert einen Erkennungskandidaten auf das Antwortmodell.

    Bewusst hier und nicht in `contract_detection_service`: die Erkennung soll
    ohne die Antwortmodelle der Inbox uebersetzbar bleiben.
    """
    return ReviewItemContractSuggestion(
        source_transaction_id=candidate.source_transaction_id,
        display_name=candidate.display_name,
        payee_label=candidate.payee_label,
        contract_key=candidate.contract_key,
        contract_key_source=candidate.contract_key_source,
        amount_minor=candidate.amount_minor,
        direction=candidate.direction,
        amount_tolerance_minor=candidate.amount_tolerance_minor,
        frequency=candidate.frequency,
        first_date=candidate.first_date,
        last_date=candidate.last_date,
        occurrences_count=candidate.occurrences_count,
        contract_type=candidate.contract_type,
        installments_total=candidate.installments_total,
        installments_paid=candidate.installments_paid,
        amount_steps=[
            ReviewContractAmountStep(
                # Die Stufen tragen das Vorzeichen der Buchung; nach aussen
                # gilt die Vertragskonvention (positiv + `direction`).
                amount_minor=abs(step.amount_minor),
                first_date=step.first_date,
                last_date=step.last_date,
                occurrences_count=len(step.transactions),
            )
            for step in candidate.amount_steps
        ],
        suggested_category_id=candidate.suggested_category_id,
        detection_confidence=candidate.score.total,
        confidence=candidate.confidence,
        is_uncertain=candidate.confidence == "uncertain",
        score_breakdown=ReviewContractScore(
            date_stability=candidate.score.date_stability,
            amount_stability=candidate.score.amount_stability,
            occurrence_count=candidate.score.occurrence_count,
            category_affinity=candidate.score.category_affinity,
            payment_method=candidate.score.payment_method,
            total=candidate.score.total,
        ),
    )


def contract_suggestion_detail(
    candidate: "ContractCandidate",
) -> ReviewContractSuggestionDetail:
    """Vorschlag samt Einzelbuchungen, jede mit ihrer Preisstufe."""
    step_of: dict[UUID, int] = {
        row.transaction_id: index
        for index, step in enumerate(candidate.amount_steps)
        for row in step.transactions
    }
    return ReviewContractSuggestionDetail(
        suggestion=contract_suggestion_item(candidate),
        transactions=[
            ReviewContractTransaction(
                transaction_id=row.transaction_id,
                account_id=row.account_id,
                account_name=row.account_name,
                booked_date=row.booked_date,
                amount_minor=row.amount_minor,
                payee_label=row.raw_payee_label,
                note=row.note,
                amount_step_index=step_of.get(row.transaction_id, 0),
            )
            for row in candidate.transactions
        ],
    )


# Wie weit die Luecke zwischen altem Ende und neuem Beginn hoechstens sein darf,
# damit zwei Eintraege noch derselbe Vertrag sein koennen: rund drei Perioden.
# Ohne diese Grenze wuerde ein vor drei Jahren beendeter Vertrag mit einem
# heutigen desselben Anbieters gepaart, obwohl dazwischen nichts lief.
_DUPLICATE_MAX_GAP_SQL = (
    "case older.frequency when 'weekly' then 21 when 'monthly' then 92 "
    "when 'quarterly' then 275 when 'semiannual' then 549 else 1096 end"
)

_CONTRACT_DUPLICATE_SQL = f"""
select
  older.id as older_id, older.name as older_name,
  older.provider_label as older_provider_label,
  older.amount_minor as older_amount_minor, older.status as older_status,
  older.first_seen_date as older_first_seen_date,
  older.last_seen_date as older_last_seen_date,
  older.contract_key as older_contract_key, older.payee_id as older_payee_id,
  older.due_day as older_due_day,
  newer.id as newer_id, newer.name as newer_name,
  newer.provider_label as newer_provider_label,
  newer.amount_minor as newer_amount_minor, newer.status as newer_status,
  newer.first_seen_date as newer_first_seen_date,
  newer.last_seen_date as newer_last_seen_date,
  newer.contract_key as newer_contract_key, newer.payee_id as newer_payee_id,
  newer.due_day as newer_due_day,
  newer.frequency as frequency, newer.direction as direction,
  coalesce(np.name, newer.provider_label, newer.name) as payee_label,
  (newer.first_seen_date - older.last_seen_date) as gap_days
from contracts older
join contracts newer
  on newer.household_id = older.household_id
 and newer.id <> older.id
 and newer.frequency = older.frequency
 and newer.direction = older.direction
 and (
   (older.payee_id is not null and newer.payee_id = older.payee_id)
   or lower(btrim(coalesce(older.provider_label, older.name)))
      = lower(btrim(coalesce(newer.provider_label, newer.name)))
 )
left join payees np on np.id = newer.payee_id and np.household_id = newer.household_id
where older.household_id = :household_id
  and older.status in ('active', 'paused', 'cancelled', 'ended')
  and newer.status in ('active', 'paused', 'cancelled', 'ended')
  and older.last_seen_date is not null
  and newer.first_seen_date is not null
  and older.last_seen_date < newer.first_seen_date
  and (newer.first_seen_date - older.last_seen_date) <= {_DUPLICATE_MAX_GAP_SQL}
  -- Schluesselbewusste Ausschlussregel (Abschnitt 19.8, Leitsatz 4): tragen
  -- beide Vertraege einen Schluessel und sind sie VERSCHIEDEN, ist das Paar
  -- kein Vorschlag - auch nicht bei gleichem Anbieter, gleicher Frequenz und
  -- knapper zeitlicher Luecke. Fehlt einer der beiden Schluessel, bleibt das
  -- Paar (schwaecherer Beleg, siehe evidence_strength unten).
  and not (
    older.contract_key is not null and newer.contract_key is not null
    and older.contract_key <> newer.contract_key
  )
  and not exists (
    select 1 from contract_events e
    where e.household_id = older.household_id and e.kind = 'dismissed'
      and (
        (e.contract_id = newer.id and e.payload ->> 'duplicate_of' = cast(older.id as text))
        or (e.contract_id = older.id and e.payload ->> 'duplicate_of' = cast(newer.id as text))
      )
  )
order by newer.first_seen_date desc, older.last_seen_date desc
limit :limit
"""


async def get_contract_duplicate_suggestions(
    session: AsyncSession, household_id: UUID, *, limit: int = 20
) -> list[ReviewItemContractDuplicate]:
    """Zwei Eintraege, die vermutlich derselbe Vertrag mit neuem Preis sind.

    Entscheidend ist die zeitliche Trennung: der aeltere endet, bevor der
    juengere beginnt. Ueberlappende Zeitraeume sind zwei echte Vertraege
    desselben Anbieters - genau der Telekom-Fall, bei dem ein Zusammenfuehren
    Daten vernichten wuerde.

    Gedacht fuer den Altbestand: Vertraege, die vor der Erkennung v2 als zwei
    getrennte Eintraege entstanden sind. Die Erkennung selbst legt solche Paare
    nicht mehr an, sie erkennt Preisstufen inzwischen als einen Vertrag.

    Seit Abschnitt 19.8 zaehlt der Vertragsschluessel: gleiche, nicht-leere
    Schluessel sind ein starker Beleg (`evidence_strength='strong'`), fehlt
    einer, bleibt es ein schwaecherer Pruefvorschlag (`'review'`). Zwei
    VERSCHIEDENE Schluessel schliessen das Paar bereits in der SQL-Abfrage aus.
    """
    result = await session.execute(
        text(_CONTRACT_DUPLICATE_SQL),
        {"household_id": str(household_id), "limit": limit},
    )
    items: list[ReviewItemContractDuplicate] = []
    for row in result:
        older = ReviewContractDuplicatePartner(
            contract_id=UUID(str(row.older_id)),
            name=str(row.older_name),
            provider_label=row.older_provider_label,
            amount_minor=int(row.older_amount_minor),
            status=row.older_status,
            first_seen_date=row.older_first_seen_date,
            last_seen_date=row.older_last_seen_date,
        )
        newer = ReviewContractDuplicatePartner(
            contract_id=UUID(str(row.newer_id)),
            name=str(row.newer_name),
            provider_label=row.newer_provider_label,
            amount_minor=int(row.newer_amount_minor),
            status=row.newer_status,
            first_seen_date=row.newer_first_seen_date,
            last_seen_date=row.newer_last_seen_date,
        )

        same_key = (
            row.older_contract_key is not None
            and row.newer_contract_key is not None
            and row.older_contract_key == row.newer_contract_key
        )
        reason_codes = ["non_overlapping_periods", "same_frequency"]
        if same_key:
            reason_codes.append("same_contract_key")
        if (
            row.older_payee_id is not None
            and row.newer_payee_id is not None
            and row.older_payee_id == row.newer_payee_id
        ):
            reason_codes.append("same_payee")
        if (
            row.older_due_day is not None
            and row.newer_due_day is not None
            and row.older_due_day == row.newer_due_day
        ):
            reason_codes.append("matching_due_day")
        if older.amount_minor != newer.amount_minor:
            reason_codes.append("amount_transition")

        items.append(
            ReviewItemContractDuplicate(
                # Ziel ist der juengere Vertrag: er traegt den aktuellen Preis
                # und die naechste Faelligkeit.
                target_id=newer.contract_id,
                source_ids=[older.contract_id],
                payee_label=str(row.payee_label),
                frequency=row.frequency,
                direction=row.direction,
                older=older,
                newer=newer,
                gap_days=int(row.gap_days),
                amount_delta_minor=newer.amount_minor - older.amount_minor,
                evidence_strength="strong" if same_key else "review",
                reason_codes=reason_codes,
            )
        )
    return items


# Erheblichkeitsschwelle fuer die Rueckfrage bei geaendertem Betrag.
#
# Gemessen am lokalen Bestand (04.08.2026): 322 `price_change`-Ereignisse, 316
# davon unbestaetigt; nach Status-, Merge- und Erheblichkeitsfilter bleiben 244.
# Die Menge verteilt sich auf ueber 3000 Haushalte - je Haushalt stehen dort
# heute hoechstens drei offene Ereignisse, im Median eines. Die
# Schwelle rettet die Inbox also nicht vor einer Flut, sie haelt die Frage
# beantwortbar: eine Verbrauchsabrechnung, die um 40 Cent schwankt, ist keine
# Preisaenderung, zu der ein Mensch sinnvoll "stimmt das?" sagen kann. Wer
# solche Zeilen zu sehen bekommt, hoert auf, die Liste zu lesen - und dann
# bleibt auch die eine Zeile liegen, auf die es ankommt.
#
# Bewusst dieselben Groessenordnungen wie die Preis-Insights
# (`insights_service._PRICE_MIN_DELTA_MINOR` und `_PRICE_MIN_RATIO = 1.05`):
# zwei verschiedene Zahlen zur selben Frage "ist diese Preisaenderung der Rede
# wert?" waeren eine zweite Wahrheit. BEIDE Bedingungen muessen zutreffen -
# 2 EUR auf 2000 EUR ist kein Ereignis, und 5 % von 3 EUR auch nicht.
#
# Der Anteil steht als GANZE PROZENT und nicht als Verhaeltnis 1.05, weil die
# Abfrage ihn ganzzahlig vergleicht (`delta * 100 >= alt * 5`). Mit dem
# Gleitkommafaktor 0.05 faellt der Grenzfall auf die falsche Seite: 4000 * 0.05
# ergibt in doppelter Genauigkeit 200.00000000000003, und eine Aenderung um
# exakt 200 waere damit "knapp unter 5 %". Eine Schwelle, die je nach Betrag
# mal greift und mal nicht, ist keine.
_AMOUNT_CHANGE_MIN_DELTA_MINOR = 200
_AMOUNT_CHANGE_MIN_PCT = 5

# Der Vergleich laeuft ueber `case ... when ... ~ ... then ...::bigint end`
# statt ueber einen Cast hinter einem WHERE-Regex: Postgres darf Qualifikation
# und Projektion umsortieren, ein ungeschuetzter Cast wuerde bei einer
# unerwartet nicht-numerischen Nutzlast die ganze Inbox mit 22P02 kippen. Das
# `case` traegt seinen eigenen Schutz und ist in jeder Reihenfolge sicher.
#
# `c.status in ('active', 'cancelled')` ist absichtlich genau
# `contract_matching_service.RECONCILE_STATUSES` - dieselbe Menge, die das
# Ereignis ueberhaupt erst erzeugt, und dieselbe, die der Push-Filter liest
# (worker/notifications/contract_alerts.py). Ein beendeter Vertrag wird nicht
# mehr gefragt: dort ist der Betrag Geschichte, keine offene Frage.
_CONTRACT_AMOUNT_CHANGE_SQL = """
select event_id, contract_id, occurred_on, name, provider_label, direction,
       frequency, old_minor, new_minor
from (
  select
    ce.id as event_id, ce.contract_id as contract_id,
    ce.occurred_on as occurred_on, ce.created_at as created_at,
    c.name as name, c.provider_label as provider_label,
    c.direction as direction, c.frequency as frequency,
    case when ce.payload ->> 'old_minor' ~ '^[0-9]+$'
         then (ce.payload ->> 'old_minor')::bigint end as old_minor,
    case when ce.payload ->> 'new_minor' ~ '^[0-9]+$'
         then (ce.payload ->> 'new_minor')::bigint end as new_minor
  from contract_events ce
  join contracts c
    on c.id = ce.contract_id and c.household_id = ce.household_id
  where ce.household_id = :household_id
    and ce.kind = 'price_change'
    and ce.acknowledged_at is null
    -- Ein zusammengefuehrter Vertrag ist ueberall sonst unsichtbar; eine
    -- Rueckfrage zu ihm liefe ins Leere (Abschnitt 19.7, Soft-Merge).
    and c.merged_into_id is null
    and c.status in ('active', 'cancelled')
) amount_change
where old_minor is not null and new_minor is not null
  and old_minor > 0
  and abs(new_minor - old_minor) >= cast(:min_delta as bigint)
  and abs(new_minor - old_minor) * 100 >= old_minor * cast(:min_pct as bigint)
order by occurred_on desc, created_at desc, event_id desc
limit :limit
"""


async def get_contract_amount_change_checks(
    session: AsyncSession, household_id: UUID, *, limit: int = 50
) -> list[ReviewItemContractAmountChangeCheck]:
    """Erhebliche, noch unbestaetigte Betragsaenderungen als Rueckfrage.

    Der Abgleich uebernimmt eine erkannte Preisstufe heute still. Das ist bei
    einer echten Anpassung richtig und bei einer falsch zugeordneten Zahlung
    desselben Empfaengers falsch - unterscheiden kann das nur der Nutzer.
    Diese Abfrage stellt ihm die Frage, ohne selbst etwas zu aendern.

    Ausdruecklich richtungsneutral: Senkungen sind eingeschlossen. Der
    Anlassfall ist eine Senkung, und gerade sie ist der
    Fall, in dem ein stilles Uebernehmen den Sollbetrag nach unten zieht.

    `acknowledged_at` ist der Ausblendmerker; eine eigene Tabelle braucht es
    dafuer nicht (Spalte existiert seit Phase 4). Dieselbe Spalte liest der
    naechtliche Push-Filter - siehe den Docstring der Annahme-Route
    `_confirm_contract_amount_change` in api/v1/routes/review.py.
    """
    result = await session.execute(
        text(_CONTRACT_AMOUNT_CHANGE_SQL),
        {
            "household_id": str(household_id),
            "min_delta": _AMOUNT_CHANGE_MIN_DELTA_MINOR,
            "min_pct": _AMOUNT_CHANGE_MIN_PCT,
            "limit": limit,
        },
    )
    items: list[ReviewItemContractAmountChangeCheck] = []
    for row in result:
        old_minor = int(row.old_minor)
        new_minor = int(row.new_minor)
        delta_minor = new_minor - old_minor
        items.append(
            ReviewItemContractAmountChangeCheck(
                event_id=UUID(str(row.event_id)),
                contract_id=UUID(str(row.contract_id)),
                name=str(row.name),
                provider_label=row.provider_label,
                direction=row.direction,
                frequency=row.frequency,
                old_amount_minor=old_minor,
                new_amount_minor=new_minor,
                delta_minor=delta_minor,
                # Aus alt/neu neu gerechnet statt aus payload->>'pct'
                # uebernommen: die Nutzlast ist eine Momentaufnahme des
                # Abgleichs, die angezeigte Zahl muss zu den beiden hier
                # ausgegebenen Betraegen passen. `old_minor > 0` steht in der
                # Abfrage, eine Division durch null gibt es nicht.
                delta_pct=round(delta_minor * 100.0 / old_minor, 2),
                occurred_on=row.occurred_on,
            )
        )
    return items


def _matches_rule_pattern(pattern: str | None, text: str | None, exact_word: bool) -> bool:
    if not pattern or not pattern.strip():
        return True
    raw = pattern.strip()
    norm_text = text or ""
    # Automatische Erkennung von Regex-Mustern (z.B. ^(Amazon|AMZN), (Rewe|Penny), /pattern/)
    is_regex = (
        raw.startswith("^")
        or raw.startswith("(")
        or raw.endswith("$")
        or "|" in raw
        or "\\" in raw
        or (raw.startswith("/") and raw.endswith("/") and len(raw) > 2)
    )
    if is_regex:
        expr = raw[1:-1] if (raw.startswith("/") and raw.endswith("/")) else raw
        try:
            return re.search(expr, norm_text, re.IGNORECASE) is not None
        except re.error:
            pass

    low_pattern = raw.lower()
    low_text = norm_text.lower()
    if exact_word:
        return re.search(rf"\b{re.escape(low_pattern)}\b", low_text) is not None
    return low_pattern in low_text


def evaluate_rule(
    rule: Any,
    *,
    payee_label: str | None,
    purpose: str | None,
    amount_minor: int,
    account_id: UUID,
) -> list[RuleCriterion] | None:
    """Prueft GENAU EINE Regel gegen eine Buchung.

    Rueckgabe ist die Liste der Kriterien, die zugestimmt haben. Eine Regel
    ohne jedes Kriterium trifft auf alles zu und liefert eine leere Liste -
    die Unterscheidung "leere Liste" (trifft zu, ohne Begruendung) gegen
    "None" (trifft nicht zu) ist bedeutungstragend und darf nicht zu einem
    Wahrheitswert verkuerzt werden.
    """
    match_account = getattr(rule, "match_account_id", None)
    if match_account and str(match_account) != str(account_id):
        return None

    min_amount = getattr(rule, "match_min_amount_minor", None)
    max_amount = getattr(rule, "match_max_amount_minor", None)
    amount_mode = getattr(rule, "match_amount_mode", "inside") or "inside"

    if amount_mode == "outside":
        if min_amount is not None and max_amount is not None:
            if min_amount <= amount_minor <= max_amount:
                return None
        elif min_amount is not None and amount_minor >= min_amount:
            return None
        elif max_amount is not None and amount_minor <= max_amount:
            return None
    else:
        if min_amount is not None and amount_minor < min_amount:
            return None
        if max_amount is not None and amount_minor > max_amount:
            return None

    norm_payee = payee_label or ""
    norm_purpose = purpose or ""

    exclude_payee = getattr(rule, "exclude_payee_raw", None)
    exclude_purp = getattr(rule, "exclude_purpose", None)
    if exclude_payee and exclude_payee.strip():
        if _matches_rule_pattern(exclude_payee, norm_payee, False):
            return None
    if exclude_purp and exclude_purp.strip():
        if _matches_rule_pattern(exclude_purp, norm_purpose, False):
            return None

    match_payee_raw = getattr(rule, "match_payee_raw", None)
    match_payee_exact = getattr(rule, "match_payee_exact_word", False)
    if match_payee_raw and match_payee_raw.strip():
        if not _matches_rule_pattern(match_payee_raw, norm_payee, match_payee_exact):
            return None

    match_purpose = getattr(rule, "match_purpose", None)
    match_purpose_exact = getattr(rule, "match_purpose_exact_word", False)
    if match_purpose and match_purpose.strip():
        if not _matches_rule_pattern(match_purpose, norm_purpose, match_purpose_exact):
            return None

    matched_on: list[RuleCriterion] = []
    if match_payee_raw:
        matched_on.append({"field": "payee_raw", "pattern": match_payee_raw})
    if match_purpose:
        matched_on.append({"field": "purpose", "pattern": match_purpose})
    if min_amount is not None:
        matched_on.append({"field": "amount_min", "pattern": str(min_amount)})
    if max_amount is not None:
        matched_on.append({"field": "amount_max", "pattern": str(max_amount)})
    if match_account:
        matched_on.append({"field": "account", "pattern": str(match_account)})
    return matched_on


async def load_active_classification_rules(
    session: AsyncSession, household_id: UUID
) -> Sequence[Any]:
    """Ein Regelsatz pro Vorschau statt einer Datenbankabfrage je Zeile."""
    res = await session.execute(
        text(
            "select id, name, action_payee_id, action_category_id, action_set_transfer, "
            "action_note, match_payee_raw, match_payee_exact_word, match_purpose, "
            "match_purpose_exact_word, exclude_payee_raw, exclude_purpose, "
            "match_min_amount_minor, match_max_amount_minor, match_amount_mode, "
            "match_account_id, action_reimbursable, action_expectation_mode, "
            "action_expectation_rate, action_expectation_minor "
            "from classification_rules "
            "where household_id = :household_id and is_active = true "
            "order by priority desc, created_at asc"
        ),
        {"household_id": str(household_id)},
    )
    return res.all()


async def match_rules_for_transaction(
    session: AsyncSession,
    household_id: UUID,
    payee_label: str | None,
    purpose: str | None,
    amount_minor: int,
    account_id: UUID,
    *,
    rules: Sequence[Any] | None = None,
) -> RuleMatch | None:
    """Wendet aktive Klassifikationsregeln in Abhaengigkeit der Prioritaet an."""
    if rules is None:
        rules = await load_active_classification_rules(session, household_id)

    for r in rules:
        matched_on = evaluate_rule(
            r,
            payee_label=payee_label,
            purpose=purpose,
            amount_minor=amount_minor,
            account_id=account_id,
        )
        if matched_on is None:
            continue

        return {
            "payee_id": r.action_payee_id,
            "category_id": r.action_category_id,
            "is_transfer": r.action_set_transfer,
            "note": r.action_note,
            "rule_id": r.id,
            "rule_name": r.name,
            "matched_on": matched_on,
        }

    return None


# Meldung des Triggers reject_archived_category_reference()
# (supabase/migrations/0001_schema.sql). Zwischen dem Aufloesen
# der Zielkategorie und dem Schreiben kann sie archiviert worden sein - dann
# scheitert nur diese eine Buchung, nicht der ganze Lauf.
_CATEGORY_ARCHIVED_MESSAGE = "CATEGORY_ARCHIVED"
_CHECK_VIOLATION_SQLSTATE = "23514"


def _is_archived_category_error(exc: DBAPIError) -> bool:
    if extract_sqlstate(exc) != _CHECK_VIOLATION_SQLSTATE:
        return False
    return _CATEGORY_ARCHIVED_MESSAGE in str(getattr(exc, "orig", exc))


async def _load_rule_candidates(
    session: AsyncSession,
    household_id: UUID,
    from_date: date | None,
    to_date: date | None,
) -> list[Any]:
    """Buchungen, die fuer einen Regellauf ueberhaupt in Frage kommen.

    Der Textvergleich laeuft gegen `payee_raw`/`purpose_raw` - dieselben
    Felder, die auch der Import der Regel vorlegt. Der Rueckfall auf
    `payee_label`/`note` ist noetig, weil von Hand angelegte Buchungen keine
    Bankrohtexte tragen; ohne ihn waere jede manuelle Buchung fuer Regeln
    unsichtbar.

    Archivierte Buchungen fallen still heraus: sie sind aus jeder Auswertung
    genommen und tauchen deshalb auch nicht als "uebersprungen" auf.
    """
    res = await session.execute(
        text(
            "select t.id as transaction_id, t.booked_date, t.amount_minor, "
            "t.account_id, t.is_transfer, t.version, "
            "coalesce(nullif(t.payee_raw, ''), t.payee_label) as payee_text, "
            "coalesce(nullif(t.purpose_raw, ''), t.note) as purpose_text, "
            "count(ts.id) as split_count, "
            "(array_agg(ts.id order by ts.created_at asc, ts.id asc))[1] as split_id, "
            "(array_agg(ts.category_id order by ts.created_at asc, ts.id asc))[1] "
            "as split_category_id "
            "from transactions t "
            "left join transaction_splits ts on ts.transaction_id = t.id "
            "where t.household_id = :household_id "
            "and not t.is_archived "
            "and (cast(:from_date as date) is null "
            "     or t.booked_date >= cast(:from_date as date)) "
            "and (cast(:to_date as date) is null "
            "     or t.booked_date <= cast(:to_date as date)) "
            "group by t.id, t.booked_date, t.amount_minor, t.account_id, "
            "t.is_transfer, t.version, t.payee_raw, t.payee_label, "
            "t.purpose_raw, t.note "
            "order by t.booked_date asc, t.id asc"
        ),
        {
            "household_id": str(household_id),
            "from_date": from_date,
            "to_date": to_date,
        },
    )
    return list(res.all())


async def _write_rule_application(
    session: AsyncSession,
    household_id: UUID,
    rule: Any,
    candidate: Any,
    matched_on: list[RuleCriterion],
    target_category_id: UUID | None,
) -> tuple[RuleApplyStatus, str | None]:
    """Schreibt genau eine Buchung eines Regellaufs.

    Laeuft im Savepoint und mit optimistischer Sperre auf `version`: wer
    zwischen Vorschau und Anwendung dieselbe Buchung angefasst hat, gewinnt -
    diese Zeile meldet dann `conflict`, die uebrigen des Laufs bleiben
    bestehen."""
    try:
        async with session.begin_nested():
            bumped = (
                await session.execute(
                    text(
                        "update transactions set version = version + 1, "
                        "is_transfer = case when :set_transfer then true "
                        "                   else is_transfer end "
                        "where id = :id and household_id = :household_id "
                        "and version = :expected_version and not is_archived "
                        "returning version"
                    ),
                    {
                        "id": str(candidate.transaction_id),
                        "household_id": str(household_id),
                        "expected_version": int(candidate.version),
                        "set_transfer": bool(rule.action_set_transfer),
                    },
                )
            ).first()
            if bumped is None:
                return "conflict", "version_conflict"

            # Ohne Zielkategorie setzt die Regel nur das Transferkennzeichen -
            # dann bleibt der Split unberuehrt, statt seine Kategorie und
            # deren Herkunft auf null zu ueberschreiben.
            if target_category_id is not None:
                await session.execute(
                    text(
                        "update transaction_splits set "
                        f"{provenance_service.CATEGORY_ASSIGNMENT_SET_SQL} "
                        "where id = :split_id"
                    ),
                    {
                        "split_id": str(candidate.split_id),
                        **provenance_service.rule_assignment(
                            target_category_id, rule.id, rule.name, matched_on
                        ),
                    },
                )
    except DBAPIError as exc:
        if not _is_archived_category_error(exc):
            raise
        return "skipped", "category_inactive"

    return "applied", None


async def _settle_candidate(
    session: AsyncSession,
    household_id: UUID,
    rule: Any,
    candidate: Any,
    matched_on: list[RuleCriterion],
    *,
    target_category_id: UUID | None,
    scope: RuleApplyScope,
    write: bool,
) -> tuple[RuleApplyStatus, str | None, UUID | None]:
    """Entscheidet fuer EINE getroffene Buchung, was mit ihr geschieht.

    Einzellauf und Sammellauf teilen sich diese Entscheidung: waeren es zwei
    Fassungen, koennte die Vorschau des einen Wegs etwas anderes melden als die
    Anwendung des anderen. Rueckgabe ist (Status, Grund, bisherige Kategorie).
    """
    previous_category_id = (
        UUID(str(candidate.split_category_id))
        if candidate.split_category_id is not None
        else None
    )

    if int(candidate.split_count) != 1:
        # Bei mehreren Splits waere unklar, welcher die Kategorie erhaelt -
        # dieselbe Grenze zieht die Mehrfachzuordnung (siehe
        # routes/transactions.py, _bulk_category_items).
        return "skipped", "split_transaction", previous_category_id

    if scope == "uncategorized" and previous_category_id is not None:
        return "skipped", "already_categorized", previous_category_id

    category_settled = (
        target_category_id is None or previous_category_id == target_category_id
    )
    transfer_settled = not rule.action_set_transfer or bool(candidate.is_transfer)
    if category_settled and transfer_settled:
        return "unchanged", "already_assigned", previous_category_id

    if not write:
        # Die Vorschau meldet denselben Status, den die Anwendung liefern
        # wuerde, fuehrt aber kein einziges UPDATE aus.
        return "applied", None, previous_category_id

    status, reason = await _write_rule_application(
        session, household_id, rule, candidate, matched_on, target_category_id
    )
    return status, reason, previous_category_id


async def apply_rule_to_existing(
    session: AsyncSession,
    household_id: UUID,
    rule: Any,
    *,
    target_category_id: UUID | None,
    scope: RuleApplyScope,
    from_date: date | None,
    to_date: date | None,
    limit: int,
    write: bool,
) -> tuple[list[RuleApplyItem], int, bool]:
    """Wendet eine Regel auf den vorhandenen Buchungsbestand an.

    Bewusst nur diese eine Regel, ohne Prioritaetsvergleich mit den uebrigen:
    der Aufrufer hat eine bestimmte Regel ausgewaehlt, und ein Lauf, der
    stattdessen eine hoeher priorisierte Regel schreibt, waere nicht
    erklaerbar. Fuer neue Buchungen bleibt `match_rules_for_transaction` mit
    seiner Prioritaetsreihenfolge zustaendig.

    Geschrieben werden ausschliesslich Kategorie und Transferkennzeichen.
    `action_payee_id` und `action_note` bleiben aussen vor: rueckwirkend waere
    das ein stilles Ueberschreiben von Notizen, die der Nutzer selbst gepflegt
    hat - beim Import gibt es dagegen noch nichts zu ueberschreiben.

    Rueckgabe: (Positionen, Zahl der Treffer, abgeschnitten?). Die Zahl der
    Treffer zaehlt ALLE zutreffenden Buchungen, auch die jenseits von `limit`.
    """
    candidates = await _load_rule_candidates(session, household_id, from_date, to_date)

    items: list[RuleApplyItem] = []
    matched = 0
    truncated = False

    for candidate in candidates:
        matched_on = evaluate_rule(
            rule,
            payee_label=candidate.payee_text,
            purpose=candidate.purpose_text,
            amount_minor=int(candidate.amount_minor),
            account_id=candidate.account_id,
        )
        if matched_on is None:
            continue

        matched += 1
        if len(items) >= limit:
            truncated = True
            continue

        status, reason, previous_category_id = await _settle_candidate(
            session,
            household_id,
            rule,
            candidate,
            matched_on,
            target_category_id=target_category_id,
            scope=scope,
            write=write,
        )

        items.append(
            RuleApplyItem(
                transaction_id=UUID(str(candidate.transaction_id)),
                booked_date=candidate.booked_date,
                amount_minor=int(candidate.amount_minor),
                payee=candidate.payee_text,
                purpose=candidate.purpose_text,
                status=status,
                reason=reason,
                previous_category_id=previous_category_id,
            )
        )

    return items, matched, truncated


class PreparedRule(NamedTuple):
    """Eine laufbereite Regel des Sammellaufs: der Datensatz und die bereits
    gepruefte Zielkategorie. Die Pruefung passiert in der Route, weil eine
    archivierte Zielkategorie dort nur diese eine Regel aus dem Lauf nimmt -
    im Einzellauf bleibt sie ein Fehler des ganzen Laufs."""

    rule: Any
    target_category_id: UUID | None


# Zaehler je Regel im Sammellauf. `matched` zaehlt alle Treffer, die uebrigen
# nur die tatsaechlich verarbeiteten (also innerhalb von `limit`).
RuleRunCounters = dict[str, int]


def _new_counters() -> RuleRunCounters:
    return {"matched": 0, "applied": 0, "unchanged": 0, "skipped": 0, "conflict": 0}


async def apply_all_rules_to_existing(
    session: AsyncSession,
    household_id: UUID,
    prepared: Sequence[PreparedRule],
    *,
    scope: RuleApplyScope,
    from_date: date | None,
    to_date: date | None,
    limit: int,
    write: bool,
) -> tuple[list[RuleApplyItem], dict[UUID, RuleRunCounters], int, bool]:
    """Wendet alle uebergebenen Regeln in einem Durchgang auf den Bestand an.

    Je Buchung gewinnt die ERSTE passende Regel aus `prepared` - die Liste
    kommt in Prioritaetsreihenfolge herein, und damit entscheidet der
    Sammellauf genau so wie `match_rules_for_transaction` beim Import. Wuerde
    stattdessen jede Regel fuer sich laufen, ueberschriebe die zuletzt
    ausgefuehrte das Ergebnis der hoeher priorisierten.

    Der Buchungsbestand wird einmal geladen und nicht je Regel erneut.

    Rueckgabe: (Positionen, Zaehler je Regel, geprueft Buchungen, abgeschnitten?).
    """
    candidates = await _load_rule_candidates(session, household_id, from_date, to_date)

    counters: dict[UUID, RuleRunCounters] = {
        UUID(str(entry.rule.id)): _new_counters() for entry in prepared
    }
    items: list[RuleApplyItem] = []
    truncated = False

    for candidate in candidates:
        for entry in prepared:
            matched_on = evaluate_rule(
                entry.rule,
                payee_label=candidate.payee_text,
                purpose=candidate.purpose_text,
                amount_minor=int(candidate.amount_minor),
                account_id=candidate.account_id,
            )
            if matched_on is None:
                continue

            rule_id = UUID(str(entry.rule.id))
            counters[rule_id]["matched"] += 1
            if len(items) >= limit:
                truncated = True
                break

            status, reason, previous_category_id = await _settle_candidate(
                session,
                household_id,
                entry.rule,
                candidate,
                matched_on,
                target_category_id=entry.target_category_id,
                scope=scope,
                write=write,
            )
            counters[rule_id][status] += 1
            items.append(
                RuleApplyItem(
                    transaction_id=UUID(str(candidate.transaction_id)),
                    booked_date=candidate.booked_date,
                    amount_minor=int(candidate.amount_minor),
                    payee=candidate.payee_text,
                    purpose=candidate.purpose_text,
                    status=status,
                    reason=reason,
                    previous_category_id=previous_category_id,
                    rule_id=rule_id,
                    rule_name=entry.rule.name,
                )
            )
            break

    return items, counters, len(candidates), truncated


# Ab zwei ausgebliebenen Perioden wird gefragt. Eine einzelne Luecke ist
# meistens eine verschobene Abbuchung; erst die zweite ist ein Hinweis darauf,
# dass der Vertrag nicht mehr laeuft.
_CONTRACT_ENDED_CHECK_MIN_MISSED = 2


async def get_review_inbox(
    session: AsyncSession,
    household_id: UUID,
    *,
    unclassified_limit: int = 100,
    unclassified_offset: int = 0,
    unclassified_search: str | None = None,
) -> ReviewInboxResponse:
    """Sammelt alle unklassifizierten oder zu pruefenden Positionen."""
    # 1. Unklassifizierte Buchungen (kein Split oder Split ohne Kategorie)
    unclassified_base_where = (
        "t.household_id = :household_id and not t.is_archived and not t.is_transfer "
        "and (not exists (select 1 from transaction_splits s where s.transaction_id = t.id) "
        "or exists (select 1 from transaction_splits s where s.transaction_id = t.id "
        "and s.category_id is null)) "
    )
    unclassified_where = unclassified_base_where
    params: dict[str, object] = {
        "household_id": str(household_id),
        "limit": unclassified_limit,
        "offset": unclassified_offset,
    }
    unclassified_open_total = (
        await session.execute(
            text(f"select count(*) from transactions t where {unclassified_base_where}"),
            params,
        )
    ).scalar_one()

    search = (unclassified_search or "").strip()
    if search:
        unclassified_where += (
            "and (coalesce(t.payee_label, '') ilike :search "
            "or coalesce(t.note, '') ilike :search "
            "or coalesce(t.purpose_raw, '') ilike :search) "
        )
        params["search"] = f"%{search}%"

    unclassified_total = (
        await session.execute(
            text(f"select count(*) from transactions t where {unclassified_where}"),
            params,
        )
    ).scalar_one()
    unclass_res = await session.execute(
        text(
            "select t.id, t.account_id, t.booked_date, t.amount_minor, t.payee_label, "
            "t.note, t.purpose_raw "
            "from transactions t "
            f"where {unclassified_where} "
            "order by t.booked_date desc, t.id desc limit :limit offset :offset"
        ),
        params,
    )
    unclassified = [
        ReviewItemUnclassified(
            transaction_id=r.id,
            account_id=r.account_id,
            booked_date=r.booked_date,
            amount_minor=r.amount_minor,
            payee_label=r.payee_label,
            note=r.note,
            purpose=r.purpose_raw or r.note,
        )
        for r in unclass_res
    ]

    # 2. Moegliche Dubletten aus Staging - MIT der Buchung, gegen die sie als
    #    Dublette gelten. Ohne sie ist die Frage nicht zu beantworten (siehe
    #    ReviewCounterpart); die Oberflaeche zeigte lange nur die neue Zeile.
    #
    #    `not t.is_archived`: eine Warnung gegen eine Buchung, die nicht mehr im
    #    Bestand steht, ist gegenstandslos. Beim Entfernen einer Buchung faellt
    #    die Zeile ohnehin auf 'new' zurueck (transaction_detach_service), der
    #    Filter faengt nur Altbestaende ab.
    dup_res = await session.execute(
        text(
            "select r.id, r.import_run_id, r.booked_date, r.amount_minor, r.payee_raw, "
            "r.purpose, r.duplicate_of_transaction_id, "
            "t.booked_date as gegen_datum, t.amount_minor as gegen_betrag, "
            "t.payee_label as gegen_empfaenger, t.purpose_raw as gegen_zweck, "
            "t.source as gegen_quelle, a.name as gegen_konto "
            "from import_rows r "
            "left join transactions t on t.id = r.duplicate_of_transaction_id "
            "  and t.household_id = r.household_id and not t.is_archived "
            "left join accounts a on a.id = t.account_id "
            "where r.household_id = :household_id and r.status = 'duplicate_possible' "
            "order by r.row_number asc limit 50"
        ),
        {"household_id": str(household_id)},
    )
    duplicates = [
        ReviewItemDuplicate(
            import_row_id=r.id,
            import_run_id=r.import_run_id,
            booked_date=r.booked_date,
            amount_minor=r.amount_minor,
            payee_raw=r.payee_raw,
            purpose=r.purpose,
            duplicate_of_transaction_id=r.duplicate_of_transaction_id,
            duplicate_of=(
                ReviewCounterpart(
                    transaction_id=r.duplicate_of_transaction_id,
                    booked_date=r.gegen_datum,
                    amount_minor=r.gegen_betrag,
                    payee_label=r.gegen_empfaenger,
                    purpose=r.gegen_zweck,
                    account_name=r.gegen_konto,
                    source=r.gegen_quelle,
                )
                if r.gegen_datum is not None
                else None
            ),
        )
        for r in dup_res
    ]

    # 3. Transfer-Kandidaten (nur Zeilen mit bekannter Gegenbuchung)
    tr_res = await session.execute(
        text(
            "select id, import_run_id, booked_date, amount_minor, payee_raw, "
            "duplicate_of_transaction_id from import_rows "
            "where household_id = :household_id and status = 'transfer_candidate' "
            "and duplicate_of_transaction_id is not null "
            "order by row_number asc limit 50"
        ),
        {"household_id": str(household_id)},
    )
    transfers = [
        ReviewItemTransfer(
            import_row_id=r.id,
            candidate_transaction_id=r.duplicate_of_transaction_id,
            booked_date=r.booked_date,
            amount_minor=r.amount_minor,
            payee_label=r.payee_raw,
        )
        for r in tr_res
    ]

    # 4. Erkannte Vertraege (Erkennung v2). Der Import steht bewusst hier und
    # nicht am Dateikopf: `contract_detection_service` haengt ueber
    # `contract_matching_service` wieder an diesem Modul, und der Kreis waere
    # auf Modulebene nicht aufloesbar (siehe Modul-Docstring).
    from app.services import contract_detection_service

    candidates = await contract_detection_service.get_contract_candidates(
        session, household_id
    )
    contract_suggestions = [contract_suggestion_item(item) for item in candidates]

    # 5. Doppelt gefuehrte Vertraege aus dem Altbestand.
    contract_duplicates = await get_contract_duplicate_suggestions(session, household_id)

    # 6. Vertraege, deren Zahlung mehrfach ausgeblieben ist. Gekuendigte
    # Vertraege werden mitgefragt: sie laufen bis `ends_on` weiter und kosten
    # bis dahin Geld - bleibt die Abbuchung aus, ist das Ende frueher als
    # erwartet eingetreten. Eine eigene Ausblendliste braucht es nicht: jede der
    # drei Antworten aendert Status oder Fehlzaehler und nimmt die Zeile damit
    # aus dieser Abfrage heraus.
    contract_res = await session.execute(
        text(
            "select c.id, c.name, c.provider_label, c.amount_minor, c.direction, "
            "c.frequency, c.missed_periods, c.last_seen_date, c.next_due_date "
            "from contracts c "
            "where c.household_id = :household_id "
            "and c.status in ('active', 'cancelled') "
            "and c.missed_periods >= :min_missed "
            "order by c.missed_periods desc, c.name asc limit 50"
        ),
        {
            "household_id": str(household_id),
            "min_missed": _CONTRACT_ENDED_CHECK_MIN_MISSED,
        },
    )
    contract_ended_checks = [
        ReviewItemContractEndedCheck(
            contract_id=r.id,
            name=r.name,
            provider_label=r.provider_label,
            amount_minor=int(r.amount_minor),
            direction=r.direction,
            frequency=r.frequency,
            missed_periods=int(r.missed_periods),
            last_seen_date=r.last_seen_date,
            next_due_date=r.next_due_date,
        )
        for r in contract_res
    ]

    # 7. Vertraege, deren Betrag sich erheblich geaendert hat. Eine eigene
    # Ausblendliste braucht es auch hier nicht: die Antwort setzt
    # `contract_events.acknowledged_at` und nimmt die Zeile damit aus der
    # Abfrage.
    contract_amount_change_checks = await get_contract_amount_change_checks(
        session, household_id
    )

    # `recurring_suggestions` geht ausdruecklich NICHT in die Summe ein: die
    # Liste bleibt leer und beide zu addieren wuerde jeden Kandidaten doppelt
    # zaehlen.
    booked_duplicates = await scan_booked_duplicates(session, household_id)
    total = (
        unclassified_open_total
        + booked_duplicates.total_count
        + len(duplicates)
        + len(transfers)
        + len(contract_suggestions)
        + len(contract_duplicates)
        + len(contract_ended_checks)
        + len(contract_amount_change_checks)
    )
    return ReviewInboxResponse(
        total_count=total,
        unclassified_open_count=unclassified_open_total,
        unclassified_total_count=unclassified_total,
        unclassified_offset=unclassified_offset,
        unclassified_limit=unclassified_limit,
        unclassified=unclassified,
        possible_duplicates=duplicates,
        booked_duplicates=booked_duplicates.items,
        booked_duplicate_count=booked_duplicates.total_count,
        transfer_candidates=transfers,
        # Dauerhaft leer. Der Typ `recurring_suggestion` ist durch
        # `contract_suggestion` abgeloest; er kennt weder Vertragsschluessel
        # noch Preisstufen, Vertrags-Score oder halbjaehrlichen Rhythmus und
        # wuerde einen Teil der Kandidaten still verfaelschen. Als
        # ANTWORTFELD bleibt er, damit ausgelieferte Clients nicht an einer
        # fehlenden Liste scheitern; als EINGABE bleibt `AcceptRecurring`
        # erhalten, weil ein halb ausgerollter Client den alten Typ noch
        # senden kann.
        recurring_suggestions=[],
        contract_ended_checks=contract_ended_checks,
        contract_suggestions=contract_suggestions,
        contract_duplicate_suggestions=contract_duplicates,
        contract_amount_change_checks=contract_amount_change_checks,
    )


async def match_reimbursement_rule_for_transaction(
    session: AsyncSession,
    household_id: UUID,
    payee_label: str | None,
    purpose: str | None,
    amount_minor: int,
    account_id: UUID,
) -> Any | None:
    """Zweiter, unabhaengiger Durchlauf - nur ueber Regeln mit Erstattungsaktion.

    Die Kategorisierung endet beim ersten Treffer (`match_rules_for_transaction`).
    Erstattungsfaehigkeit ist dazu orthogonal: die Regel, die die Kategorie
    setzt, muss nicht die sein, die etwas ueber die Erstattung weiss. Ein
    eigener Durchlauf mit demselben Praedikat haelt beide Entscheidungen
    unabhaengig, ohne das Verhalten des ersten zu aendern.

    Nur Abgaenge kommen in Frage - eine Erstattungserwartung setzt voraus, dass
    etwas bezahlt wurde. Das spart den Datenbanktrigger als Fehlerquelle im
    Importpfad, wo eine Abweisung den ganzen Lauf treffen wuerde.
    """
    if amount_minor >= 0:
        return None
    rows = (
        await session.execute(
            text(
                "select id, name, action_reimbursable, action_expectation_mode, "
                "action_expectation_rate, action_expectation_minor, "
                "match_payee_raw, match_payee_exact_word, match_purpose, "
                "match_purpose_exact_word, exclude_payee_raw, exclude_purpose, "
                "match_min_amount_minor, match_max_amount_minor, match_amount_mode, "
                "match_account_id from classification_rules "
                "where household_id = :household_id and is_active = true "
                "and action_reimbursable is not null "
                "order by priority desc, created_at asc"
            ),
            {"household_id": str(household_id)},
        )
    ).all()
    for rule in rows:
        if (
            evaluate_rule(
                rule,
                payee_label=payee_label,
                purpose=purpose,
                amount_minor=amount_minor,
                account_id=account_id,
            )
            is not None
        ):
            return rule
    return None
