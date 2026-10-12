"""Schaden- und Leistungsfaelle (docs/VERSICHERUNGEN-PLAN.md, P5).

Vier Regeln erklaeren fast jede Entscheidung in dieser Datei:

1. **Gesundheit bleibt beim Erstattungsmodul.** Ein Vorgang auf einer Police mit
   `is_health_scoped = true` wird abgewiesen - mit Verweis auf
   `/v1/reimbursements/cases`. Der Dienst prueft es freundlich, der Trigger
   `insurance_claims_validate` erzwingt es auch gegen direktes SQL. Zwei
   Vorgangsmodelle fuer dieselbe Sache waeren die Zweitwahrheit, die dieser Plan
   ueberall vermeidet.
2. **Auch hier entsteht kein Geld.** `claimed_minor` und `approved_minor` sind
   der Inhalt einer Abrechnung. `settled_minor` dagegen wird ausschliesslich aus
   den zugeordneten Zahlungen gerechnet - es gibt keinen zweiten Schreiber und
   damit keine zweite Wahrheit ueber dieselbe Summe. Der Weg zur Buchung ist
   derselbe wie im Erstattungsmodul (`health_transaction_allocations` als
   Vorbild), kein zweiter Zuordnungsmechanismus.
3. **Der Fristanker ist nicht die Entscheidung.** Paragraph 92 Abs. 2 S. 1 VVG
   knuepft an den ABSCHLUSS DER VERHANDLUNGEN ueber die Entschaedigung. Bei
   einer Teilregulierung ist die Entscheidung des Versicherers der ANFANG der
   Verhandlung; bei voller Anerkennung enden sie erst mit der Zahlung. Wer hier
   `decided_on + 1 Monat` rechnete, meldete in zwei von drei Faellen ein
   bestehendes Recht als verfallen.
4. **Ohne Anker gibt es kein Datum, sondern eine Aussage.** Fehlt
   `negotiations_closed_on`, antwortet der Dienst mit `open_undetermined` und
   `needs_anchor = true`: das Recht besteht seit dem Schadentag, sein Ende steht
   noch nicht fest. Ein plausibel aussehendes, frei gerechnetes Datum waere die
   einzige Variante, die dem Nutzer schadet - und `expired` erreicht ein Vorgang
   deshalb nur ueber einen belegten oder bestaetigten Anker.
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from typing import Any, Final, Literal, cast
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.concurrency import VERSION_CONFLICT, entity_etag
from app.schemas.insurance import (
    ClaimAnchorCertainty,
    ClaimAnchorReason,
    ClaimAnchorSource,
    ClaimAnchorSuggestion,
    ClaimCloseNegotiations,
    ClaimEvent,
    ClaimPayment,
    ClaimPaymentCreate,
    ClaimPaymentDirection,
    ClaimStatusChange,
    ClaimTerminationBasis,
    ClaimTerminationEffect,
    ClaimTerminationWindow,
    ClaimTransactionCandidate,
    InsuranceClaim,
    InsuranceClaimCreate,
    InsuranceClaimDetail,
    InsuranceClaimUpdate,
    PayerPolicyLink,
    PayerPolicySuggestion,
    PolicySuggestion,
)
from app.services import insurance_service
from app.services.contract_matching_service import payee_key
from app.services.insurance_deadline_service import (
    _CHANNEL_BUFFER_BUSINESS_DAYS,
    add_months,
    minus_delivery_days,
    previous_delivery_day,
)

# ---------------------------------------------------------------------------
# Fehlercodes
# ---------------------------------------------------------------------------

INSURANCE_CLAIM_NOT_FOUND: Final = "insurance_claim_not_found"
# Die harte Abgrenzung aus Leitsatz 1. Der Code traegt einen `hint` auf den
# richtigen Endpunkt: eine Abweisung ohne Weg ist eine Sackgasse.
INSURANCE_CLAIM_HEALTH_SCOPED: Final = "insurance_claim_health_scoped"
INSURANCE_CLAIM_PAYMENT_NOT_FOUND: Final = "insurance_claim_payment_not_found"
INSURANCE_CLAIM_PAYMENT_OVERALLOCATED: Final = "insurance_claim_payment_overallocated"
INSURANCE_CLAIM_PAYMENT_DIRECTION: Final = "insurance_claim_payment_direction_mismatch"
INSURANCE_CLAIM_TRANSACTION_NOT_FOUND: Final = "insurance_claim_transaction_not_found"
INSURANCE_CLAIM_ANCHOR_BEFORE_EVENT: Final = "insurance_claim_anchor_before_event"
INSURANCE_CLAIM_NO_TERMINATION_RIGHT: Final = "insurance_claim_no_termination_right"
INSURANCE_PAYER_NOT_FOUND: Final = "reimbursement_payer_not_found"

_HEALTH_HINT: Final = (
    "Krankheitskosten laufen ueber /v1/reimbursements/cases - dort mit "
    "Traegersplit, Einreichung und Beihilfe. Ein zweiter Vorgang zu derselben "
    "Rechnung waere eine zweite Wahrheit."
)

# ---------------------------------------------------------------------------
# Konstanten
# ---------------------------------------------------------------------------

# Statuswerte, in denen ein Sonderkuendigungsfenster ueberhaupt laufen kann.
# 'draft' fehlt, weil ein nicht gemeldeter Schaden noch kein Vorgang beim
# Versicherer ist; 'withdrawn' fehlt, weil der Nutzer den Anspruch selbst
# zurueckgenommen hat.
_WINDOW_STATUSES: Final = (
    "reported",
    "in_review",
    "info_requested",
    "decided",
    "payment_expected",
    "paid",
    "closed",
)

# Fundstelle je Norm. Steht als Text in der Antwort, nicht als Bedingung im
# Code: die Oberflaeche entscheidet an `termination_basis`, nicht am Satz.
_LAW_BY_BASIS: Final[dict[str, str]] = {
    "vvg_92": "Paragraph 92 Abs. 2 S. 1 VVG",
    "vvg_111": "Paragraph 111 Abs. 2 S. 1 VVG",
    "contract": "Bedingungen der Police",
    "none": "",
}

# Zahlungsarten, die als Entschaedigung zaehlen. 'deductible', 'invoice' und
# 'recovery' fehlen: sie bewegen Geld in die andere Richtung oder sind kein
# Teil der Leistung.
_SETTLEMENT_KINDS: Final = ("settlement", "advance")

_CLAIM_UPDATABLE_COLUMNS: Final = (
    "policy_id",
    "insured_object_id",
    "title",
    "claim_number",
    "occurred_on",
    "reported_on",
    "location",
    "description",
    "police_reference",
    "decision",
    "decided_on",
    "reduction_reason",
    "claimed_minor",
    "approved_minor",
    "deductible_minor",
    "paid_to_third_party",
    "next_action",
    "next_action_due",
    "termination_basis",
    "termination_effect_options",
)

_CLAIM_INSERT_COLUMNS: Final = (
    "household_id",
    "policy_id",
    "insured_object_id",
    "title",
    "claim_number",
    "occurred_on",
    "reported_on",
    "location",
    "description",
    "police_reference",
    "status",
    "claimed_minor",
    "approved_minor",
    "deductible_minor",
    "next_action",
    "next_action_due",
    "termination_basis",
    "termination_effect_options",
    "special_termination_state",
    "special_termination_until",
)

_UUID_COLUMNS: Final = frozenset(
    {"policy_id", "insured_object_id", "household_id", "negotiations_closed_transaction_id"}
)
_ARRAY_COLUMNS: Final = frozenset({"termination_effect_options"})

_CLAIM_COLUMNS: Final = """
  c.id, c.policy_id, c.insured_object_id, c.title, c.claim_number, c.occurred_on,
  c.reported_on, c.location, c.description, c.police_reference, c.status,
  c.decision, c.decided_on, c.reduction_reason, c.claimed_minor, c.approved_minor,
  c.deductible_minor, c.settled_minor, c.paid_to_third_party, c.next_action,
  c.next_action_due, c.termination_basis, c.negotiations_closed_on,
  c.negotiations_closed_reason, c.negotiations_closed_source,
  c.negotiations_closed_transaction_id, c.negotiations_closed_certainty,
  c.special_termination_state, c.special_termination_until,
  c.termination_effect_options, c.version, c.created_at, c.updated_at
"""

# Der Policenkontext reist mit: ohne Zustellweg, Empfaengerland und
# Hauptfaelligkeit ist das Fenster nicht zu rechnen, und ein zweiter Rundlauf je
# Zeile waere N+1. Die beiden Unterabfragen zur letzten Eingangszahlung stehen
# aus demselben Grund hier - der Ankervorschlag braucht sie in jeder Zeile.
_CLAIM_CONTEXT_COLUMNS: Final = """
  p.insurer_name, p.label as policy_label, p.line_key, p.policy_number_norm,
  p.service_channel, p.recipient_state, p.main_due_on, p.main_due_source,
  p.is_health_scoped, l.label as line_label, o.label as object_label,
  (select count(*) from insurance_claim_payments cp
     where cp.claim_id = c.id and cp.household_id = c.household_id) as payment_count,
  (select max(cp.booked_on) from insurance_claim_payments cp
     where cp.claim_id = c.id and cp.household_id = c.household_id
       and cp.direction = 'inbound') as last_inbound_on,
  (select cp.transaction_id from insurance_claim_payments cp
     where cp.claim_id = c.id and cp.household_id = c.household_id
       and cp.direction = 'inbound'
     order by cp.booked_on desc, cp.created_at desc limit 1) as last_inbound_transaction_id
"""

_CLAIM_FROM: Final = """
  from insurance_claims c
  left join insurance_policies p
    on p.id = c.policy_id and p.household_id = c.household_id
  left join insurance_lines l on l.line_key = p.line_key
  left join insured_objects o
    on o.id = c.insured_object_id and o.household_id = c.household_id
"""

_PAYMENT_COLUMNS: Final = """
  cp.id, cp.claim_id, cp.transaction_id, cp.direction, cp.kind, cp.amount_minor,
  cp.booked_on, cp.note, cp.created_at, t.booked_date as transaction_booked_date,
  t.amount_minor as transaction_amount_minor,
  t.payee_label as transaction_payee_label
"""

_PAYMENT_FROM: Final = """
  from insurance_claim_payments cp
  left join transactions t
    on t.id = cp.transaction_id and t.household_id = cp.household_id
"""


# ---------------------------------------------------------------------------
# Kleinkram
# ---------------------------------------------------------------------------


def _placeholder(column: str) -> str:
    if column in _ARRAY_COLUMNS:
        return f"cast(:{column} as text[])"
    if column in _UUID_COLUMNS:
        return f"cast(:{column} as uuid)"
    return f":{column}"


def _param_value(column: str, value: Any) -> Any:
    if column in _UUID_COLUMNS and value is not None:
        return str(value)
    return value


def _digits(value: str | None) -> str:
    return re.sub(r"\D+", "", value or "")


def _rowcount(result: object) -> int:
    return cast(CursorResult[Any], result).rowcount


def claim_etag(version: int) -> str:
    """ETag aus der Version des Vorgangs.

    Bewusst NICHT das zusammengesetzte ETag der Police (Plan 4.11): ein Vorgang
    haengt an keinem Vertrag, und eine parallele Betragsaenderung am Beitrag
    aendert nichts an dem, was in der Schadenakte steht.
    """
    return entity_etag(version)


def _assert_version(data: dict[str, Any], expected: int | None) -> None:
    current = int(data["version"])
    if expected is not None and expected != current:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=VERSION_CONFLICT,
            headers={"ETag": claim_etag(current)},
        )


def _health_scoped_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={
            "code": INSURANCE_CLAIM_HEALTH_SCOPED,
            "hint": _HEALTH_HINT,
            "endpoint": "/v1/reimbursements/cases",
        },
    )


def _translate_integrity_error(exc: IntegrityError) -> HTTPException:
    """Uebersetzt die Triggermeldungen in die Fehlercodes der API.

    Der Dienst prueft die Gesundheitsgrenze vorher selbst und antwortet
    freundlicher; hier landet nur, wer sie im Rennen zwischen zwei Anfragen
    passiert - oder wer die Police in derselben Sekunde umgehaengt hat.
    """
    message = str(getattr(exc, "orig", exc))
    if "INSURANCE_CLAIM_HEALTH_SCOPED" in message:
        return _health_scoped_error()
    if "INSURANCE_CLAIM_PAYMENT_OVERALLOCATED" in message:
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": INSURANCE_CLAIM_PAYMENT_OVERALLOCATED,
                "hint": (
                    "Diese Buchung ist bereits einem anderen Vorgang zugeordnet. Eine "
                    "Sammelueberweisung deckt mehrere Schaeden - aber nur bis zu ihrem Betrag."
                ),
            },
        )
    if "INSURANCE_CLAIM_PAYMENT_MUST_BE_INCOMING" in message:
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=INSURANCE_CLAIM_PAYMENT_DIRECTION,
        )
    if "INSURANCE_CLAIM_PAYMENT_MUST_BE_OUTGOING" in message:
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=INSURANCE_CLAIM_PAYMENT_DIRECTION,
        )
    if "INSURANCE_CLAIM_PAYMENT_TRANSACTION_NOT_FOUND" in message:
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=INSURANCE_CLAIM_TRANSACTION_NOT_FOUND,
        )
    # Alles Uebrige sind die CHECKs der Tabelle - Meldung vor Eintritt,
    # Teilregulierung ohne Begruendung, Anker ohne Grund. Sie haben keinen
    # eigenen Code, weil das Eingabemodell sie bereits abfaengt; hier landet nur,
    # wer an ihm vorbei schreibt.
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="insurance_claim_rejected"
    )


# ---------------------------------------------------------------------------
# Das Sonderkuendigungsfenster - reiner Rechenkern
# ---------------------------------------------------------------------------


def _period_end_on(main_due_on: date | None, today: date) -> date | None:
    """Letzter Tag der laufenden Versicherungsperiode, oder ``None``.

    Paragraph 92 Abs. 2 S. 3 VVG deckelt den Versicherungsnehmer auf den Schluss
    der laufenden Periode; ueber Paragraph 111 Abs. 2 S. 2 gilt das auch fuer die
    Haftpflicht. Die Periode laeuft von Hauptfaelligkeit zu Hauptfaelligkeit -
    ohne dieses Datum gibt es keinen Deckel, sondern eine Leerstelle.
    """
    if main_due_on is None:
        return None
    naechste = main_due_on
    for _ in range(200):
        if naechste > today:
            break
        naechste = add_months(naechste, 12)
    for _ in range(200):
        vorher = add_months(naechste, -12)
        if vorher <= today:
            break
        naechste = vorher
    return naechste - timedelta(days=1)


def compute_window(
    *,
    today: date,
    termination_basis: str,
    claim_status: str,
    has_policy: bool,
    anchor_on: date | None,
    anchor_reason: str | None,
    anchor_source: str | None,
    anchor_certainty: str,
    anchor_transaction_id: UUID | None,
    effect_options: list[str],
    service_channel: str,
    recipient_state: str | None,
    main_due_on: date | None,
    main_due_source: str | None,
    suggestion: ClaimAnchorSuggestion | None = None,
) -> ClaimTerminationWindow:
    """Rechnet das Sonderkuendigungsfenster eines Schadenvorgangs.

    Gibt IMMER ein Ergebnis zurueck, nie ``None``: "kein Recht" ist eine
    Aussage, kein fehlender Wert. Und sie ist eine ANDERE Aussage als "das Recht
    besteht, sein Ende steht noch nicht fest" - der Unterschied zwischen `none`
    und `open_undetermined` ist der ganze Punkt dieses Rechenkerns.
    """
    basis = cast(ClaimTerminationBasis, termination_basis)
    law = _LAW_BY_BASIS.get(termination_basis, "")
    basis_kind: Literal["statute", "policy_terms", "contract"] = (
        "policy_terms" if termination_basis == "contract" else "statute"
    )
    optionen = [
        cast(ClaimTerminationEffect, wert)
        for wert in effect_options
        if wert in ("immediate", "end_of_period")
    ]

    if not has_policy:
        return ClaimTerminationWindow(
            state="none",
            termination_basis=basis,
            law=law,
            basis_kind=basis_kind,
            rationale=(
                "Der Vorgang haengt an keiner eigenen Police - es gibt hier nichts zu "
                "kuendigen."
            ),
        )
    if termination_basis == "none":
        return ClaimTerminationWindow(
            state="none",
            termination_basis=basis,
            law=law,
            basis_kind=basis_kind,
            rationale=(
                "Fuer diese Sparte sieht das Gesetz nach einem Schaden kein "
                "Sonderkuendigungsrecht vor."
            ),
        )
    if claim_status not in _WINDOW_STATUSES:
        return ClaimTerminationWindow(
            state="none",
            termination_basis=basis,
            law=law,
            basis_kind=basis_kind,
            rationale=(
                "Solange der Schaden nicht gemeldet ist, entsteht kein Fenster."
                if claim_status == "draft"
                else "Der Anspruch wurde zurueckgenommen."
            ),
        )

    gemeinsam: dict[str, Any] = {
        "termination_basis": basis,
        "law": law,
        "basis_kind": basis_kind,
        "effect_options": optionen,
        "latest_effect_on": (
            _period_end_on(main_due_on, today) if main_due_source == "user_confirmed" else None
        ),
        "anchor_on": anchor_on,
        "anchor_reason": cast("ClaimAnchorReason | None", anchor_reason),
        "anchor_source": cast("ClaimAnchorSource | None", anchor_source),
        "anchor_certainty": cast(ClaimAnchorCertainty, anchor_certainty),
        "anchor_transaction_id": anchor_transaction_id,
        "suggestion": suggestion,
    }

    if anchor_on is None:
        # Der wichtigste Zustand des Moduls: das Recht besteht seit dem
        # Schadentag (Paragraph 92 Abs. 1 VVG), sein Ende haengt am Abschluss
        # der Verhandlungen - und der steht oft monatelang nicht fest.
        return ClaimTerminationWindow(
            state="open_undetermined",
            needs_anchor=True,
            rationale=(
                "Das Sonderkuendigungsrecht besteht seit dem Schadentag. Es endet einen "
                "Monat nachdem die Verhandlungen ueber die Entschaedigung abgeschlossen "
                "sind - dieses Datum steht noch nicht fest."
            ),
            **gemeinsam,
        )

    legal = add_months(anchor_on, 1)
    # Paragraph 193 BGB schiebt hier NICHT: der Fristtyp ist eine
    # Kuendigungsfrist (BGH III ZR 172/04). Der Rechenkern haelt dieselbe Regel
    # fuer die ordentliche Frist - sie steht deshalb hier nicht zum zweiten Mal.
    sicher = previous_delivery_day(legal, recipient_state)
    puffer = _CHANNEL_BUFFER_BUSINESS_DAYS.get(service_channel, 3)
    handeln = minus_delivery_days(sicher, puffer, recipient_state) if puffer else sicher
    abgelaufen = legal < today
    return ClaimTerminationWindow(
        state="expired" if abgelaufen else "open_until",
        deadline_legal_on=legal,
        safe_service_on=sicher,
        recommended_action_on=handeln,
        days_left=None if abgelaufen else (handeln - today).days,
        rationale=_anchor_rationale(anchor_reason, anchor_certainty, law),
        **gemeinsam,
    )


def _anchor_rationale(reason: str | None, certainty: str, law: str) -> str:
    """Woraus das Fensterende gerechnet ist - im Klartext, ohne falsche Sicherheit."""
    herkunft = {
        "final_rejection": "der endgueltigen Ablehnung",
        "full_payment": "der vollstaendigen Zahlung",
        "settlement_letter": "der Abrechnung",
        "withdrawal": "der Ruecknahme",
        "judgment_final": "der Rechtskraft des Urteils",
        "liability_acknowledged": "der Anerkennung des Freistellungsanspruchs",
        "liability_refused": "der Ablehnung des Freistellungsanspruchs",
        "user_confirmed": "dem von Ihnen bestaetigten Abschluss der Verhandlungen",
    }.get(reason or "", "dem Abschluss der Verhandlungen")
    satz = f"Gerechnet ab {herkunft}"
    if law:
        satz += f" ({law})"
    if certainty == "belegt":
        return satz + ". Der Anker haengt an einem zugeordneten Kontoumsatz."
    if certainty == "geschaetzt":
        return satz + ". Der Anker wurde von Ihnen gesetzt, das Datum ist voraussichtlich."
    return satz + "."


def suggest_anchor(
    *,
    termination_basis: str,
    claim_status: str,
    decision: str | None,
    decided_on: date | None,
    last_inbound_on: date | None,
    last_inbound_transaction_id: UUID | None,
    anchor_on: date | None,
) -> ClaimAnchorSuggestion | None:
    """Vorschlag fuer den Fristanker - oder ausdruecklich keiner.

    Die drei Faelle stehen so im Plan (4.8) und sind in ihrer Reihenfolge
    wichtig:

    * **Teilregulierung: kein Vorschlag.** Dort ist die Entscheidung der Anfang
      der Verhandlung. Wer hier ein Datum vorschlaegt, schlaegt das falsche vor.
    * **Haftpflicht:** Anerkennung oder Ablehnung ist der richtige Anker
      (Paragraph 111 Abs. 2 S. 1 VVG) - `abgeleitet`, weil das Datum aus dem
      Vorgang stammt und nicht aus einem Beleg.
    * **Volle Anerkennung und bezahlt:** das Datum der letzten Eingangszahlung.
      Der einzige Anker, den die App hart BELEGEN kann - weil sie die
      Kontoumsaetze hat.
    """
    if anchor_on is not None or termination_basis == "none":
        return None
    if claim_status not in _WINDOW_STATUSES:
        return None
    if decision == "accepted_partial":
        return None

    if termination_basis == "vvg_111" and decided_on is not None and decision is not None:
        grund: ClaimAnchorReason = (
            "liability_refused" if decision == "rejected" else "liability_acknowledged"
        )
        return ClaimAnchorSuggestion(
            negotiations_closed_on=decided_on,
            reason=grund,
            source="manual",
            certainty="abgeleitet",
            rationale=(
                "In der Haftpflicht laeuft die Frist ab Anerkennung oder Ablehnung des "
                "Freistellungsanspruchs (Paragraph 111 Abs. 2 S. 1 VVG)."
            ),
        )
    if decision == "rejected" and decided_on is not None:
        return ClaimAnchorSuggestion(
            negotiations_closed_on=decided_on,
            reason="final_rejection",
            source="manual",
            certainty="abgeleitet",
            rationale=(
                "Bei einer Ablehnung fallen Entscheidung und Abschluss der Verhandlungen "
                "zusammen - sofern die Ablehnung endgueltig ist."
            ),
        )
    if decision == "accepted_full" and claim_status == "paid" and last_inbound_on is not None:
        belegt = last_inbound_transaction_id is not None
        return ClaimAnchorSuggestion(
            negotiations_closed_on=last_inbound_on,
            reason="full_payment",
            source="transaction" if belegt else "manual",
            certainty="belegt" if belegt else "geschaetzt",
            transaction_id=last_inbound_transaction_id,
            rationale=(
                "Bei voller Anerkennung enden die Verhandlungen mit der vollstaendigen "
                "Zahlung; das Datum stammt aus der letzten zugeordneten Zahlung."
            ),
        )
    return None


def _window_from(data: dict[str, Any], *, today: date) -> ClaimTerminationWindow:
    vorschlag = suggest_anchor(
        termination_basis=str(data["termination_basis"]),
        claim_status=str(data["status"]),
        decision=cast("str | None", data.get("decision")),
        decided_on=cast("date | None", data.get("decided_on")),
        last_inbound_on=cast("date | None", data.get("last_inbound_on")),
        last_inbound_transaction_id=cast("UUID | None", data.get("last_inbound_transaction_id")),
        anchor_on=cast("date | None", data.get("negotiations_closed_on")),
    )
    return compute_window(
        today=today,
        termination_basis=str(data["termination_basis"]),
        claim_status=str(data["status"]),
        has_policy=data.get("policy_id") is not None,
        anchor_on=cast("date | None", data.get("negotiations_closed_on")),
        anchor_reason=cast("str | None", data.get("negotiations_closed_reason")),
        anchor_source=cast("str | None", data.get("negotiations_closed_source")),
        anchor_certainty=str(data.get("negotiations_closed_certainty") or "unbekannt"),
        anchor_transaction_id=cast(
            "UUID | None", data.get("negotiations_closed_transaction_id")
        ),
        effect_options=list(data.get("termination_effect_options") or []),
        service_channel=str(data.get("service_channel") or "post"),
        recipient_state=cast("str | None", data.get("recipient_state")),
        main_due_on=cast("date | None", data.get("main_due_on")),
        main_due_source=cast("str | None", data.get("main_due_source")),
        suggestion=vorschlag,
    )


def _as_claim(data: dict[str, Any], *, today: date) -> InsuranceClaim:
    anerkannt = data.get("approved_minor")
    grundlage = int(anerkannt) if anerkannt is not None else int(data["claimed_minor"])
    return InsuranceClaim(
        **data,
        open_minor=max(grundlage - int(data["settled_minor"]), 0),
        special_termination=_window_from(data, today=today),
    )


def _as_payment(data: dict[str, Any]) -> ClaimPayment:
    return ClaimPayment(**data)


# ---------------------------------------------------------------------------
# Lesen
# ---------------------------------------------------------------------------


async def _require_policy_context(
    session: AsyncSession, household_id: UUID, policy_id: UUID
) -> dict[str, Any]:
    """Die Policenfelder, die ein Vorgang braucht - samt Gesundheitskennzeichen."""
    row = (
        await session.execute(
            text(
                "select p.id, p.is_health_scoped, p.line_key, p.insurer_name, "
                "p.policy_number_norm, p.service_channel, p.recipient_state, "
                "p.main_due_on, p.main_due_source, "
                "l.default_claim_termination_basis "
                "from insurance_policies p "
                "join insurance_lines l on l.line_key = p.line_key "
                "where p.id = :id and p.household_id = :household_id"
            ),
            {"id": str(policy_id), "household_id": str(household_id)},
        )
    ).first()
    if row is None:
        # 404 statt 403 bei fremdem Haushalt und bei einer gesundheitsnahen
        # Police ohne Freigabe: die Existenz einer fremden Ressource soll nicht
        # verraten werden.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=insurance_service.INSURANCE_POLICY_NOT_FOUND,
        )
    return dict(row._mapping)


async def _require_claim(
    session: AsyncSession, household_id: UUID, claim_id: UUID
) -> dict[str, Any]:
    row = (
        await session.execute(
            text(
                f"select {_CLAIM_COLUMNS}, {_CLAIM_CONTEXT_COLUMNS} {_CLAIM_FROM} "
                "where c.id = :id and c.household_id = :household_id"
            ),
            {"id": str(claim_id), "household_id": str(household_id)},
        )
    ).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=INSURANCE_CLAIM_NOT_FOUND
        )
    return dict(row._mapping)


async def list_claims(
    session: AsyncSession,
    household_id: UUID,
    *,
    today: date,
    policy_id: UUID | None = None,
    claim_status: str | None = None,
    include_closed: bool = True,
) -> list[InsuranceClaim]:
    """Die Vorgaenge des Haushalts, juengster Schadentag zuerst."""
    bedingungen = ["c.household_id = :household_id"]
    parameter: dict[str, Any] = {"household_id": str(household_id)}
    if policy_id is not None:
        # Eine fremde oder gesundheitsnah verdeckte Police muss 404 antworten und
        # nicht die leere Liste: sonst laesst sich ueber den Filter erraten, dass
        # es sie ueberhaupt gibt. Der Schreibweg auf demselben Pfad tut das schon.
        await _require_policy_context(session, household_id, policy_id)
        bedingungen.append("c.policy_id = cast(:policy_id as uuid)")
        parameter["policy_id"] = str(policy_id)
    if claim_status is not None:
        bedingungen.append("c.status = :claim_status")
        parameter["claim_status"] = claim_status
    elif not include_closed:
        bedingungen.append("c.status not in ('paid','closed','withdrawn')")
    rows = (
        await session.execute(
            text(
                f"select {_CLAIM_COLUMNS}, {_CLAIM_CONTEXT_COLUMNS} {_CLAIM_FROM} "
                "where " + " and ".join(bedingungen) + " "
                "order by c.occurred_on desc, c.created_at desc"
            ),
            parameter,
        )
    ).all()
    return [_as_claim(dict(row._mapping), today=today) for row in rows]


async def get_claim(
    session: AsyncSession, household_id: UUID, claim_id: UUID, *, today: date
) -> InsuranceClaimDetail:
    """Der Vorgang mit Zahlungen und Zeitstrahl."""
    data = await _require_claim(session, household_id, claim_id)
    basis = _as_claim(data, today=today)
    return InsuranceClaimDetail(
        **basis.model_dump(),
        payments=await _list_payments(session, household_id, claim_id),
        events=await _list_events(session, household_id, claim_id),
    )


async def _list_events(
    session: AsyncSession, household_id: UUID, claim_id: UUID
) -> list[ClaimEvent]:
    rows = (
        await session.execute(
            text(
                "select id, claim_id, from_status, to_status, occurred_on, payload, "
                "created_at from insurance_claim_events "
                "where claim_id = :claim_id and household_id = :household_id "
                "order by occurred_on, id"
            ),
            {"claim_id": str(claim_id), "household_id": str(household_id)},
        )
    ).all()
    return [ClaimEvent(**dict(row._mapping)) for row in rows]


async def list_payments(
    session: AsyncSession, household_id: UUID, claim_id: UUID
) -> list[ClaimPayment]:
    """Die Zahlungen eines Vorgangs - erst pruefen, ob es ihn gibt.

    Ohne die Pruefung antwortet ein fremder und sogar ein frei erfundener
    Vorgang mit der leeren Liste statt mit 404.
    """
    await _require_claim(session, household_id, claim_id)
    return await _list_payments(session, household_id, claim_id)


async def _list_payments(
    session: AsyncSession, household_id: UUID, claim_id: UUID
) -> list[ClaimPayment]:
    rows = (
        await session.execute(
            text(
                f"select {_PAYMENT_COLUMNS} {_PAYMENT_FROM} "
                "where cp.claim_id = :claim_id and cp.household_id = :household_id "
                "order by cp.booked_on, cp.created_at"
            ),
            {"claim_id": str(claim_id), "household_id": str(household_id)},
        )
    ).all()
    return [_as_payment(dict(row._mapping)) for row in rows]


# ---------------------------------------------------------------------------
# Schreiben
# ---------------------------------------------------------------------------


async def _event(
    session: AsyncSession,
    household_id: UUID,
    claim_id: UUID,
    *,
    from_status: str | None,
    to_status: str,
    occurred_on: date,
    payload: dict[str, Any],
) -> None:
    """Schreibt eine Zeile in den Zeitstrahl.

    Abgeleitete Fakten, keine Anzeigetexte: `payload` traegt, was geschehen ist.
    Fertige Saetze in der Datenbank waeren beim ersten Sprachwechsel falsch -
    und im Zweifel aelter als die Fassung, die sie erklaeren sollen.
    """
    await session.execute(
        text(
            "insert into insurance_claim_events "
            "(household_id, claim_id, from_status, to_status, occurred_on, payload) "
            "values (:household_id, :claim_id, :from_status, :to_status, :occurred_on, "
            "cast(:payload as jsonb))"
        ),
        {
            "household_id": str(household_id),
            "claim_id": str(claim_id),
            "from_status": from_status,
            "to_status": to_status,
            "occurred_on": occurred_on,
            "payload": json.dumps(payload, default=str),
        },
    )


async def _persist_window(
    session: AsyncSession, household_id: UUID, claim_id: UUID, *, today: date
) -> None:
    """Schreibt den frisch gerechneten Fensterzustand an die Zeile zurueck.

    Gespeichert wird nur, weil der naechtliche Push einen Dedup-Anker braucht
    und der Worker nicht je Zeile nachrechnen soll; die API antwortet immer mit
    dem frisch gerechneten Stand. Ein Update ohne Aenderung bumpt die Version
    nicht - der Versionstrigger vergleicht die Zeile.
    """
    data = await _require_claim(session, household_id, claim_id)
    fenster = _window_from(data, today=today)
    await session.execute(
        text(
            "update insurance_claims set special_termination_state = :state, "
            "special_termination_until = :until "
            "where id = :claim_id and household_id = :household_id"
        ),
        {
            "state": fenster.state,
            "until": fenster.deadline_legal_on,
            "claim_id": str(claim_id),
            "household_id": str(household_id),
        },
    )


async def _reconcile(session: AsyncSession, household_id: UUID, claim_id: UUID) -> None:
    """Rechnet `settled_minor` und `paid_to_third_party` aus den Zahlungen neu.

    Die Summe wird jedes Mal neu gebildet, nie fortgeschrieben - eine entfernte
    Zuordnung faellt damit von allein aus dem Ergebnis. `paid_to_third_party`
    wird dabei nur GESETZT, nie zurueckgenommen: eine belegte Direktzahlung ist
    ein Fakt, und der Nutzer darf das Kennzeichen unabhaengig davon selbst
    setzen (es steht in `_CLAIM_UPDATABLE_COLUMNS`).
    """
    arten = ", ".join(f"'{art}'" for art in _SETTLEMENT_KINDS)
    await session.execute(
        text(
            "update insurance_claims c set settled_minor = s.summe, "
            "paid_to_third_party = (c.paid_to_third_party or s.direkt > 0) "
            "from (select coalesce(sum(amount_minor) filter ("
            f"  where direction = 'inbound' and kind in ({arten})), 0) as summe, "
            "  count(*) filter (where direction = 'inbound' and transaction_id is null) "
            "  as direkt "
            "  from insurance_claim_payments "
            "  where claim_id = :claim_id and household_id = :household_id) s "
            "where c.id = :claim_id and c.household_id = :household_id"
        ),
        {"claim_id": str(claim_id), "household_id": str(household_id)},
    )


async def create_claim(
    session: AsyncSession,
    household_id: UUID,
    payload: InsuranceClaimCreate,
    *,
    today: date,
    policy_id: UUID | None = None,
) -> InsuranceClaim:
    """Legt einen Vorgang an.

    Die Sparte belegt `termination_basis` vor - geraten wird nichts. Ohne Police
    steht sie auf 'none': ein Schaden gegen eine fremde Versicherung gibt kein
    Kuendigungsrecht an einem eigenen Vertrag, den es nicht gibt.
    """
    ziel = policy_id if policy_id is not None else payload.policy_id
    police: dict[str, Any] | None = None
    if ziel is not None:
        police = await _require_policy_context(session, household_id, ziel)
        if bool(police["is_health_scoped"]):
            raise _health_scoped_error()

    basis = payload.termination_basis or (
        str(police["default_claim_termination_basis"]) if police is not None else "none"
    )
    fenster = compute_window(
        today=today,
        termination_basis=basis,
        claim_status=payload.status,
        has_policy=ziel is not None,
        anchor_on=None,
        anchor_reason=None,
        anchor_source=None,
        anchor_certainty="unbekannt",
        anchor_transaction_id=None,
        effect_options=list(payload.termination_effect_options or ["end_of_period"]),
        service_channel=str(police["service_channel"]) if police else "post",
        recipient_state=cast("str | None", police["recipient_state"]) if police else None,
        main_due_on=cast("date | None", police["main_due_on"]) if police else None,
        main_due_source=cast("str | None", police["main_due_source"]) if police else None,
    )

    werte: dict[str, Any] = {
        "household_id": str(household_id),
        "policy_id": str(ziel) if ziel is not None else None,
        "insured_object_id": (
            str(payload.insured_object_id) if payload.insured_object_id else None
        ),
        "title": payload.title,
        "claim_number": payload.claim_number,
        "occurred_on": payload.occurred_on,
        # Kein Ersatzdatum, wenn der Vorgang schon als gemeldet erfasst wird:
        # wann die Meldung raus war, weiss der Nutzer, und "gleich am Schadentag"
        # waere geraten. Das Fenster haengt ohnehin am Status, nicht an diesem
        # Datum; nur die Rueckfrage "seit 30 Tagen ohne Rueckmeldung" braucht es.
        "reported_on": payload.reported_on,
        "location": payload.location,
        "description": payload.description,
        "police_reference": payload.police_reference,
        "status": payload.status,
        "claimed_minor": payload.claimed_minor,
        "approved_minor": payload.approved_minor,
        "deductible_minor": payload.deductible_minor,
        "next_action": payload.next_action,
        "next_action_due": payload.next_action_due,
        "termination_basis": basis,
        "termination_effect_options": list(
            payload.termination_effect_options or ["end_of_period"]
        ),
        "special_termination_state": fenster.state,
        "special_termination_until": fenster.deadline_legal_on,
    }
    try:
        row = (
            await session.execute(
                text(
                    "insert into insurance_claims ("
                    + ", ".join(_CLAIM_INSERT_COLUMNS)
                    + ") values ("
                    + ", ".join(_placeholder(column) for column in _CLAIM_INSERT_COLUMNS)
                    + ") returning id"
                ),
                werte,
            )
        ).first()
    except IntegrityError as exc:
        raise _translate_integrity_error(exc) from exc
    assert row is not None  # "returning id" liefert immer eine Zeile

    await _event(
        session,
        household_id,
        row.id,
        from_status=None,
        to_status=payload.status,
        occurred_on=payload.reported_on or payload.occurred_on,
        payload={"claimed_minor": payload.claimed_minor, "termination_basis": basis},
    )
    return _as_claim(await _require_claim(session, household_id, row.id), today=today)


async def update_claim(
    session: AsyncSession,
    household_id: UUID,
    claim_id: UUID,
    payload: InsuranceClaimUpdate,
    *,
    today: date,
    expected: int | None,
) -> InsuranceClaim:
    """Teilaenderung mit `If-Match`.

    Der Fristanker fehlt hier bewusst: er entsteht ausschliesslich ueber
    `close-negotiations`, damit ein Datum nie ohne Grund und ohne Sicherheit in
    der Zeile landet. Dieselbe Haltung wie bei `main_due_source` an der Police.
    """
    vorher = await _require_claim(session, household_id, claim_id)
    _assert_version(vorher, expected)

    changes: dict[str, Any] = payload.model_dump(exclude_unset=True)
    changes.pop("clear_fields", None)
    # Ein ausdruecklicher Loeschwunsch ist die spezifischere Angabe und gewinnt
    # gegen einen gleichzeitig gesetzten Wert (Muster aus routes/contracts.py).
    for geleert in payload.clear_fields:
        changes[geleert] = None

    if changes.get("policy_id") is not None:
        police = await _require_policy_context(
            session, household_id, cast(UUID, changes["policy_id"])
        )
        if bool(police["is_health_scoped"]):
            raise _health_scoped_error()

    felder = [column for column in _CLAIM_UPDATABLE_COLUMNS if column in changes]
    if felder:
        parameter: dict[str, Any] = {
            column: _param_value(column, changes[column]) for column in felder
        }
        parameter["claim_id"] = str(claim_id)
        parameter["household_id"] = str(household_id)
        try:
            await session.execute(
                text(
                    "update insurance_claims set "
                    + ", ".join(f"{column} = {_placeholder(column)}" for column in felder)
                    + " where id = :claim_id and household_id = :household_id"
                ),
                parameter,
            )
        except IntegrityError as exc:
            raise _translate_integrity_error(exc) from exc
        # Die Sparte, der Status oder die Wirkungswahl koennen sich geaendert
        # haben - das Fenster gilt danach neu.
        await _persist_window(session, household_id, claim_id, today=today)
    return _as_claim(await _require_claim(session, household_id, claim_id), today=today)


async def delete_claim(session: AsyncSession, household_id: UUID, claim_id: UUID) -> None:
    """Loescht den Vorgang samt Zahlungen und Zeitstrahl.

    Die zugeordneten BUCHUNGEN bleiben unberuehrt - geloescht wird die
    Zuordnung, nicht das Geld. Genau deshalb steht der Fremdschluessel auf
    `set null` und nicht umgekehrt.
    """
    await _require_claim(session, household_id, claim_id)
    await session.execute(
        text("delete from insurance_claims where id = :id and household_id = :household_id"),
        {"id": str(claim_id), "household_id": str(household_id)},
    )


async def change_status(
    session: AsyncSession,
    household_id: UUID,
    claim_id: UUID,
    payload: ClaimStatusChange,
    *,
    today: date,
    expected: int | None = None,
) -> InsuranceClaim:
    """Statuswechsel als Einfeldaktion - und ausdruecklich ohne Fristdatum.

    Beim Uebergang nach `reported` entsteht `open_undetermined`: das Recht
    besteht ab Eintritt des Versicherungsfalles, sein Ende steht noch nicht
    fest. Die alte Regel `special_termination_until = decided_on + 1 Monat` ist
    ersatzlos entfallen (Plan 3.12, Pruefung 3).
    """
    vorher = await _require_claim(session, household_id, claim_id)
    _assert_version(vorher, expected)
    ereignis_am = payload.occurred_on or today

    felder: list[str] = ["status"]
    parameter: dict[str, Any] = {"status": payload.to_status}
    if payload.decision is not None:
        felder.append("decision")
        parameter["decision"] = payload.decision
        felder.append("decided_on")
        parameter["decided_on"] = payload.decided_on or ereignis_am
    if payload.reduction_reason is not None:
        felder.append("reduction_reason")
        parameter["reduction_reason"] = payload.reduction_reason
    if payload.to_status == "reported" and vorher.get("reported_on") is None:
        # Ein bereits erfasstes Meldedatum wird NIE ueberschrieben: der Nutzer
        # traegt den Vorgang oft nach, und dann ist sein Datum das richtige.
        felder.append("reported_on")
        parameter["reported_on"] = ereignis_am

    parameter["claim_id"] = str(claim_id)
    parameter["household_id"] = str(household_id)
    try:
        await session.execute(
            text(
                "update insurance_claims set "
                + ", ".join(f"{column} = :{column}" for column in felder)
                + " where id = :claim_id and household_id = :household_id"
            ),
            parameter,
        )
    except IntegrityError as exc:
        raise _translate_integrity_error(exc) from exc

    await _persist_window(session, household_id, claim_id, today=today)
    await _event(
        session,
        household_id,
        claim_id,
        from_status=str(vorher["status"]),
        to_status=payload.to_status,
        occurred_on=ereignis_am,
        payload={
            "decision": payload.decision,
            "note": payload.note,
        },
    )
    return _as_claim(await _require_claim(session, household_id, claim_id), today=today)


async def close_negotiations(
    session: AsyncSession,
    household_id: UUID,
    claim_id: UUID,
    payload: ClaimCloseNegotiations,
    *,
    today: date,
    expected: int | None = None,
) -> InsuranceClaim:
    """Setzt den Fristanker - die einzige Stelle, an der er entsteht.

    Die Sicherheit wird nicht uebernommen, sondern abgeleitet: mit zugeordnetem
    Kontoumsatz `belegt`, aus dem Entscheidungsdatum `abgeleitet`, sonst
    `geschaetzt`. Ein Client kann sie deshalb nicht behaupten.
    """
    vorher = await _require_claim(session, household_id, claim_id)
    _assert_version(vorher, expected)
    if str(vorher["termination_basis"]) == "none" or vorher.get("policy_id") is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": INSURANCE_CLAIM_NO_TERMINATION_RIGHT,
                "hint": (
                    "Ohne eigene Police und ohne Sonderkuendigungsrecht der Sparte gibt es "
                    "kein Fenster, das ein Anker beenden koennte."
                ),
            },
        )
    if payload.negotiations_closed_on < cast(date, vorher["occurred_on"]):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=INSURANCE_CLAIM_ANCHOR_BEFORE_EVENT,
        )

    quelle: ClaimAnchorSource = "manual"
    sicherheit: ClaimAnchorCertainty = "geschaetzt"
    if payload.transaction_id is not None:
        await _require_transaction(session, household_id, payload.transaction_id)
        quelle = "transaction"
        sicherheit = "belegt"
    elif (
        vorher.get("decided_on") is not None
        and payload.negotiations_closed_on == vorher["decided_on"]
        and payload.reason
        in ("final_rejection", "liability_acknowledged", "liability_refused", "judgment_final")
    ):
        sicherheit = "abgeleitet"

    try:
        await session.execute(
            text(
                "update insurance_claims set negotiations_closed_on = :anchor_on, "
                "negotiations_closed_reason = :reason, negotiations_closed_source = :source, "
                "negotiations_closed_certainty = :certainty, "
                "negotiations_closed_transaction_id = cast(:transaction_id as uuid) "
                "where id = :claim_id and household_id = :household_id"
            ),
            {
                "anchor_on": payload.negotiations_closed_on,
                "reason": payload.reason,
                "source": quelle,
                "certainty": sicherheit,
                "transaction_id": (
                    str(payload.transaction_id) if payload.transaction_id else None
                ),
                "claim_id": str(claim_id),
                "household_id": str(household_id),
            },
        )
    except IntegrityError as exc:
        raise _translate_integrity_error(exc) from exc

    await _persist_window(session, household_id, claim_id, today=today)
    await _event(
        session,
        household_id,
        claim_id,
        from_status=str(vorher["status"]),
        to_status=str(vorher["status"]),
        occurred_on=payload.negotiations_closed_on,
        payload={
            "negotiations_closed_on": payload.negotiations_closed_on,
            "reason": payload.reason,
            "certainty": sicherheit,
            "transaction_id": payload.transaction_id,
        },
    )
    return _as_claim(await _require_claim(session, household_id, claim_id), today=today)


# ---------------------------------------------------------------------------
# Zahlungen
# ---------------------------------------------------------------------------


async def _require_transaction(
    session: AsyncSession, household_id: UUID, transaction_id: UUID
) -> dict[str, Any]:
    row = (
        await session.execute(
            text(
                "select id, booked_date, amount_minor from transactions "
                "where id = :id and household_id = :household_id"
            ),
            {"id": str(transaction_id), "household_id": str(household_id)},
        )
    ).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=INSURANCE_CLAIM_TRANSACTION_NOT_FOUND,
        )
    return dict(row._mapping)


async def create_payment(
    session: AsyncSession,
    household_id: UUID,
    claim_id: UUID,
    payload: ClaimPaymentCreate,
) -> ClaimPayment:
    """Ordnet eine Zahlung zu.

    Ohne `transaction_id` ist es die Direktzahlung an die Werkstatt: sie hat
    keine Buchung, aber sehr wohl eine Zahlung - und setzt deshalb
    `paid_to_third_party`.
    """
    claim = await _require_claim(session, household_id, claim_id)
    gebucht_am = payload.booked_on
    if payload.transaction_id is not None:
        buchung = await _require_transaction(session, household_id, payload.transaction_id)
        gebucht_am = gebucht_am or cast(date, buchung["booked_date"])
    assert gebucht_am is not None  # das Eingabemodell erzwingt das eine oder das andere

    try:
        row = (
            await session.execute(
                text(
                    "insert into insurance_claim_payments (household_id, claim_id, "
                    "transaction_id, direction, kind, amount_minor, booked_on, note) "
                    "values (:household_id, :claim_id, cast(:transaction_id as uuid), "
                    ":direction, :kind, :amount_minor, :booked_on, :note) returning id"
                ),
                {
                    "household_id": str(household_id),
                    "claim_id": str(claim_id),
                    "transaction_id": (
                        str(payload.transaction_id) if payload.transaction_id else None
                    ),
                    "direction": payload.direction,
                    "kind": payload.kind,
                    "amount_minor": payload.amount_minor,
                    "booked_on": gebucht_am,
                    "note": payload.note,
                },
            )
        ).first()
    except IntegrityError as exc:
        raise _translate_integrity_error(exc) from exc
    assert row is not None  # "returning id" liefert immer eine Zeile

    await _reconcile(session, household_id, claim_id)
    await _event(
        session,
        household_id,
        claim_id,
        from_status=str(claim["status"]),
        to_status=str(claim["status"]),
        occurred_on=gebucht_am,
        payload={
            "payment": "added",
            "direction": payload.direction,
            "kind": payload.kind,
            "amount_minor": payload.amount_minor,
            "transaction_id": payload.transaction_id,
        },
    )
    zeilen = await _list_payments(session, household_id, claim_id)
    return next(zeile for zeile in zeilen if zeile.id == row.id)


async def delete_payment(
    session: AsyncSession,
    household_id: UUID,
    claim_id: UUID,
    payment_id: UUID,
    *,
    today: date,
) -> None:
    """Nimmt eine Zuordnung zurueck. Die Buchung selbst bleibt unberuehrt."""
    claim = await _require_claim(session, household_id, claim_id)
    ergebnis = await session.execute(
        text(
            "delete from insurance_claim_payments where id = :id and claim_id = :claim_id "
            "and household_id = :household_id"
        ),
        {"id": str(payment_id), "claim_id": str(claim_id), "household_id": str(household_id)},
    )
    if _rowcount(ergebnis) == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=INSURANCE_CLAIM_PAYMENT_NOT_FOUND
        )
    await _reconcile(session, household_id, claim_id)
    await _event(
        session,
        household_id,
        claim_id,
        from_status=str(claim["status"]),
        to_status=str(claim["status"]),
        occurred_on=today,
        payload={"payment": "removed", "payment_id": payment_id},
    )


# ---------------------------------------------------------------------------
# Zahlungskandidaten
# ---------------------------------------------------------------------------


async def transaction_candidates(
    session: AsyncSession,
    household_id: UUID,
    claim_id: UUID,
    *,
    today: date,
    direction: ClaimPaymentDirection = "inbound",
) -> list[ClaimTransactionCandidate]:
    """Buchungen, die zu diesem Vorgang passen koennten - gerankt, nie gesetzt.

    Vorbild ist `reimbursement_service.transaction_candidates`, und zwar bis in
    die Gewichte: Betrag und Beleg tragen den Vorschlag, Datum und Name stuetzen
    ihn. Der Unterschied liegt im Beleg - hier ist es die Schadennummer, sonst
    die Belegnummer der Rechnung.
    """
    claim = await _require_claim(session, household_id, claim_id)
    anerkannt = claim.get("approved_minor")
    grundlage = int(anerkannt) if anerkannt is not None else int(claim["claimed_minor"])
    ziel = max(grundlage - int(claim["settled_minor"]), 0)
    beginn = cast(
        date, claim.get("reported_on") or claim["occurred_on"]
    )
    vorzeichen = "t.amount_minor > 0" if direction == "inbound" else "t.amount_minor < 0"

    rows = (
        await session.execute(
            text(
                "select t.id, t.booked_date, t.amount_minor, t.payee_label, t.note, "
                "greatest(abs(t.amount_minor) - coalesce((select sum(cp.amount_minor) "
                "  from insurance_claim_payments cp where cp.transaction_id = t.id "
                "   and cp.household_id = t.household_id), 0), 0) as remaining_minor "
                "from transactions t "
                "where t.household_id = :household_id and not t.is_archived and "
                + vorzeichen
                + " and t.booked_date between :start_date and :end_date "
                "order by abs(abs(t.amount_minor) - :target_minor), t.booked_date desc "
                "limit 60"
            ),
            {
                "household_id": str(household_id),
                "start_date": beginn,
                "end_date": today,
                "target_minor": ziel,
            },
        )
    ).all()

    schaden_nr = _digits(cast("str | None", claim.get("claim_number")))
    schein_nr = _digits(cast("str | None", claim.get("policy_number_norm")))
    versicherer = payee_key(cast("str | None", claim.get("insurer_name")))
    ergebnis: list[ClaimTransactionCandidate] = []
    for row in rows:
        if row.remaining_minor <= 0:
            continue
        beleg: list[str] = []
        punkte = 0.0
        heuhaufen = _digits(f"{row.note or ''}{row.payee_label or ''}")
        # Die Schadennummer im Verwendungszweck ist der einzige harte Hinweis -
        # aber sie fehlt je nach Bank und Format. Sie zaehlt deshalb als starkes
        # Signal neben Betrag und Datum, nicht als Bedingung. Unter vier Ziffern
        # traefe sie zufaellig.
        if len(schaden_nr) >= 4 and schaden_nr in heuhaufen:
            punkte += 0.55
            beleg.append("Schadennummer steht im Verwendungszweck")
        elif len(schein_nr) >= 4 and schein_nr in heuhaufen:
            punkte += 0.35
            beleg.append("Versicherungsscheinnummer steht im Verwendungszweck")
        betrag = abs(int(row.amount_minor))
        if ziel and abs(betrag - ziel) <= 1:
            punkte += 0.62
            beleg.append("Betrag stimmt ueberein")
        elif ziel and abs(betrag - ziel) / ziel <= 0.05:
            punkte += 0.35
            beleg.append("Betrag liegt im 5%-Fenster")
        gegenseite = payee_key(cast("str | None", row.payee_label))
        if versicherer and gegenseite and (versicherer in gegenseite or gegenseite in versicherer):
            punkte += 0.28
            beleg.append("Versicherer stimmt ueberein")
        if abs((row.booked_date - beginn).days) <= 60:
            punkte += 0.10
            beleg.append("Datum liegt nahe an der Meldung")
        if punkte >= 0.25:
            ergebnis.append(
                ClaimTransactionCandidate(
                    transaction_id=row.id,
                    booked_date=row.booked_date,
                    amount_minor=row.amount_minor,
                    payee_label=row.payee_label,
                    note=row.note,
                    remaining_minor=int(row.remaining_minor),
                    score=min(round(punkte, 3), 1.0),
                    evidence=beleg,
                )
            )
    return sorted(ergebnis, key=lambda eintrag: (-eintrag.score, -eintrag.booked_date.toordinal()))[
        :20
    ]


# ---------------------------------------------------------------------------
# Erstattungsbruecke (Plan 9.1)
# ---------------------------------------------------------------------------


def _name_score(payer_name: str, insurer_name: str) -> tuple[float, str | None]:
    """Wie nah liegen zwei Namen - in `payee_key()`-Normalform.

    Dieselbe Normalisierung wie im Vertragsabgleich, damit "Barmenia
    Krankenversicherung AG" und "Barmenia" denselben Schluessel bekommen wie
    ueberall sonst im Projekt.
    """
    links = payee_key(payer_name)
    rechts = payee_key(insurer_name)
    if not links or not rechts:
        return 0.0, None
    if links == rechts:
        return 0.9, "Name stimmt ueberein"
    if links in rechts or rechts in links:
        return 0.6, "Name ist enthalten"
    gemeinsam = {teil for teil in links.split() if len(teil) >= 4} & {
        teil for teil in rechts.split() if len(teil) >= 4
    }
    if gemeinsam:
        return 0.35, "Namensbestandteil stimmt ueberein"
    return 0.0, None


async def suggest_policy_for_payer(
    session: AsyncSession,
    household_id: UUID,
    payer_id: UUID,
    payload: PayerPolicyLink | None = None,
) -> PayerPolicySuggestion:
    """Welche Police gehoert zu diesem Kostentraeger - und optional: verknuepfe sie.

    Ohne Feld im Koerper schreibt der Aufruf nichts. Das ist die ganze Bruecke
    aus 9.1: kein Zwang, keine Datenmigration, keine stille Verknuepfung. Das
    Erstattungsmodul funktioniert unveraendert weiter, auch wenn nie eine Police
    angelegt wird - dort liegen echte Nutzerdaten.
    """
    row = (
        await session.execute(
            text(
                "select id, name, payer_type, policy_id from reimbursement_payers "
                "where id = :id and household_id = :household_id"
            ),
            {"id": str(payer_id), "household_id": str(household_id)},
        )
    ).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=INSURANCE_PAYER_NOT_FOUND
        )
    zahler = dict(row._mapping)

    if payload is not None and (payload.unlink or payload.link_policy_id is not None):
        ziel = payload.link_policy_id
        if ziel is not None:
            # Existenz und Sichtbarkeit in einem: eine gesundheitsnahe Police
            # ohne Freigabe kommt hier gar nicht an.
            await _require_policy_context(session, household_id, ziel)
        await session.execute(
            text(
                "update reimbursement_payers set policy_id = cast(:policy_id as uuid), "
                "updated_at = now() where id = :id and household_id = :household_id"
            ),
            {
                "policy_id": str(ziel) if ziel is not None else None,
                "id": str(payer_id),
                "household_id": str(household_id),
            },
        )
        zahler["policy_id"] = ziel

    policen = (
        await session.execute(
            text(
                "select p.id, p.insurer_name, p.label, p.line_key, p.is_health_scoped, "
                "l.label as line_label, l.risk_field "
                "from insurance_policies p join insurance_lines l on l.line_key = p.line_key "
                "where p.household_id = :household_id and p.status <> 'entered_in_error'"
            ),
            {"household_id": str(household_id)},
        )
    ).all()

    vorschlaege: list[PolicySuggestion] = []
    for police in policen:
        punkte, beleg = _name_score(str(zahler["name"]), str(police.insurer_name))
        belege = [beleg] if beleg else []
        # Ein PKV-Zahler gehoert zu einer gesundheitsnahen Police. Das ist ein
        # Indiz und keine Bedingung: der Name traegt den Vorschlag.
        if str(zahler["payer_type"]) in ("pkv", "supplementary") and bool(
            police.is_health_scoped
        ):
            punkte += 0.2
            belege.append("Sparte passt zur Art des Kostentraegers")
        if punkte < 0.3:
            continue
        vorschlaege.append(
            PolicySuggestion(
                policy_id=police.id,
                insurer_name=police.insurer_name,
                label=police.label,
                line_key=police.line_key,
                line_label=police.line_label,
                is_health_scoped=bool(police.is_health_scoped),
                score=min(round(punkte, 3), 1.0),
                evidence=belege,
            )
        )
    vorschlaege.sort(key=lambda eintrag: -eintrag.score)

    hinweis = ""
    if str(zahler["payer_type"]) == "beihilfe" and not vorschlaege:
        hinweis = "Beihilfestellen haben keine Police - hier bleibt die Verknuepfung leer."
    elif not vorschlaege:
        hinweis = "Keine Police mit aehnlichem Namen. Die Verknuepfung ist freiwillig."
    return PayerPolicySuggestion(
        payer_id=cast(UUID, zahler["id"]),
        payer_name=str(zahler["name"]),
        payer_type=str(zahler["payer_type"]),
        policy_id=cast("UUID | None", zahler["policy_id"]),
        suggestions=vorschlaege[:5],
        hint=hinweis,
    )


__all__ = [
    "INSURANCE_CLAIM_HEALTH_SCOPED",
    "INSURANCE_CLAIM_NOT_FOUND",
    "change_status",
    "claim_etag",
    "close_negotiations",
    "compute_window",
    "create_claim",
    "create_payment",
    "delete_claim",
    "delete_payment",
    "get_claim",
    "list_claims",
    "list_payments",
    "suggest_anchor",
    "suggest_policy_for_payer",
    "transaction_candidates",
    "update_claim",
]
