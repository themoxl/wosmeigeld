"""Fachlogik des Policenregisters (docs/VERSICHERUNGEN-PLAN.md, Phase P1).

Vier Regeln stehen ueber allem und erklaeren fast jede Besonderheit dieser
Datei:

1. **Hier entsteht kein Geld.** Der Beitrag lebt in `contracts`; an der Police
   steht hoechstens ein Informationsbetrag eines FREMDEN Zahlers. Es gibt genau
   drei Geldfaelle - Vertrag, Fremdbetrag, keiner - und keinen vierten.
2. **Jedes Datum traegt seine Herkunft.** `main_due_source` und
   `notice_terms_source` setzt der Datenbanktrigger, nie dieser Dienst. Fuer die
   Bestaetigung setzt er lediglich VOR dem UPDATE die Sitzungsvariable, die der
   Trigger liest. Deshalb stehen die sechs Herkunftsspalten in keinem der beiden
   Spaltentupel (Plan 4.11).
3. **Ein angenommenes Datum bekommt keinen Countdown.** Der Rechenkern liefert
   `days_left` unabhaengig von der Sicherheit; dieser Dienst gibt ihn nur bei
   `certainty = 'confirmed'` nach aussen. Ein Client, der die Zahl rendert,
   koennte sonst eine Annahme wie einen belegten Termin aussehen lassen.
4. **Wo eine Angabe fehlt, sagt das Werkzeug das.** Kein Fristdatum wird
   geraten; "nicht berechenbar" ist eine Aussage, kein fehlender Wert.

Der Fristenrechner steht bewusst woanders (`insurance_deadline_service`): er ist
eine reine Funktion ohne Datenbank, damit Liste, Detail und spaeter der
Nachtlauf dieselbe Antwort geben.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any, Final, Literal, NamedTuple, cast
from uuid import UUID

from fastapi import HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import Row, text
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.concurrency import (
    IF_MATCH_REQUIRED,
    INVALID_IF_MATCH,
    VERSION_CONFLICT,
)
from app.schemas.contract import ContractStatus
from app.schemas.insurance import (
    BackfillPreview,
    BackfillPreviewItem,
    BackfillResult,
    ConfirmLine,
    ConfirmMainDue,
    ConfirmNoticeTerms,
    ContractCandidate,
    InsuranceCoverageKey,
    InsuranceLine,
    InsurancePolicy,
    InsurancePolicyCreate,
    InsurancePolicyUpdate,
    InsuranceSettings,
    InsuranceSummary,
    InsuranceTask,
    LinkContract,
    PolicyDeadline,
    PolicyPremium,
    PolicyPurpose,
    PolicyStatus,
    PolicyStatusChange,
)
from app.services import contract_matching_service, contract_service
from app.services.insurance_deadline_service import (
    InsuranceDeadline,
    compute_insurance_deadline,
    min_term_end_on,
    shifts_for,
)

# ---------------------------------------------------------------------------
# Fehlercodes
# ---------------------------------------------------------------------------

INSURANCE_POLICY_NOT_FOUND: Final = "insurance_policy_not_found"
INSURANCE_LINE_UNKNOWN: Final = "insurance_line_unknown"
INSURANCE_ATTR_UNKNOWN: Final = "insurance_attr_unknown"
INSURANCE_PREMIUM_SOURCE_CONFLICT: Final = "insurance_premium_source_conflict"
INSURANCE_PREMIUM_PAYER_INCOMPLETE: Final = "insurance_premium_payer_incomplete"
INSURANCE_PREMIUM_SHARE_EXCEEDED: Final = "insurance_premium_share_exceeded"
INSURANCE_CONTRACT_ALREADY_LINKED: Final = "insurance_contract_already_linked"
INSURANCE_CONTRACT_NOT_FOUND: Final = "insurance_contract_not_found"
INSURANCE_POLICY_NUMBER_DUPLICATE: Final = "insurance_policy_number_duplicate"
INSURANCE_MISSING_MAIN_DUE_ON: Final = "insurance_missing_main_due_on"
# Nicht im Fehlerkatalog des Plans, aber unvermeidlich: der Trigger stuft die
# Herkunft eines Datums bei einem gewoehnlichen UPDATE nicht hoch, und der CHECK
# insurance_policies_main_due_source_pair verbietet ein Datum ohne Herkunft. Ein
# PATCH, der die erste Hauptfaelligkeit eintraegt, liefe deshalb in 23514. Der
# Weg dafuer ist die Einfeldaktion - "erfassen" und "bestaetigen" sind
# derselbe Schreibweg, und genau so ist es gewollt.
INSURANCE_MAIN_DUE_NEEDS_CONFIRMATION: Final = "insurance_main_due_needs_confirmation"
# Beitragsfrei gestellt heisst: es fliesst kein Beitrag (CHECK
# insurance_policies_paid_up_pair). Der Fremdbetrag muss vorher weg.
INSURANCE_PAID_UP_WITH_EXTERNAL_PREMIUM: Final = "insurance_paid_up_with_external_premium"
INSURANCE_LINE_STILL_UNKNOWN: Final = "insurance_line_still_unknown"
INSURANCE_CANCELLED_NEEDS_DATE: Final = "insurance_cancelled_needs_date"
# Der Anker 'fixed_date' ohne Monat und Tag (CHECK
# insurance_policies_fixed_date_pair). Beides kommt aus der Sparte; wer den
# Anker an einer Sparte ohne Kalenderfixpunkt verlangt, verlangt einen Stichtag,
# den niemand nennen kann.
INSURANCE_FIXED_DATE_UNKNOWN: Final = "insurance_fixed_date_unknown"

# ---------------------------------------------------------------------------
# Konstanten
# ---------------------------------------------------------------------------

# Statuswerte, in denen eine Police fachlich lebt. Deckungsgleich mit dem Satz,
# den insurance_deadline_service ueberhaupt rechnet - eine beendete Police
# bekommt weder Frist noch Aufgabe noch Beitragssumme.
_LIVE_STATUSES: Final = ("active", "suspended", "paid_up", "dormant")

# 'entered_in_error' ist der Grabstein einer irrtuemlich erfassten Police. Er
# bleibt lesbar, zaehlt aber in keiner Kennzahl mit.
_COUNTED_STATUSES: Final = (
    "draft",
    "applied",
    "active",
    "suspended",
    "paid_up",
    "dormant",
    "cancelled",
    "ended",
)

# Fristanker, die ohne Hauptfaelligkeit rechnen. Alle uebrigen brauchen sie -
# und ohne sie gibt es eine Rueckfrage statt eines Datums.
_ANCHORS_WITHOUT_MAIN_DUE: Final = ("month_end", "per_event", "none")

# Feste Spaltenliste statt der Payload-Schluessel, damit kein Feldname ungeprueft
# in ein SET-Fragment wandert (gleiches Muster wie routes/contracts.py). Die
# sechs Herkunftsspalten fehlen hier absichtlich.
_POLICY_UPDATABLE_COLUMNS: Final = (
    "line_key",
    "insurer_name",
    "label",
    "policy_number",
    "customer_number",
    "tariff_name",
    "purpose_note",
    "starts_on",
    "ends_on",
    "main_due_on",
    "notice_anchor",
    "notice_period_months",
    "min_term_months",
    "renewal_term_months",
    "waiting_period_days",
    "sum_insured_minor",
    "deductible_minor",
    "broker_name",
    "broker_phone",
    "claims_phone",
    "service_channel",
    "recipient_state",
    "dynamic_agreed",
    "attrs",
    "note",
)

_JSON_COLUMNS: Final = frozenset({"attrs"})

_LINE_COLUMNS: Final = """
  line_key, risk_field, gdv_sparte, label, purpose_short, purpose_long,
  typically_covers, typically_not_covers, is_placeholder, need_tier, need_reason,
  source_url, source_checked_on, default_notice_anchor, default_anchor_basis_kind,
  default_notice_period_months, default_main_due_md, default_fixed_month,
  default_fixed_day, default_min_term_months, default_renewal_term_months,
  default_waiting_days, default_deadline_kind, special_law, completeness_fields,
  covered_by_keys, requires_object_kind, attr_schema, sort_order
"""

# Der Katalogblock reist bei jeder Police mit: Zwecktext und Fristvorgabe sind
# in Liste UND Detail sichtbar (Plan 2.2, Festlegung 1), ein zweiter Rundlauf
# je Zeile waere N+1.
_POLICY_COLUMNS: Final = """
  p.id, p.line_key, l.label as line_label, l.purpose_short as line_purpose_short,
  l.risk_field, l.sort_order as line_sort_order, l.completeness_fields,
  l.default_notice_anchor, l.default_notice_period_months, l.default_main_due_md,
  l.default_fixed_month, l.default_fixed_day,
  p.label, p.insurer_name, p.insurer_template_key, p.policy_number,
  p.customer_number, p.tariff_name,
  p.purpose_note, p.status, p.starts_on, p.ends_on, p.main_due_on,
  p.main_due_source, p.main_due_confirmed_at, p.notice_anchor, p.anchor_basis_kind,
  p.notice_period_months, p.notice_fixed_month, p.notice_fixed_day,
  p.notice_terms_source, p.deadline_kind, p.min_term_months, p.renewal_term_months,
  p.waiting_period_days, p.sum_insured_minor, p.sum_insured_kind, p.deductible_minor,
  p.broker_name, p.broker_phone, p.claims_phone, p.service_channel, p.recipient_state,
  p.cancelled_on, p.cancellation_reason, p.dormant_until, p.paid_up_benefit_minor,
  p.dynamic_agreed, p.attrs, p.data_source, p.line_confirmed_at, p.line_deferred_at,
  p.note, p.parent_policy_id, p.is_health_scoped, p.version, p.created_at, p.updated_at,
  p.contract_id, p.external_premium_minor, p.external_frequency, p.premium_payer,
  p.premium_payer_note, p.premium_share_bp,
  c.name as contract_name, c.amount_minor as contract_amount_minor,
  c.frequency as contract_frequency, c.status as contract_status,
  c.direction as contract_direction, c.starts_on as contract_starts_on,
  c.ends_on as contract_ends_on, coalesce(c.version, 0) as contract_version
"""

# Ein zusammengefuehrter Vertrag (merged_into_id) ist ueber die normale API
# unsichtbar; er darf auch hier keinen Beitrag mehr beisteuern. Die Police faellt
# dann in der Anzeige auf "kein Betrag hinterlegt" zurueck, statt einen Wert aus
# einem Grabstein zu zeigen.
_POLICY_FROM: Final = """
  from insurance_policies p
  join insurance_lines l on l.line_key = p.line_key
  left join contracts c
    on c.id = p.contract_id and c.household_id = p.household_id
   and c.merged_into_id is null
"""


# ---------------------------------------------------------------------------
# Ergaenzende Vertraege
# ---------------------------------------------------------------------------
# Diese Modelle stehen hier und nicht in app/schemas/insurance.py, weil der
# Arbeitsschritt P1-Backend die Schemadatei nicht anfasst. Sie gehoeren
# fachlich dorthin und sollten beim naechsten Schnitt umziehen.


class InsuranceLineDetail(InsuranceLine):
    """Sparte samt `attr_schema` - die Formularerzeugung braucht beides."""

    attr_schema: dict[str, Any] = Field(default_factory=dict)


class InsurancePolicyDetail(BaseModel):
    """Police, Katalogblock und die noch fehlenden Pflichtfelder."""

    policy: InsurancePolicy
    line: InsuranceLineDetail
    completeness_missing: list[str]


class PolicyStatusChangeRequest(PolicyStatusChange):
    """Statuswechsel mit optionaler Vertragsentscheidung.

    Ohne `contract_status` antwortet der Wechsel nach `cancelled`, `ended`,
    `paid_up` oder `dormant` mit einer Vorschau statt zu schreiben (Plan 4.16):
    er wuerde sonst die Fixkosten still auseinanderlaufen lassen - Police
    gekuendigt, Vertrag aktiv.
    """

    contract_status: ContractStatus | None = None


class PolicyStatusOutcome(BaseModel):
    """Vorher/Nachher fuer beide Objekte - als Vorschau oder als Ergebnis."""

    applied: bool
    policy_status_from: PolicyStatus
    policy_status_to: PolicyStatus
    contract_id: UUID | None = None
    contract_name: str | None = None
    contract_status_from: ContractStatus | None = None
    contract_status_to: ContractStatus | None = None
    hint: str = ""
    policy: InsurancePolicy | None = None


class PolicyWithEtag(NamedTuple):
    """Police und ihr zusammengesetztes ETag - das ETag braucht die
    Vertragsversion, die im Antwortmodell bewusst nicht steht."""

    policy: InsurancePolicy
    etag: str


# ---------------------------------------------------------------------------
# Zusammengesetztes ETag (Plan 4.11)
# ---------------------------------------------------------------------------


def policy_etag(contract_version: int, policy_version: int) -> str:
    """`W/"<Vertragsversion>.<Policenversion>"`, ohne Vertrag `W/"0.<n>"`.

    Ein ETag allein aus der Policenversion waere blind fuer eine parallele
    Betragsaenderung am Vertrag - und genau die aendert, was auf der Police
    steht.
    """
    return f'W/"{contract_version}.{policy_version}"'


def parse_policy_if_match(
    if_match: str | None, *, required: bool = True
) -> tuple[int, int] | None:
    """Liest das Versionspaar aus `If-Match`.

    Akzeptiert `W/"3.5"`, `"3.5"` und `3.5`. Ein fehlender Header liefert bei
    `required=True` 428 - der Client hat nichts falsch formuliert, ihm fehlt nur
    die Vorbedingung (gleiche Linie wie `core.concurrency.parse_if_match`).
    """
    if if_match is None:
        if required:
            raise HTTPException(
                status_code=status.HTTP_428_PRECONDITION_REQUIRED, detail=IF_MATCH_REQUIRED
            )
        return None

    candidate = if_match.strip()
    if candidate.startswith("W/"):
        candidate = candidate[2:].strip()
    candidate = candidate.strip('"')
    parts = candidate.split(".")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=INVALID_IF_MATCH)
    return int(parts[0]), int(parts[1])


def _assert_policy_version(data: dict[str, Any], expected: tuple[int, int] | None) -> None:
    current = (int(data["contract_version"]), int(data["version"]))
    if expected is not None and current != expected:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=VERSION_CONFLICT,
            headers={"ETag": policy_etag(*current)},
        )


# ---------------------------------------------------------------------------
# Katalog
# ---------------------------------------------------------------------------


async def list_lines(
    session: AsyncSession, *, risk_field: str | None = None
) -> list[InsuranceLine]:
    rows = (
        await session.execute(
            text(
                f"select {_LINE_COLUMNS} from insurance_lines "
                "where is_active and (cast(:risk_field as text) is null "
                "  or risk_field = cast(:risk_field as text)) "
                "order by sort_order, label"
            ),
            {"risk_field": risk_field},
        )
    ).all()
    return [InsuranceLine(**row._mapping) for row in rows]


async def load_line(session: AsyncSession, line_key: str) -> Row[Any] | None:
    result = await session.execute(
        text(f"select {_LINE_COLUMNS} from insurance_lines where line_key = :line_key"),
        {"line_key": line_key},
    )
    return result.first()


async def get_line(session: AsyncSession, line_key: str) -> InsuranceLineDetail:
    row = await load_line(session, line_key)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=INSURANCE_LINE_UNKNOWN
        )
    return InsuranceLineDetail(**row._mapping)


async def list_coverage_keys(
    session: AsyncSession, *, line_key: str | None = None
) -> list[InsuranceCoverageKey]:
    rows = (
        await session.execute(
            text(
                "select coverage_key, label, purpose_short, applies_to, sort_order "
                "from insurance_coverage_keys "
                "where cast(:line_key as text) is null "
                "  or cast(:line_key as text) = any(applies_to) "
                "order by sort_order, label"
            ),
            {"line_key": line_key},
        )
    ).all()
    return [InsuranceCoverageKey(**row._mapping) for row in rows]


# ---------------------------------------------------------------------------
# Ableitungen je Police
# ---------------------------------------------------------------------------


def _contract_counts(data: dict[str, Any], *, today: date) -> bool:
    """Zaehlt der verknuepfte Vertrag heute in die Fixkosten?

    Dieselbe Regel wie `contract_service.build_summary`: gekuendigte Vertraege
    kosten bis zum Enddatum weiter, pausierte und beendete nicht, ein noch nicht
    begonnener kostet heute nichts. Ohne diese Gleichheit koennte die
    Policenliste eine andere Jahressumme zeigen als die Vertragsseite.
    """
    if data.get("contract_status") not in ("active", "cancelled"):
        return False
    if data.get("contract_direction") != "expense":
        return False
    ends_on = data.get("contract_ends_on")
    if ends_on is not None and ends_on < today:
        return False
    starts_on = data.get("contract_starts_on")
    return not (starts_on is not None and starts_on > today)


def _premium(data: dict[str, Any], *, today: date) -> PolicyPremium:
    share_bp = int(data.get("premium_share_bp") or 10000)
    if data.get("contract_id") is not None:
        amount = data.get("contract_amount_minor")
        frequency = data.get("contract_frequency")
        yearly: int | None = None
        if amount is not None and frequency is not None and _contract_counts(data, today=today):
            # Nur was tatsaechlich in den Fixkosten steht - der Feldname ist
            # eine Zusage, keine Beschreibung des Vertragsbetrags.
            yearly = round(
                contract_service.yearly_minor(int(amount), str(frequency)) * share_bp / 10000
            )
        return PolicyPremium(
            kind="contract",
            contract_id=data["contract_id"],
            contract_name=data.get("contract_name"),
            contract_amount_minor=int(amount) if amount is not None else None,
            contract_frequency=str(frequency) if frequency is not None else None,
            premium_share_bp=share_bp,
            yearly_in_fixed_costs_minor=yearly,
        )
    if data.get("external_premium_minor") is not None:
        return PolicyPremium(
            kind="external",
            external_premium_minor=int(data["external_premium_minor"]),
            external_frequency=data.get("external_frequency"),
            premium_payer=data.get("premium_payer"),
            premium_payer_note=data.get("premium_payer_note"),
            premium_share_bp=share_bp,
        )
    return PolicyPremium(kind="none", premium_share_bp=share_bp)


def _deadline(data: dict[str, Any], *, today: date) -> PolicyDeadline:
    # Empfaengerland und Mindestbindung gehen MIT in die Rechnung. Ohne sie
    # nannte dieselbe Police auf /fixkosten und auf /versicherungen
    # verschiedene Kuendigungstermine - der Vertragsweg reichte beides schon
    # durch, dieser hier nicht. Die Angaben stehen in derselben Zeile; sie
    # wegzulassen hiess, den Feiertag am Sitz des Versicherers zu ignorieren
    # und eine vereinbarte Bindung zu uebergehen.
    computed = compute_insurance_deadline(
        anchor=data["notice_anchor"],
        deadline_kind=data["deadline_kind"],
        today=today,
        main_due_on=data.get("main_due_on"),
        notice_period_months=data.get("notice_period_months"),
        notice_fixed_month=data.get("notice_fixed_month"),
        notice_fixed_day=data.get("notice_fixed_day"),
        service_channel=data.get("service_channel") or "post",
        main_due_source=data["main_due_source"],
        notice_terms_source=data["notice_terms_source"],
        status=data["status"],
        ends_on=data.get("ends_on"),
        recipient_state=data.get("recipient_state"),
        min_term_ends_on=min_term_end_on(
            # Der Beginn der Police schlaegt den des Vertrags - die Bindung ist
            # an der Police vereinbart, nicht an der Zahlung.
            data.get("starts_on") or data.get("contract_starts_on"),
            data.get("min_term_months"),
        ),
    )
    return PolicyDeadline(
        anchor=computed.anchor,
        anchor_basis_kind=data["anchor_basis_kind"],
        deadline_kind=computed.deadline_kind,
        certainty=computed.certainty,
        term_ends_on=computed.term_ends_on,
        deadline_legal_on=computed.deadline_legal_on,
        safe_service_on=computed.safe_service_on,
        recommended_action_on=computed.recommended_action_on,
        # Leitsatz: ein angenommenes Datum bekommt nie einen Countdown. Der
        # Rechenkern liefert die Zahl trotzdem - er kennt die Anzeige nicht -,
        # die API gibt sie nur bei belegter Frist heraus.
        days_left=computed.days_left if computed.certainty == "confirmed" else None,
        reason=computed.reason,
        safe_service_holiday=_safe_service_holiday(computed),
    )


def _safe_service_holiday(computed: InsuranceDeadline) -> str | None:
    """Name des Feiertags, der den sicheren Zustelltag verschoben hat.

    Nur der erste: die Herleitung nennt einen Grund, keine Liste. Wochenenden
    bleiben draussen - sie verschieben zwar auch, sind aber keine Auskunft, die
    jemand nachschlagen muesste.
    """
    for shift in shifts_for(computed, "safe_service"):
        if shift.cause == "holiday":
            return shift.label
    return None


def _completeness(data: dict[str, Any]) -> tuple[int, int, list[str]]:
    """Anteil der ausgefuellten Pflichtfelder DIESER Sparte.

    Der Nenner kommt aus `insurance_lines.completeness_fields`; ohne ihn waere
    "7 von 12" eine erfundene Zahl. Ein Feld, das die Sparte verlangt, dieses
    Modell aber noch nicht kennt (Personen und Objekte ab P4), zaehlt als
    fehlend - das ist ehrlicher, als den Nenner zu kuerzen.
    """
    fields: list[str] = list(data.get("completeness_fields") or [])
    missing = [field for field in fields if _is_blank(data.get(field))]
    return len(fields) - len(missing), len(fields), missing


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    return isinstance(value, str) and not value.strip()


def _needs_main_due(data: dict[str, Any]) -> bool:
    """Fehlt an dieser Police die Hauptfaelligkeit?

    Bewusst nicht am gespeicherten Anker allein festgemacht: eine uebernommene
    Police traegt 'none', WEIL das Datum fehlt - genau sie ist gemeint. Gefragt
    wird deshalb zusaetzlich der Katalog. Nur wo die Sparte rollierend (GKV,
    Monatsende) oder gar nicht ordentlich kuendbar ist, gibt es nichts zu
    erfassen.
    """
    if data.get("main_due_on") is not None:
        return False
    anchor = str(data["notice_anchor"])
    if anchor == "none":
        anchor = str(data.get("default_notice_anchor") or "none")
    if anchor in ("month_end", "per_event"):
        return False
    return not (anchor == "fixed_date" and data.get("notice_fixed_month") is not None)


def _next_task(data: dict[str, Any]) -> str | None:
    """Die eine konkrete naechste Handlung - oder nichts.

    Bewusst ein Satz statt eines Status: "aktiv" beantwortet keine Frage.
    """
    if data["status"] not in _LIVE_STATUSES:
        return None
    if data["line_key"] == "unknown" and data.get("line_deferred_at") is None:
        return "Sparte zuordnen"
    if _needs_main_due(data):
        return "Hauptfaelligkeit erfassen"
    if data["main_due_source"] == "catalog_suggested":
        return "Hauptfaelligkeit bestaetigen"
    if data["notice_terms_source"] == "derived_from_days":
        return "Bezugspunkt der Frist bestaetigen"
    return None


def _as_policy(row: Row[Any], *, today: date) -> InsurancePolicy:
    data: dict[str, Any] = dict(row._mapping)
    filled, total, _missing = _completeness(data)
    return InsurancePolicy(
        **data,
        premium=_premium(data, today=today),
        deadline=_deadline(data, today=today),
        completeness_filled=filled,
        completeness_total=total,
        next_task=_next_task(data),
    )


# ---------------------------------------------------------------------------
# Lesen
# ---------------------------------------------------------------------------


async def _require_policy_row(
    session: AsyncSession, household_id: UUID, policy_id: UUID
) -> Row[Any]:
    result = await session.execute(
        text(
            f"select {_POLICY_COLUMNS} {_POLICY_FROM} "
            "where p.id = :id and p.household_id = :household_id"
        ),
        {"id": str(policy_id), "household_id": str(household_id)},
    )
    row = result.first()
    if row is None:
        # 404 statt 403 bei fremdem Haushalt: die Existenz einer fremden
        # Ressource soll nicht verraten werden (gleiche Linie wie contract_service).
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=INSURANCE_POLICY_NOT_FOUND
        )
    return row


async def _reload(
    session: AsyncSession, household_id: UUID, policy_id: UUID, *, today: date
) -> PolicyWithEtag:
    row = await _require_policy_row(session, household_id, policy_id)
    data = dict(row._mapping)
    return PolicyWithEtag(
        policy=_as_policy(row, today=today),
        etag=policy_etag(int(data["contract_version"]), int(data["version"])),
    )


async def get_policy(
    session: AsyncSession, household_id: UUID, policy_id: UUID, *, today: date
) -> PolicyWithEtag:
    return await _reload(session, household_id, policy_id, today=today)


async def get_policy_detail(
    session: AsyncSession, household_id: UUID, policy_id: UUID, *, today: date
) -> tuple[InsurancePolicyDetail, str]:
    row = await _require_policy_row(session, household_id, policy_id)
    data = dict(row._mapping)
    _filled, _total, missing = _completeness(data)
    line = await get_line(session, str(data["line_key"]))
    detail = InsurancePolicyDetail(
        policy=_as_policy(row, today=today),
        line=line,
        completeness_missing=missing,
    )
    return detail, policy_etag(int(data["contract_version"]), int(data["version"]))


async def list_policies(
    session: AsyncSession,
    household_id: UUID,
    *,
    today: date,
    policy_status: str | None = None,
    line_key: str | None = None,
    risk_field: str | None = None,
    search: str | None = None,
    premium_source: Literal["contract", "external", "none"] | None = None,
    include_ended: bool = False,
    sort: Literal["deadline", "yearly", "line", "name"] = "line",
) -> list[InsurancePolicy]:
    conditions = ["p.household_id = :household_id"]
    params: dict[str, Any] = {"household_id": str(household_id)}

    if policy_status is not None:
        conditions.append("p.status = :status")
        params["status"] = policy_status
    elif not include_ended:
        # Ohne diesen Ausschluss waechst die Liste mit jeder beendeten Police
        # dauerhaft an - dieselbe Voreinstellung wie bei den Vertraegen.
        conditions.append("p.status not in ('ended', 'entered_in_error')")
    if line_key is not None:
        conditions.append("p.line_key = :line_key")
        params["line_key"] = line_key
    if risk_field is not None:
        conditions.append("l.risk_field = :risk_field")
        params["risk_field"] = risk_field
    if search:
        conditions.append(
            "(p.insurer_name ilike :search or coalesce(p.label, '') ilike :search "
            "or coalesce(p.policy_number, '') ilike :search "
            "or coalesce(p.tariff_name, '') ilike :search)"
        )
        params["search"] = f"%{search.strip()}%"
    if premium_source == "contract":
        conditions.append("p.contract_id is not null")
    elif premium_source == "external":
        conditions.append("p.external_premium_minor is not null")
    elif premium_source == "none":
        conditions.append("p.contract_id is null and p.external_premium_minor is null")

    rows = (
        await session.execute(
            text(
                f"select {_POLICY_COLUMNS} {_POLICY_FROM} where "
                + " and ".join(conditions)
                + " order by l.sort_order, lower(p.insurer_name), p.created_at"
            ),
            params,
        )
    ).all()
    policies = [_as_policy(row, today=today) for row in rows]
    return _sorted(policies, sort)


# Sortierung nach Frist: eine bestaetigte Frist steht IMMER vor einer
# angenommenen - ein angenommenes Datum darf keinen belegten Termin verdraengen.
_CERTAINTY_RANK: Final[dict[str, int]] = {"confirmed": 0, "assumed": 1, "not_computable": 2}


def _sorted(policies: list[InsurancePolicy], sort: str) -> list[InsurancePolicy]:
    if sort == "deadline":
        return sorted(
            policies,
            key=lambda item: (
                _CERTAINTY_RANK.get(item.deadline.certainty, 3),
                item.deadline.recommended_action_on or date.max,
                item.insurer_name.lower(),
            ),
        )
    if sort == "yearly":
        return sorted(
            policies,
            key=lambda item: (
                -(item.premium.yearly_in_fixed_costs_minor or 0),
                item.insurer_name.lower(),
            ),
        )
    if sort == "name":
        return sorted(policies, key=lambda item: (item.label or item.insurer_name).lower())
    return policies


async def get_purpose(
    session: AsyncSession, household_id: UUID, policy_id: UUID
) -> PolicyPurpose:
    """Der Zwecktext - aus dem Katalog, solange die Police nichts Eigenes sagt.

    Ab P4 gibt es erfasste Deckungsbausteine, und die sind die staerkere
    Aussage: der Katalog beschreibt die SPARTE ("typischerweise"), ein erfasster
    Baustein beschreibt DIESE Police. Sobald einer da ist, kippt
    `coverage_source` auf 'policy', und die beiden Spalten kommen aus
    `insurance_coverages` statt aus `insurance_lines` - `is_included`
    entscheidet die Seite.

    Der Vorbehalt (`caveat`) bleibt trotzdem stehen: er gilt weiter fuer
    `purpose_long`, den erfasste Bausteine nicht ersetzen. Genau deshalb ist die
    Herkunft ein eigenes Feld und kein weggelassener Satz - die Oberflaeche
    nimmt den Vorbehalt fuer die Spalten zurueck, nicht fuer den Text.
    """
    row = await _require_policy_row(session, household_id, policy_id)
    data = dict(row._mapping)
    line = await get_line(session, str(data["line_key"]))

    # Beschriftung aus dem Bausteinkatalog, nicht aus dem Schluessel: der Text
    # steht dort einmal und wird hier gelesen, nicht nachgebaut.
    erfasst = (
        await session.execute(
            text(
                "select k.label, cov.is_included from insurance_coverages cov "
                "join insurance_coverage_keys k on k.coverage_key = cov.coverage_key "
                "where cov.policy_id = :policy_id and cov.household_id = :household_id "
                "order by k.sort_order, k.label"
            ),
            {"policy_id": str(policy_id), "household_id": str(household_id)},
        )
    ).all()

    if not erfasst:
        return PolicyPurpose(
            purpose_long=line.purpose_long,
            typically_covers=line.typically_covers,
            typically_not_covers=line.typically_not_covers,
            coverage_source="catalog",
            purpose_note=data.get("purpose_note"),
            source_url=line.source_url,
            source_checked_on=line.source_checked_on,
        )

    return PolicyPurpose(
        purpose_long=line.purpose_long,
        typically_covers=[str(zeile.label) for zeile in erfasst if zeile.is_included],
        typically_not_covers=[str(zeile.label) for zeile in erfasst if not zeile.is_included],
        coverage_source="policy",
        purpose_note=data.get("purpose_note"),
        # Fundstelle und Pruefdatum gehoeren zum Spartentext und bleiben damit
        # an ihm haengen; die erfassten Bausteine belegt der Nutzer selbst.
        source_url=line.source_url,
        source_checked_on=line.source_checked_on,
    )


# ---------------------------------------------------------------------------
# Uebersicht und Aufgaben
# ---------------------------------------------------------------------------


async def _all_rows(session: AsyncSession, household_id: UUID) -> list[Row[Any]]:
    return list(
        (
            await session.execute(
                text(
                    f"select {_POLICY_COLUMNS} {_POLICY_FROM} "
                    "where p.household_id = :household_id "
                    "order by l.sort_order, lower(p.insurer_name)"
                ),
                {"household_id": str(household_id)},
            )
        ).all()
    )


async def build_summary(
    session: AsyncSession, household_id: UUID, *, today: date
) -> InsuranceSummary:
    rows = await _all_rows(session, household_id)

    policy_count = 0
    active_count = 0
    yearly_in_fixed_costs = 0
    yearly_by_others = 0
    breakdown: dict[str, int] = {}
    no_premium_count = 0
    unknown_line_count = 0
    deferred_line_count = 0
    missing_main_due_count = 0
    unconfirmed_main_due_count = 0
    next_deadline_on: date | None = None
    next_deadline_policy_id: UUID | None = None

    for row in rows:
        data = dict(row._mapping)
        if data["status"] not in _COUNTED_STATUSES:
            continue
        policy_count += 1
        if data["status"] == "active":
            active_count += 1
        if data["status"] not in _LIVE_STATUSES:
            continue

        premium = _premium(data, today=today)
        if premium.kind == "contract":
            yearly_in_fixed_costs += premium.yearly_in_fixed_costs_minor or 0
        elif premium.kind == "external":
            # Ein Einmalbeitrag ist kein Jahresbeitrag. Er in eine Jahressumme
            # zu rechnen erfaende eine wiederkehrende Zahlung; ein eigenes Feld
            # dafuer gibt es im P1-Vertrag nicht.
            if premium.external_frequency not in (None, "single"):
                value = contract_service.yearly_minor(
                    int(premium.external_premium_minor or 0), str(premium.external_frequency)
                )
                yearly_by_others += value
                payer = str(premium.premium_payer or "other")
                breakdown[payer] = breakdown.get(payer, 0) + value
        else:
            no_premium_count += 1

        if data["line_key"] == "unknown":
            if data.get("line_deferred_at") is None:
                unknown_line_count += 1
            else:
                deferred_line_count += 1
        if _needs_main_due(data):
            missing_main_due_count += 1
        elif data["main_due_source"] == "catalog_suggested":
            unconfirmed_main_due_count += 1

        deadline = _deadline(data, today=today)
        # Nur belegte Fristen. Eine angenommene als "naechste Frist" auszugeben
        # hiesse, sie in der Kennzahlenleiste mit Countdown zu zeigen.
        if deadline.certainty == "confirmed" and deadline.recommended_action_on is not None:
            if next_deadline_on is None or deadline.recommended_action_on < next_deadline_on:
                next_deadline_on = deadline.recommended_action_on
                next_deadline_policy_id = data["id"]

    return InsuranceSummary(
        policy_count=policy_count,
        active_count=active_count,
        yearly_in_fixed_costs_minor=yearly_in_fixed_costs,
        yearly_paid_by_others_minor=yearly_by_others,
        paid_by_others_breakdown=breakdown,
        no_premium_count=no_premium_count,
        unknown_line_count=unknown_line_count,
        deferred_line_count=deferred_line_count,
        missing_main_due_count=missing_main_due_count,
        unconfirmed_main_due_count=unconfirmed_main_due_count,
        next_deadline_on=next_deadline_on,
        next_deadline_policy_id=next_deadline_policy_id,
    )


def _policy_label(data: dict[str, Any]) -> str:
    label = data.get("label")
    if isinstance(label, str) and label.strip():
        return label.strip()
    return str(data["insurer_name"])


# Reihenfolge der Aufgabenarten aus Plan 2.2. In P1 traegt keine der drei ein
# Verfallsdatum - das kommt erst mit dem Sonderkuendigungsfenster in P3 -,
# deshalb sortiert diese Rangfolge und nicht ein Datum.
_TASK_ORDER: Final[dict[str, int]] = {
    "missing_main_due": 0,
    "unconfirmed_main_due": 1,
    "unknown_line": 2,
}


async def build_tasks(
    session: AsyncSession, household_id: UUID, *, today: date
) -> list[InsuranceTask]:
    """Die Liste hinter "Jetzt wichtig".

    Genau drei Arten: das sind die drei Aufgaben, die die Uebernahme
    hinterlaesst. `missing_premium_source` und `deadline_approaching` bleiben in
    P1 ungenutzt - die erste ist kein Mangel (Fall (c) ist ein zulaessiger
    Zustand), die zweite braucht den Fristenkalender aus P2.
    """
    rows = await _all_rows(session, household_id)
    tasks: list[InsuranceTask] = []

    for row in rows:
        data = dict(row._mapping)
        if data["status"] not in _LIVE_STATUSES:
            continue
        label = _policy_label(data)

        if _needs_main_due(data):
            tasks.append(
                InsuranceTask(
                    kind="missing_main_due",
                    policy_id=data["id"],
                    policy_label=label,
                    what="Hauptfaelligkeit erfassen",
                    why="Ohne Hauptfaelligkeit laesst sich kein Kuendigungstermin berechnen.",
                )
            )
        elif data["main_due_source"] == "catalog_suggested":
            tasks.append(
                InsuranceTask(
                    kind="unconfirmed_main_due",
                    policy_id=data["id"],
                    policy_label=label,
                    what="Hauptfaelligkeit bestaetigen",
                    why="Das Datum ist eine Annahme - bis zur Bestaetigung gibt es "
                    "keinen Countdown.",
                    due_on=data.get("main_due_on"),
                )
            )

        if data["line_key"] == "unknown" and data.get("line_deferred_at") is None:
            tasks.append(
                InsuranceTask(
                    kind="unknown_line",
                    policy_id=data["id"],
                    policy_label=label,
                    what="Sparte zuordnen",
                    why="Ohne Sparte fehlen Zwecktext und Fristvorgaben.",
                )
            )

    return sorted(tasks, key=lambda item: (_TASK_ORDER.get(item.kind, 9), item.policy_label))


# ---------------------------------------------------------------------------
# Schreiben: Hilfen
# ---------------------------------------------------------------------------


def _placeholder(column: str) -> str:
    return f"cast(:{column} as jsonb)" if column in _JSON_COLUMNS else f":{column}"


def _param_value(column: str, value: Any) -> Any:
    if column in _JSON_COLUMNS:
        return json.dumps(value or {})
    return value


async def _assert_attrs(session: AsyncSession, line_key: str, attrs: dict[str, Any]) -> None:
    """Prueft `attrs` gegen das Schema der Sparte - falls es eines gibt.

    Der Kernkatalog traegt bisher ein leeres `attr_schema`. Bei leerem Schema
    jeden Schluessel abzulehnen wuerde das Feld unbenutzbar machen; deshalb
    prueft der Dienst nur, wo der Katalog tatsaechlich etwas festlegt.
    """
    if not attrs:
        return
    row = await load_line(session, line_key)
    if row is None:
        return
    schema = dict(row._mapping).get("attr_schema") or {}
    properties = schema.get("properties") if isinstance(schema, dict) else None
    if not isinstance(properties, dict) or not properties:
        return
    unknown = sorted(key for key in attrs if key not in properties)
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": INSURANCE_ATTR_UNKNOWN, "fields": unknown},
        )


def _effective_anchor(line: dict[str, Any], *, main_due_on: date | None) -> str:
    """Fristanker aus dem Katalog - aber nur, wenn er rechnen kann.

    Der Anker `term_end` rechnet ab der Hauptfaelligkeit; ohne sie ist die
    Police wegen `insurance_policies_term_end_anchor` nicht einmal speicherbar.
    `fixed_date` ohne Monat und Tag ebenso. Wo der Katalog nichts Rechenbares
    hergibt, bleibt der Anker 'none' - und die Oberflaeche sagt "Angabe fehlt"
    statt ein Datum zu zeigen.
    """
    candidate = str(line.get("default_notice_anchor") or "none")
    if candidate in _ANCHORS_WITHOUT_MAIN_DUE:
        return candidate
    if candidate == "fixed_date":
        if line.get("default_fixed_month") is None or line.get("default_fixed_day") is None:
            return "none"
        return candidate
    return candidate if main_due_on is not None else "none"


def _fixed_date_pair(
    line: dict[str, Any],
    *,
    anchor: str,
    current_month: int | None = None,
    current_day: int | None = None,
) -> tuple[int | None, int | None]:
    """Monat und Tag zum Anker `fixed_date` - sonst nichts.

    Der Anker allein ist nicht speicherbar: `insurance_policies_fixed_date_pair`
    verlangt beide Zahlen. Sie kommen deshalb mit dem Anker aus derselben
    Katalogzeile, aus der er stammt (`moped`: 28.02. nach Paragraph 26 FZV).
    Eine bereits an der Police erfasste Angabe gewinnt - der Katalog ist die
    Vorbelegung, nicht die Wahrheit ueber diese eine Police.
    """
    if anchor != "fixed_date":
        return current_month, current_day
    if current_month is not None and current_day is not None:
        return current_month, current_day
    return line.get("default_fixed_month"), line.get("default_fixed_day")


def _catalog_main_due(line: dict[str, Any], *, today: date) -> date | None:
    """Vorbelegung 'MM-DD' aus dem Katalog auf ein Datum im laufenden Jahr.

    Die Hauptfaelligkeit ist ein Jahrestag; das Jahr traegt keine Aussage - der
    Rechenkern sucht sich die naechste Wiederkehr ohnehin selbst.
    """
    raw = line.get("default_main_due_md")
    if not isinstance(raw, str) or len(raw) != 5:
        return None
    try:
        return date(today.year, int(raw[:2]), int(raw[3:]))
    except ValueError:  # pragma: no cover - der CHECK im Katalog schliesst das aus
        return None


async def _require_linkable_contract(
    session: AsyncSession,
    household_id: UUID,
    contract_id: UUID,
    *,
    policy_id: UUID | None = None,
) -> Row[Any]:
    row = (
        await session.execute(
            text(
                "select id, name, status, amount_minor, frequency from contracts "
                "where id = :id and household_id = :household_id and merged_into_id is null"
            ),
            {"id": str(contract_id), "household_id": str(household_id)},
        )
    ).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=INSURANCE_CONTRACT_NOT_FOUND
        )
    taken = (
        await session.execute(
            text(
                "select id from insurance_policies "
                "where household_id = :household_id and contract_id = cast(:contract_id as uuid) "
                "and (cast(:policy_id as uuid) is null or id <> cast(:policy_id as uuid)) "
                "and status <> 'entered_in_error' limit 1"
            ),
            {
                "household_id": str(household_id),
                "contract_id": str(contract_id),
                "policy_id": str(policy_id) if policy_id else None,
            },
        )
    ).first()
    if taken is not None:
        # In P1 gibt es keine Buendel: ein Vertrag traegt genau eine Police.
        # Ab P4 loest `premium_share_bp` diesen Fall auf.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=INSURANCE_CONTRACT_ALREADY_LINKED
        )
    return row


def _translate_integrity_error(exc: IntegrityError) -> HTTPException:
    message = str(getattr(exc, "orig", exc))
    if "INSURANCE_PREMIUM_SHARE_EXCEEDED" in message:
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=INSURANCE_PREMIUM_SHARE_EXCEEDED
        )
    if "insurance_policies_number_uniq" in message:
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=INSURANCE_POLICY_NUMBER_DUPLICATE
        )
    if "insurance_policies_term_end_anchor" in message:
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=INSURANCE_MISSING_MAIN_DUE_ON
        )
    if "insurance_policies_paid_up_pair" in message:
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=INSURANCE_PAID_UP_WITH_EXTERNAL_PREMIUM,
        )
    if "insurance_policies_premium_source_excl" in message:
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=INSURANCE_PREMIUM_SOURCE_CONFLICT,
        )
    if "insurance_policies_external_triple" in message:
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=INSURANCE_PREMIUM_PAYER_INCOMPLETE,
        )
    if "insurance_policies_main_due_source_pair" in message:
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=INSURANCE_MAIN_DUE_NEEDS_CONFIRMATION,
        )
    if "insurance_policies_cancel_pair" in message:
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=INSURANCE_CANCELLED_NEEDS_DATE,
        )
    if "insurance_policies_fixed_date_pair" in message:
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=INSURANCE_FIXED_DATE_UNKNOWN,
        )
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="insurance_policy_rejected"
    )


# ---------------------------------------------------------------------------
# Schreiben: Police
# ---------------------------------------------------------------------------

_INSERT_COLUMNS: Final = (
    "household_id",
    "line_key",
    "insurer_name",
    "label",
    "policy_number",
    "customer_number",
    "tariff_name",
    "purpose_note",
    "status",
    "starts_on",
    "ends_on",
    "main_due_on",
    "notice_anchor",
    "notice_fixed_month",
    "notice_fixed_day",
    "notice_period_months",
    "min_term_months",
    "renewal_term_months",
    "waiting_period_days",
    "sum_insured_minor",
    "deductible_minor",
    "broker_name",
    "broker_phone",
    "claims_phone",
    "service_channel",
    "recipient_state",
    "dynamic_agreed",
    "attrs",
    "note",
    "contract_id",
    "external_premium_minor",
    "external_frequency",
    "premium_payer",
    "premium_payer_note",
    "data_source",
    "line_confirmed_at",
)


async def create_policy(
    session: AsyncSession,
    household_id: UUID,
    payload: InsurancePolicyCreate,
    *,
    today: date,
) -> PolicyWithEtag:
    line_row = await load_line(session, payload.line_key)
    if line_row is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=INSURANCE_LINE_UNKNOWN
        )
    line = dict(line_row._mapping)
    await _assert_attrs(session, payload.line_key, payload.attrs)

    if payload.contract_id is not None:
        await _require_linkable_contract(session, household_id, payload.contract_id)

    # Der Anker kommt aus dem Katalog nur zusammen mit einem Datum; ein vom
    # Client gesetzter Anker gewinnt, muss dann aber rechnen koennen.
    anchor = payload.notice_anchor or _effective_anchor(line, main_due_on=payload.main_due_on)
    if anchor == "term_end" and payload.main_due_on is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=INSURANCE_MISSING_MAIN_DUE_ON
        )
    fixed_month, fixed_day = _fixed_date_pair(line, anchor=anchor)
    if anchor == "fixed_date" and (fixed_month is None or fixed_day is None):
        # Ein Client, der den Anker selbst setzt, kann ihn nicht aus einer
        # Sparte begruenden, die keinen Kalenderfixpunkt kennt.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=INSURANCE_FIXED_DATE_UNKNOWN,
        )

    values: dict[str, Any] = {
        "household_id": str(household_id),
        "line_key": payload.line_key,
        "insurer_name": payload.insurer_name.strip(),
        "label": payload.label,
        "policy_number": payload.policy_number,
        "customer_number": payload.customer_number,
        "tariff_name": payload.tariff_name,
        "purpose_note": payload.purpose_note,
        "status": payload.status,
        "starts_on": payload.starts_on,
        "ends_on": payload.ends_on,
        "main_due_on": payload.main_due_on,
        "notice_anchor": anchor,
        "notice_fixed_month": fixed_month,
        "notice_fixed_day": fixed_day,
        # Die Fristlaenge ist die einzige Katalogvorgabe, die ohne Rueckfrage
        # uebernommen wird: sie ist rechtlich hergeleitet und traegt ueber
        # notice_terms_source = 'catalog_suggested' ihre Herkunft mit.
        "notice_period_months": (
            payload.notice_period_months
            if payload.notice_period_months is not None
            else line.get("default_notice_period_months")
        ),
        "min_term_months": payload.min_term_months,
        "renewal_term_months": payload.renewal_term_months,
        "waiting_period_days": payload.waiting_period_days,
        "sum_insured_minor": payload.sum_insured_minor,
        "deductible_minor": payload.deductible_minor,
        "broker_name": payload.broker_name,
        "broker_phone": payload.broker_phone,
        "claims_phone": payload.claims_phone,
        "service_channel": payload.service_channel,
        "recipient_state": payload.recipient_state,
        "dynamic_agreed": payload.dynamic_agreed,
        "attrs": json.dumps(payload.attrs or {}),
        "note": payload.note,
        "contract_id": str(payload.contract_id) if payload.contract_id else None,
        "external_premium_minor": (
            payload.external.external_premium_minor if payload.external else None
        ),
        "external_frequency": payload.external.external_frequency if payload.external else None,
        "premium_payer": payload.external.premium_payer if payload.external else None,
        "premium_payer_note": payload.external.premium_payer_note if payload.external else None,
        "data_source": "manual",
    }

    placeholders = []
    for column in _INSERT_COLUMNS:
        if column == "line_confirmed_at":
            # Wer die Sparte im Anlegedialog selbst waehlt, hat sie damit
            # bestaetigt - die Rueckfrage waere eine Wiederholung.
            placeholders.append("now()" if payload.line_key != "unknown" else "null")
        elif column == "contract_id":
            placeholders.append("cast(:contract_id as uuid)")
        else:
            placeholders.append(_placeholder(column))

    try:
        result = await session.execute(
            text(
                "insert into insurance_policies ("
                + ", ".join(_INSERT_COLUMNS)
                + ") values ("
                + ", ".join(placeholders)
                + ") returning id"
            ),
            values,
        )
    except IntegrityError as exc:
        raise _translate_integrity_error(exc) from exc

    row = result.first()
    if row is None:  # pragma: no cover - "returning id" liefert immer eine Zeile
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="insurance_policy_rejected"
        )
    return await _reload(session, household_id, row.id, today=today)


async def update_policy(
    session: AsyncSession,
    household_id: UUID,
    policy_id: UUID,
    payload: InsurancePolicyUpdate,
    *,
    today: date,
    expected: tuple[int, int] | None,
) -> PolicyWithEtag:
    before_row = await _require_policy_row(session, household_id, policy_id)
    before = dict(before_row._mapping)
    _assert_policy_version(before, expected)

    changes: dict[str, Any] = payload.model_dump(exclude_unset=True)
    changes.pop("clear_fields", None)
    # Ein ausdruecklicher Loeschwunsch ist die spezifischere Angabe und gewinnt
    # gegen einen gleichzeitig gesetzten Wert (Muster aus routes/contracts.py).
    for cleared in payload.clear_fields:
        changes[cleared] = None

    extra: list[str] = []
    extra_params: dict[str, Any] = {}
    if "line_key" in changes and changes["line_key"] != before["line_key"]:
        line_row = await load_line(session, str(changes["line_key"]))
        if line_row is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=INSURANCE_LINE_UNKNOWN
            )
        # Fristtyp und Ankergrundlage haengen an der Sparte. Der Trigger fuellt
        # sie nur, solange sie leer sind - nach einem Spartenwechsel stuende
        # sonst die Vorgabe der alten Sparte an der Police, und der Paragraph
        # 193 BGB schoebe eine Frist, die er nicht schieben darf.
        extra.append(
            "deadline_kind = (select default_deadline_kind from insurance_lines "
            "where line_key = :line_key)"
        )
        extra.append(
            "anchor_basis_kind = (select default_anchor_basis_kind from insurance_lines "
            "where line_key = :line_key)"
        )
        extra.append(
            "line_confirmed_at = case when :line_key = 'unknown' then null else now() end"
        )
        extra.append("line_deferred_at = null")

    if "attrs" in changes and changes["attrs"] is not None:
        await _assert_attrs(
            session, str(changes.get("line_key", before["line_key"])), dict(changes["attrs"])
        )

    resulting_main_due = changes.get("main_due_on", before.get("main_due_on"))
    if (
        resulting_main_due is not None
        and before.get("main_due_on") is None
        and "main_due_on" in changes
    ):
        # Siehe INSURANCE_MAIN_DUE_NEEDS_CONFIRMATION: die erste
        # Hauptfaelligkeit entsteht ausschliesslich ueber die Bestaetigung.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=INSURANCE_MAIN_DUE_NEEDS_CONFIRMATION,
        )

    resulting_anchor = changes.get("notice_anchor", before["notice_anchor"])
    if resulting_anchor == "term_end" and resulting_main_due is None:
        if "notice_anchor" in changes:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=INSURANCE_MISSING_MAIN_DUE_ON,
            )
        # Das Datum wurde geloescht: der Anker faellt mit ihm zurueck. Er ist
        # ohne Hauptfaelligkeit nicht speicherbar, und eine Fehlermeldung waere
        # hier eine Sackgasse statt einer Antwort.
        changes["notice_anchor"] = "none"

    if resulting_anchor == "fixed_date" and before.get("notice_fixed_month") is None:
        # Derselbe Fall wie beim Anlegen: der Anker bringt seinen Stichtag aus
        # der Sparte mit, in die er gehoert - nach einem Spartenwechsel aus der
        # NEUEN.
        target_line = await load_line(session, str(changes.get("line_key", before["line_key"])))
        fixed_month, fixed_day = _fixed_date_pair(
            dict(target_line._mapping) if target_line is not None else {},
            anchor="fixed_date",
        )
        if fixed_month is None or fixed_day is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=INSURANCE_FIXED_DATE_UNKNOWN,
            )
        extra.append("notice_fixed_month = :fixed_month")
        extra.append("notice_fixed_day = :fixed_day")
        extra_params["fixed_month"] = fixed_month
        extra_params["fixed_day"] = fixed_day

    assignments: list[str] = []
    params: dict[str, Any] = {"id": str(policy_id), "household_id": str(household_id)}
    params.update(extra_params)
    for column in _POLICY_UPDATABLE_COLUMNS:
        if column not in changes:
            continue
        assignments.append(f"{column} = {_placeholder(column)}")
        params[column] = _param_value(column, changes[column])
    assignments.extend(extra)

    if not assignments:
        # Ein leerer Patch bumpt nicht - das ETag des Clients bleibt gueltig.
        return await _reload(session, household_id, policy_id, today=today)

    try:
        updated = await session.execute(
            text(
                "update insurance_policies set "
                + ", ".join(assignments)
                + " where id = :id and household_id = :household_id"
            ),
            params,
        )
    except IntegrityError as exc:
        raise _translate_integrity_error(exc) from exc
    if cast(CursorResult[Any], updated).rowcount == 0:  # pragma: no cover - RLS-Restrisiko
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=INSURANCE_POLICY_NOT_FOUND
        )
    return await _reload(session, household_id, policy_id, today=today)


async def delete_policy(session: AsyncSession, household_id: UUID, policy_id: UUID) -> None:
    """Loescht nur die Police.

    Der Vertrag bleibt unberuehrt - Beitrag, Fixkosten und Cashflow aendern sich
    durch das Loeschen einer Police nicht.
    """
    result = await session.execute(
        text(
            "delete from insurance_policies where id = :id and household_id = :household_id "
            "returning id"
        ),
        {"id": str(policy_id), "household_id": str(household_id)},
    )
    if result.first() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=INSURANCE_POLICY_NOT_FOUND
        )


# ---------------------------------------------------------------------------
# Einfeldaktionen
# ---------------------------------------------------------------------------

# Vorschlag fuer den Vertragsstatus je Policenstatus (Plan 4.16). Bewusst nur
# ein Vorschlag: er aendert die Fixkosten und darf nicht still passieren.
_CONTRACT_STATUS_SUGGESTION: Final[dict[str, ContractStatus]] = {
    "cancelled": "cancelled",
    "ended": "ended",
    "paid_up": "paused",
    "dormant": "paused",
}


async def change_status(
    session: AsyncSession,
    household_id: UUID,
    policy_id: UUID,
    payload: PolicyStatusChangeRequest,
    *,
    today: date,
    expected: tuple[int, int] | None,
) -> tuple[PolicyStatusOutcome, str]:
    row = await _require_policy_row(session, household_id, policy_id)
    data = dict(row._mapping)
    _assert_policy_version(data, expected)

    suggestion = _CONTRACT_STATUS_SUGGESTION.get(payload.status)
    contract_id = data.get("contract_id")
    contract_status = data.get("contract_status")
    outcome = PolicyStatusOutcome(
        applied=False,
        policy_status_from=data["status"],
        policy_status_to=payload.status,
        contract_id=contract_id,
        contract_name=data.get("contract_name"),
        contract_status_from=contract_status,
        contract_status_to=suggestion,
    )

    if (
        suggestion is not None
        and contract_id is not None
        and contract_status is not None
        and payload.contract_status is None
    ):
        # Vorschau statt Schreiben: Police gekuendigt und Vertrag aktiv sind
        # zwei Wahrheiten, und der Beitrag zaehlte weiter in die Fixkostenquote.
        outcome.hint = (
            "Der verknuepfte Vertrag laeuft weiter und zaehlt in die Fixkosten. "
            "Mit contract_status wird beides in einer Transaktion geschrieben."
        )
        return outcome, policy_etag(int(data["contract_version"]), int(data["version"]))

    if payload.status == "paid_up" and data.get("external_premium_minor") is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=INSURANCE_PAID_UP_WITH_EXTERNAL_PREMIUM,
        )

    cancelled_on = payload.cancelled_on
    if cancelled_on is None and payload.status == "cancelled" and data.get("cancelled_on") is None:
        # CHECK insurance_policies_cancel_pair verlangt ein Datum. Dieselbe
        # Haltung wie beim Vertrag, der beim Beenden `ends_on` auf heute setzt.
        cancelled_on = today

    try:
        await session.execute(
            text(
                "update insurance_policies set status = :status, "
                "cancelled_on = coalesce(:cancelled_on, cancelled_on), "
                "cancellation_reason = coalesce(:reason, cancellation_reason), "
                "dormant_until = coalesce(:dormant_until, dormant_until), "
                "paid_up_benefit_minor = coalesce(:paid_up_benefit_minor, paid_up_benefit_minor) "
                "where id = :id and household_id = :household_id"
            ),
            {
                "id": str(policy_id),
                "household_id": str(household_id),
                "status": payload.status,
                "cancelled_on": cancelled_on,
                "reason": payload.cancellation_reason,
                "dormant_until": payload.dormant_until,
                "paid_up_benefit_minor": payload.paid_up_benefit_minor,
            },
        )
    except IntegrityError as exc:
        raise _translate_integrity_error(exc) from exc

    if payload.contract_status is not None and contract_id is not None:
        if contract_status is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=INSURANCE_CONTRACT_NOT_FOUND
            )
        if payload.contract_status != contract_status:
            # Keine zweite Uebergangstabelle: der Vertrag prueft seine
            # Statuswechsel selbst.
            contract_service.assert_status_transition(
                str(contract_status), payload.contract_status
            )
            await session.execute(
                text(
                    "update contracts set status = :status, "
                    "ends_on = case when :status = 'ended' then coalesce(ends_on, :today) "
                    "  else ends_on end, "
                    "cancelled_on = case when :status = 'cancelled' "
                    "  then coalesce(cancelled_on, :today) else cancelled_on end "
                    "where id = :id and household_id = :household_id"
                ),
                {
                    "id": str(contract_id),
                    "household_id": str(household_id),
                    "status": payload.contract_status,
                    "today": today,
                },
            )
            # Nur die beiden Uebergaenge, fuer die contract_events eine Art
            # kennt. Fuer 'paused' eine der vorhandenen Arten zu verwenden
            # ("reactivated") waere im Verlauf schlicht falsch abgelesen.
            if payload.contract_status in ("cancelled", "ended"):
                await contract_service.record_event(
                    session,
                    household_id,
                    contract_id,
                    payload.contract_status,
                    payload={
                        "status_from": contract_status,
                        "status_to": payload.contract_status,
                        "reason": "insurance_policy_status",
                    },
                )
        outcome.contract_status_to = payload.contract_status

    reloaded = await _reload(session, household_id, policy_id, today=today)
    outcome.applied = True
    outcome.policy = reloaded.policy
    return outcome, reloaded.etag


async def confirm_line(
    session: AsyncSession,
    household_id: UUID,
    policy_id: UUID,
    payload: ConfirmLine,
    *,
    today: date,
) -> PolicyWithEtag:
    """Bestaetigt die Sparte - oder stellt sie ausdruecklich zurueck.

    `defer` ist der Fall "Sparte nicht in der Liste": die Police verschwindet aus
    der Aufgabenliste, ohne falsch zugeordnet zu werden. Eine Daueraufgabe, die
    niemand erledigen kann, waere schlimmer als eine offene Sparte.
    """
    row = await _require_policy_row(session, household_id, policy_id)
    data = dict(row._mapping)

    if payload.defer:
        await session.execute(
            text(
                "update insurance_policies set line_deferred_at = now(), line_confirmed_at = null "
                "where id = :id and household_id = :household_id"
            ),
            {"id": str(policy_id), "household_id": str(household_id)},
        )
        return await _reload(session, household_id, policy_id, today=today)

    line_key = payload.line_key or str(data["line_key"])
    if line_key == "unknown":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=INSURANCE_LINE_STILL_UNKNOWN
        )
    line_row = await load_line(session, line_key)
    if line_row is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=INSURANCE_LINE_UNKNOWN
        )
    line = dict(line_row._mapping)

    # Die Fristvorgaben der neuen Sparte greifen nur, wo die Police noch nichts
    # Eigenes traegt: eine bereits erfasste Fristlaenge darf ein Spartenwechsel
    # nicht ueberschreiben, sonst zeigte notice_terms_source auf eine Herkunft,
    # die nicht mehr stimmt.
    anchor = data["notice_anchor"]
    if anchor == "none":
        anchor = _effective_anchor(line, main_due_on=data.get("main_due_on"))
    # Der Stichtag gehoert zum Anker, nicht zur Sparte: uebernimmt die Police
    # hier `fixed_date` aus dem Katalog, muss sie Monat und Tag mitnehmen -
    # sonst scheitert der Spartenwechsel am CHECK, und genau dieser Weg ist der
    # jeder aus einem Vertrag uebernommenen Police.
    fixed_month, fixed_day = _fixed_date_pair(
        line,
        anchor=str(anchor),
        current_month=data.get("notice_fixed_month"),
        current_day=data.get("notice_fixed_day"),
    )

    try:
        await session.execute(
            text(
                "update insurance_policies set line_key = :line_key, "
                "line_confirmed_at = now(), line_deferred_at = null, "
                "deadline_kind = (select default_deadline_kind from insurance_lines "
                "  where line_key = :line_key), "
                "anchor_basis_kind = (select default_anchor_basis_kind from insurance_lines "
                "  where line_key = :line_key), "
                "notice_anchor = :anchor, "
                "notice_fixed_month = :fixed_month, "
                "notice_fixed_day = :fixed_day, "
                "notice_period_months = coalesce(notice_period_months, :months) "
                "where id = :id and household_id = :household_id"
            ),
            {
                "id": str(policy_id),
                "household_id": str(household_id),
                "line_key": line_key,
                "anchor": anchor,
                "fixed_month": fixed_month,
                "fixed_day": fixed_day,
                "months": line.get("default_notice_period_months"),
            },
        )
    except IntegrityError as exc:
        # Der einzige Schreibweg, der bisher ohne Uebersetzung dastand - ein
        # verletzter CHECK kam hier als 500 heraus statt als Auskunft.
        raise _translate_integrity_error(exc) from exc
    return await _reload(session, household_id, policy_id, today=today)


async def confirm_main_due(
    session: AsyncSession,
    household_id: UUID,
    policy_id: UUID,
    payload: ConfirmMainDue | None,
    *,
    today: date,
) -> PolicyWithEtag:
    """Der EINZIGE Schreibweg zu `main_due_source = 'user_confirmed'`.

    Ohne Body bestaetigt die Aktion das vorhandene oder das im Katalog
    vorgeschlagene Datum unveraendert - der Ein-Klick-Fall "30.11. stimmt so".
    Der Katalogwert wird bewusst erst hier zu einem Datum: beim Anlegen waere er
    eine Behauptung, hier ist er eine Bestaetigung.
    """
    row = await _require_policy_row(session, household_id, policy_id)
    data = dict(row._mapping)

    if payload is not None:
        main_due_on: date | None = payload.main_due_on
    else:
        main_due_on = data.get("main_due_on") or _catalog_main_due(data, today=today)
    if main_due_on is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=INSURANCE_MISSING_MAIN_DUE_ON
        )

    anchor = (payload.notice_anchor if payload is not None else None) or data["notice_anchor"]
    if anchor == "none":
        anchor = _effective_anchor(data, main_due_on=main_due_on)

    # Die Sitzungsvariable MUSS vor dem UPDATE stehen und in derselben
    # Transaktion: der BEFORE-Trigger liest sie. Ohne sie bliebe die Herkunft auf
    # 'catalog_suggested' - und genau deshalb kann kein gewoehnliches UPDATE die
    # Herkunft hochstufen.
    await session.execute(text("set local insurance.main_due_confirm = 'user'"))
    try:
        await session.execute(
            text(
                "update insurance_policies set main_due_on = :main_due_on, "
                "notice_anchor = :anchor, "
                "notice_period_months = coalesce(notice_period_months, :months) "
                "where id = :id and household_id = :household_id"
            ),
            {
                "id": str(policy_id),
                "household_id": str(household_id),
                "main_due_on": main_due_on,
                "anchor": anchor,
                "months": data.get("default_notice_period_months"),
            },
        )
    except IntegrityError as exc:
        raise _translate_integrity_error(exc) from exc
    finally:
        # Zuruecksetzen, damit kein spaeteres UPDATE derselben Transaktion
        # versehentlich als Bestaetigung gilt.
        await session.execute(text("set local insurance.main_due_confirm = ''"))

    return await _reload(session, household_id, policy_id, today=today)


async def confirm_notice_terms(
    session: AsyncSession,
    household_id: UUID,
    policy_id: UUID,
    payload: ConfirmNoticeTerms,
    *,
    today: date,
) -> PolicyWithEtag:
    """Bestaetigt die Fristlaenge - die zweite Unsicherheitsquelle derselben Frist.

    Ohne diesen Schritt bleibt eine uebernommene Police dauerhaft auf
    `certainty = 'assumed'`, auch wenn die Hauptfaelligkeit bestaetigt ist: die
    Monatszahl ist dann aus Tagen umgerechnet ('derived_from_days'), und der
    Bezugspunkt der Umrechnung ist ungeprueft.
    """
    row = await _require_policy_row(session, household_id, policy_id)
    data = dict(row._mapping)

    anchor = payload.notice_anchor or data["notice_anchor"]
    if anchor == "none":
        anchor = _effective_anchor(data, main_due_on=data.get("main_due_on"))
    if anchor == "term_end" and data.get("main_due_on") is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=INSURANCE_MISSING_MAIN_DUE_ON
        )

    await session.execute(text("set local insurance.notice_terms_confirm = 'user'"))
    try:
        await session.execute(
            text(
                "update insurance_policies set notice_period_months = :months, "
                "notice_anchor = :anchor "
                "where id = :id and household_id = :household_id"
            ),
            {
                "id": str(policy_id),
                "household_id": str(household_id),
                "months": payload.notice_period_months,
                "anchor": anchor,
            },
        )
    except IntegrityError as exc:
        raise _translate_integrity_error(exc) from exc
    finally:
        await session.execute(text("set local insurance.notice_terms_confirm = ''"))

    return await _reload(session, household_id, policy_id, today=today)


async def link_contract(
    session: AsyncSession,
    household_id: UUID,
    policy_id: UUID,
    payload: LinkContract,
    *,
    today: date,
) -> PolicyWithEtag:
    """Fall (b)/(c) -> Fall (a).

    Ein etwaiger Fremdbetrag faellt dabei weg: Vertrag und Fremdbetrag zusammen
    verbietet `insurance_policies_premium_source_excl`, und fachlich waere es
    doppelt gezaehltes Geld.
    """
    await _require_policy_row(session, household_id, policy_id)
    await _require_linkable_contract(
        session, household_id, payload.contract_id, policy_id=policy_id
    )
    try:
        await session.execute(
            text(
                "update insurance_policies set contract_id = cast(:contract_id as uuid), "
                "premium_share_bp = :share_bp, external_premium_minor = null, "
                "external_frequency = null, premium_payer = null, premium_payer_note = null "
                "where id = :id and household_id = :household_id"
            ),
            {
                "id": str(policy_id),
                "household_id": str(household_id),
                "contract_id": str(payload.contract_id),
                "share_bp": payload.premium_share_bp,
            },
        )
    except IntegrityError as exc:
        raise _translate_integrity_error(exc) from exc
    return await _reload(session, household_id, policy_id, today=today)


async def unlink_contract(
    session: AsyncSession, household_id: UUID, policy_id: UUID, *, today: date
) -> PolicyWithEtag:
    """Police faellt in Fall (c), "kein Beitrag hinterlegt". Der Vertrag bleibt."""
    await _require_policy_row(session, household_id, policy_id)
    await session.execute(
        text(
            "update insurance_policies set contract_id = null, premium_share_bp = 10000 "
            "where id = :id and household_id = :household_id"
        ),
        {"id": str(policy_id), "household_id": str(household_id)},
    )
    return await _reload(session, household_id, policy_id, today=today)


# ---------------------------------------------------------------------------
# Vertragskandidaten
# ---------------------------------------------------------------------------

_CANDIDATE_LIMIT: Final = 25


async def contract_candidates(
    session: AsyncSession, household_id: UUID, policy_id: UUID
) -> list[ContractCandidate]:
    """Vertraege ohne Police, gerankt - ein Vorschlag, keine Automatik.

    Der Empfaengerschluessel kommt aus `contract_matching_service.payee_key`:
    braechte dieser Dienst eine eigene Normalisierung mit, faende er einen
    Vertrag nicht wieder, den die Erkennung selbst vorgeschlagen hat.
    """
    row = await _require_policy_row(session, household_id, policy_id)
    data = dict(row._mapping)
    insurer_key = contract_matching_service.payee_key(str(data["insurer_name"]))
    insurer_words = {
        word for word in str(data["insurer_name"]).lower().split() if len(word) >= 4
    }
    template_key = data.get("insurer_template_key")

    rows = (
        await session.execute(
            text(
                "select c.id, c.name, c.provider_label, c.amount_minor, c.frequency, "
                "c.next_due_date, c.contract_type, c.provider_template_key, "
                "pa.name as payee_name "
                "from contracts c left join payees pa on pa.id = c.payee_id "
                "where c.household_id = :household_id and c.merged_into_id is null "
                "and c.status in ('active', 'paused', 'cancelled') "
                "and not exists (select 1 from insurance_policies p "
                "  where p.household_id = c.household_id and p.contract_id = c.id "
                "  and p.status <> 'entered_in_error') "
                "order by c.name"
            ),
            {"household_id": str(household_id)},
        )
    ).all()

    candidates: list[ContractCandidate] = []
    for contract in rows:
        values = dict(contract._mapping)
        score = 0.0
        evidence: list[str] = []
        if values["contract_type"] == "insurance":
            score += 0.5
            evidence.append("Als Versicherungsvertrag gefuehrt")
        provider = values.get("provider_label") or values.get("payee_name") or values["name"]
        if insurer_key and contract_matching_service.payee_key(str(provider)) == insurer_key:
            score += 0.3
            evidence.append("Versicherer stimmt ueberein")
        elif insurer_words and insurer_words & set(str(provider).lower().split()):
            score += 0.15
            evidence.append("Anbietername aehnelt dem Versicherer")
        if template_key is not None and values.get("provider_template_key") == template_key:
            score += 0.2
            evidence.append("Gleiche Anbietervorlage")
        haystack = f"{values['name']} {provider}".lower()
        if insurer_words and any(word in haystack for word in insurer_words):
            score += 0.05
        candidates.append(
            ContractCandidate(
                contract_id=values["id"],
                name=values["name"],
                provider_label=values.get("provider_label"),
                amount_minor=int(values["amount_minor"]),
                frequency=str(values["frequency"]),
                next_due_date=values["next_due_date"],
                score=round(min(score, 1.0), 2),
                evidence=evidence,
            )
        )

    candidates.sort(key=lambda item: (-item.score, item.name.lower()))
    return candidates[:_CANDIDATE_LIMIT]


# ---------------------------------------------------------------------------
# Bestandsuebernahme
# ---------------------------------------------------------------------------

# Ring 1 des Plans: Vertraege, die ausdruecklich als Versicherung gefuehrt sind.
# Ring 2 (Kategorie- und Anbieterheuristik) fehlt in P1 bewusst - er koennte die
# Sparte nur raten, und eine falsch geratene Sparte ist der teuerste unsichtbare
# Fehler dieses Moduls.
_BACKFILL_CANDIDATES: Final = """
  from contracts c
  left join payees pa on pa.id = c.payee_id
  where c.household_id = :household_id
    and c.contract_type = 'insurance'
    and c.merged_into_id is null
    and c.status <> 'not_a_contract'
    and not exists (
      select 1 from insurance_policies p
      where p.household_id = c.household_id and p.contract_id = c.id
    )
"""


async def backfill_preview(session: AsyncSession, household_id: UUID) -> BackfillPreview:
    rows = (
        await session.execute(
            text(
                "select c.id, c.name, c.provider_label, c.amount_minor, c.frequency, "
                "c.cancellation_period_days, pa.name as payee_name "
                + _BACKFILL_CANDIDATES
                + " order by c.name"
            ),
            {"household_id": str(household_id)},
        )
    ).all()
    already = (
        await session.execute(
            text(
                "select count(*) as anzahl from contracts c "
                "where c.household_id = :household_id and c.contract_type = 'insurance' "
                "and c.merged_into_id is null and exists ("
                "  select 1 from insurance_policies p "
                "  where p.household_id = c.household_id and p.contract_id = c.id)"
            ),
            {"household_id": str(household_id)},
        )
    ).scalar_one()

    placeholder = await load_line(session, "unknown")
    placeholder_label = (
        str(dict(placeholder._mapping)["label"]) if placeholder is not None else "Sparte noch offen"
    )

    items: list[BackfillPreviewItem] = []
    for row in rows:
        values = dict(row._mapping)
        reason = ["Vertrag ist als Versicherung gefuehrt."]
        if values["frequency"] == "yearly":
            reason.append("Hauptfaelligkeit wird als Annahme aus der Faelligkeit vorbelegt.")
        else:
            reason.append(
                "Ohne jaehrliche Zahlweise bleibt die Hauptfaelligkeit offen - "
                "der Einzugstermin ist nicht der Beginn des Versicherungsjahres."
            )
        if values["cancellation_period_days"] is not None:
            reason.append(
                f"Kuendigungsfrist aus {values['cancellation_period_days']} Tagen "
                "umgerechnet - Bezugspunkt bitte pruefen."
            )
        items.append(
            BackfillPreviewItem(
                contract_id=values["id"],
                contract_name=values["name"],
                provider_label=values.get("provider_label"),
                amount_minor=int(values["amount_minor"]),
                frequency=str(values["frequency"]),
                # Die Sparte bestaetigt der Nutzer. Der Katalog traegt in P1
                # keine Ableitungszeilen, und unterhalb der Konfidenzschwelle
                # bleibt es beim Platzhalter.
                suggested_line_key="unknown",
                suggested_line_label=placeholder_label,
                reason=" ".join(reason),
            )
        )
    return BackfillPreview(items=items, already_linked_count=int(already))


async def run_backfill(session: AsyncSession, household_id: UUID) -> BackfillResult:
    """Legt je Kandidatenvertrag eine Police an.

    Der Lauf bestaetigt nichts: er setzt die Sitzungsvariablen nie, also bleibt
    jedes uebernommene Datum eine Annahme. Und er fasst bestehende Policen nicht
    an - ein zweiter Lauf findet sie ueber `not exists` gar nicht erst, womit ein
    bereits bestaetigtes Datum nicht von der wandernden Vertragsfaelligkeit
    ueberschrieben werden kann.
    """
    before = await backfill_preview(session, household_id)
    rows = (
        await session.execute(
            text(
                "insert into insurance_policies ("
                "  household_id, contract_id, line_key, label, insurer_name, "
                "  insurer_template_key, status, starts_on, ends_on, cancelled_on, "
                "  min_term_months, renewal_term_months, main_due_on, notice_anchor, "
                "  notice_period_months, notice_terms_source, service_channel, "
                "  premium_share_bp, data_source) "
                "select c.household_id, c.id, 'unknown', "
                "  left(btrim(c.name), 120), "
                "  left(btrim(coalesce(nullif(btrim(c.provider_label), ''), "
                "    pa.name, c.name)), 160), "
                "  c.provider_template_key, "
                "  case c.status when 'active' then 'active' when 'paused' then 'suspended' "
                "    when 'cancelled' then 'cancelled' else 'ended' end, "
                "  c.starts_on, c.ends_on, c.cancelled_on, c.min_term_months, "
                "  c.renewal_term_months, "
                # Nur bei jaehrlicher Zahlweise faellt der Einzugstermin mit dem
                # Beginn des Versicherungsjahres zusammen. Bei monatlicher
                # Zahlweise laege der gerechnete Kuendigungstermin bis zu elf
                # Monate daneben - das waere eine erfundene Frist.
                "  case when c.frequency = 'yearly' then c.next_due_date end, "
                "  case when c.frequency = 'yearly' and c.cancellation_period_days is not null "
                "    then 'term_end' else 'none' end, "
                "  case when c.cancellation_period_days is not null "
                "    then greatest(1, cast(round(c.cancellation_period_days / 30.0) as int)) end, "
                # Die Umrechnung Tage -> Monate traegt ihre Herkunft als SPALTE,
                # nicht als Anzeigetext: sie blockiert certainty = 'confirmed',
                # bis der Nutzer den Bezugspunkt bestaetigt.
                "  case when c.cancellation_period_days is not null "
                "    then 'derived_from_days' else 'catalog_suggested' end, "
                "  'post', 10000, 'from_contract' "
                + _BACKFILL_CANDIDATES
                + " returning id"
            ),
            {"household_id": str(household_id)},
        )
    ).all()

    created = [row.id for row in rows]
    return BackfillResult(
        created=len(created),
        # Uebergangen wurden die Versicherungsvertraege, die bereits eine Police
        # tragen - genau sie fasst der Lauf ein zweites Mal nicht an.
        skipped=before.already_linked_count,
        policy_ids=created,
    )


# ---------------------------------------------------------------------------
# Einstellungen
# ---------------------------------------------------------------------------

_SETTINGS_COLUMNS: Final = (
    "notify_deadline, notify_premium_notice, notify_claim_followup, "
    "lead_days, lead_days_special, review_interval_months"
)


async def get_settings(session: AsyncSession, household_id: UUID) -> InsuranceSettings:
    """Benachrichtigungseinstellungen, notfalls die Voreinstellung.

    Eine fehlende Zeile ist kein Fehler: die Tabelle wird erst beim ersten
    Speichern angelegt, und bis dahin gelten genau die Werte, die auch der
    naechtliche Lauf per `coalesce` benutzt. Ein 404 zwaenge die Oberflaeche,
    diese Vorgaben ein zweites Mal zu kennen - und zwei Orte fuer dieselbe
    Zahl heisst frueher oder spaeter zwei verschiedene Staffeln.
    """
    row = (
        await session.execute(
            text(f"select {_SETTINGS_COLUMNS} from insurance_settings where household_id = :hid"),
            {"hid": str(household_id)},
        )
    ).mappings().first()
    if row is None:
        return InsuranceSettings()
    return InsuranceSettings.model_validate(dict(row))


async def save_settings(
    session: AsyncSession, household_id: UUID, payload: InsuranceSettings
) -> InsuranceSettings:
    """Schreibt die Einstellungen vollstaendig (Upsert).

    Kein If-Match: die Einstellungen sind ein einzelner Satz Werte je Haushalt
    ohne Versionsspalte, und ein verlorenes Rennen kostet hier eine
    Erinnerungsstufe, keinen Datensatz. Die Oberflaeche schickt den geladenen
    Stand vollstaendig zurueck, damit ein Feld, das sie nicht anbietet, nicht
    stillschweigend auf seine Voreinstellung zurueckfaellt.
    """
    row = (
        await session.execute(
            text(
                "insert into insurance_settings (household_id, notify_deadline, "
                "  notify_premium_notice, notify_claim_followup, lead_days, "
                "  lead_days_special, review_interval_months) "
                "values (:hid, :nd, :npn, :ncf, cast(:ld as int[]), "
                "  cast(:lds as int[]), :rim) "
                "on conflict (household_id) do update set "
                "  notify_deadline = excluded.notify_deadline, "
                "  notify_premium_notice = excluded.notify_premium_notice, "
                "  notify_claim_followup = excluded.notify_claim_followup, "
                "  lead_days = excluded.lead_days, "
                "  lead_days_special = excluded.lead_days_special, "
                "  review_interval_months = excluded.review_interval_months, "
                "  updated_at = now() "
                f"returning {_SETTINGS_COLUMNS}"
            ),
            {
                "hid": str(household_id),
                "nd": payload.notify_deadline,
                "npn": payload.notify_premium_notice,
                "ncf": payload.notify_claim_followup,
                # Absteigend und ohne Doppel gespeichert, damit `current_stage`
                # die engste erreichte Stufe findet, egal in welcher Reihenfolge
                # sie eingetippt wurde.
                "ld": sorted(set(payload.lead_days), reverse=True),
                "lds": sorted(set(payload.lead_days_special), reverse=True),
                "rim": payload.review_interval_months,
            },
        )
    ).mappings().one()
    return InsuranceSettings.model_validate(dict(row))


__all__ = [
    "InsuranceLineDetail",
    "InsurancePolicyDetail",
    "PolicyStatusChangeRequest",
    "PolicyStatusOutcome",
    "PolicyWithEtag",
    "backfill_preview",
    "build_summary",
    "build_tasks",
    "change_status",
    "confirm_line",
    "confirm_main_due",
    "confirm_notice_terms",
    "contract_candidates",
    "create_policy",
    "delete_policy",
    "get_line",
    "get_policy",
    "get_policy_detail",
    "get_purpose",
    "get_settings",
    "link_contract",
    "list_coverage_keys",
    "list_lines",
    "list_policies",
    "parse_policy_if_match",
    "policy_etag",
    "run_backfill",
    "save_settings",
    "unlink_contract",
    "update_policy",
]
