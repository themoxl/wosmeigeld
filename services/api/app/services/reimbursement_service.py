"""Fachlogik fuer Gesundheitsrechnungen und Erstattungsabgleich.

Die Funktionen halten die vier Wahrheiten (Rechnungszahlung, Einreichung,
Entscheidung und Bankeingang) getrennt. `overall_status` ist nur eine daraus
abgeleitete Arbeitsansicht und nie die alleinige Quelle.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from sqlalchemy import Row, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.reimbursements import (
    CaseCandidate,
    InsuredPerson,
    InsuredPersonCreate,
    ReimbursementCase,
    ReimbursementCaseCreate,
    ReimbursementCaseUpdate,
    ReimbursementPayer,
    ReimbursementPayerCreate,
    ReimbursementSummary,
    TransactionAllocation,
    TransactionAllocationCreate,
    TransactionAllocationView,
    TransactionCandidate,
)

_CASE_COLUMNS = """
  c.id, c.insured_person_id as person_id, p.display_name as person_name,
  c.provider_name, c.invoice_number, c.invoice_date, c.treatment_from,
  c.treatment_to, c.due_date, c.billed_minor, c.expected_minor,
  case when c.cash_verification_required then
    coalesce((select sum(a.amount_minor) from health_transaction_allocations a
      where a.case_id = c.id and a.allocation_type = 'reimbursement'), 0)
    else c.reimbursed_minor end as reimbursed_minor,
  case when c.cash_verification_required then c.reimbursed_minor else 0 end
    as unverified_reimbursed_minor,
  c.deductible_minor, c.noncovered_minor,
  greatest(c.expected_minor - case when c.cash_verification_required then
    coalesce((select sum(a.amount_minor) from health_transaction_allocations a
      where a.case_id = c.id and a.allocation_type = 'reimbursement'), 0)
    else c.reimbursed_minor end, 0) as outstanding_minor,
  c.currency, case when c.cash_verification_required then 'action_required'
    else c.overall_status end as overall_status, c.payment_status, c.submission_status,
  c.decision_status, c.reimbursement_status, c.next_action,
  c.next_action_due, c.notes, c.version, c.created_at, c.updated_at,
  (select count(*) from reimbursement_documents d where d.case_id = c.id or exists
    (select 1 from reimbursement_document_links l where l.document_id = d.id
      and l.case_id = c.id)) as document_count
"""


def _derive_overall_status(values: dict[str, Any]) -> str:
    if int(values.get("unverified_reimbursed_minor") or 0) > 0:
        return "action_required"
    if values.get("next_action") or values.get("submission_status") == "documents_requested":
        return "action_required"
    if values.get("decision_status") in {"rejected", "appeal"}:
        return "action_required"
    if values.get("reimbursement_status") == "mismatch":
        return "action_required"
    if values.get("submission_status") in {"not_submitted", "ready"} and int(
        values.get("expected_minor") or 0
    ) > 0:
        return "action_required"
    if values.get("decision_status") in {"processing"}:
        return "waiting"
    if values.get("reimbursement_status") in {"expected", "partial"}:
        return "waiting"

    paid = values.get("payment_status") in {"paid", "not_required"}
    intentionally_closed = values.get("submission_status") in {"held_for_bonus", "not_planned"}
    cash_closed = values.get("reimbursement_status") in {"received", "not_expected"}
    decision_closed = values.get("decision_status") in {"received", "partial", "none"}
    if paid and (intentionally_closed or (cash_closed and decision_closed)):
        return "reconciled"
    if values.get("submission_status") == "submitted":
        return "waiting"
    return "draft"


async def _event(
    session: AsyncSession,
    case_id: UUID,
    person_id: UUID,
    event_type: str,
    summary: str,
    details: dict[str, object] | None = None,
) -> None:
    # Bewusst nur fachliche, sanitierte Metadaten. Keine Diagnosen, Dokumenttexte
    # oder freien Notizen in der Ereignistabelle.
    await session.execute(
        text(
            "insert into health_case_events "
            "(insured_person_id, case_id, event_type, summary, details) "
            "values (:person_id, :case_id, :event_type, :summary, cast(:details as jsonb))"
        ),
        {
            "person_id": str(person_id),
            "case_id": str(case_id),
            "event_type": event_type,
            "summary": summary,
            "details": __import__("json").dumps(details or {}),
        },
    )


async def list_people(session: AsyncSession, household_id: UUID) -> list[InsuredPerson]:
    rows = (
        await session.execute(
            text(
                "select id, display_name, birth_date, linked_user_id, is_active, "
                "created_at, updated_at from health_insured_persons "
                "where household_id = :household_id and is_active order by lower(display_name)"
            ),
            {"household_id": str(household_id)},
        )
    ).all()
    return [InsuredPerson(**row._mapping) for row in rows]


async def create_person(
    session: AsyncSession,
    household_id: UUID,
    user_id: UUID,
    payload: InsuredPersonCreate,
) -> InsuredPerson:
    person_id = uuid4()
    try:
        await session.execute(
            text(
                "insert into health_insured_persons "
                "(id, household_id, display_name, birth_date, linked_user_id, created_by) "
                "values (:person_id, :household_id, :display_name, :birth_date, "
                ":linked_user_id, :user_id)"
            ),
            {
                "person_id": str(person_id),
                "household_id": str(household_id),
                "display_name": payload.display_name.strip(),
                "birth_date": payload.birth_date,
                "linked_user_id": str(payload.linked_user_id)
                if payload.linked_user_id
                else None,
                "user_id": str(user_id),
            },
        )
        await session.execute(
            text(
                "insert into health_access_grants "
                "(household_id, insured_person_id, user_id, permission, granted_by) "
                "values (:household_id, :person_id, :user_id, 'manage', :user_id) "
                "on conflict (household_id, insured_person_id, user_id) do nothing"
            ),
            {
                "household_id": str(household_id),
                "person_id": str(person_id),
                "user_id": str(user_id),
            },
        )
        row = (
            await session.execute(
                text(
                    "select id, display_name, birth_date, linked_user_id, is_active, "
                    "created_at, updated_at from health_insured_persons where id = :person_id"
                ),
                {"person_id": str(person_id)},
            )
        ).one()
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="insured_person_already_exists") from exc
    return InsuredPerson(**row._mapping)


async def list_payers(session: AsyncSession, household_id: UUID) -> list[ReimbursementPayer]:
    rows = (
        await session.execute(
            text(
                "select id, payer_type, name, is_active, created_at, updated_at "
                "from reimbursement_payers where household_id = :household_id and is_active "
                "order by lower(name)"
            ),
            {"household_id": str(household_id)},
        )
    ).all()
    return [ReimbursementPayer(**row._mapping) for row in rows]


async def create_payer(
    session: AsyncSession, household_id: UUID, payload: ReimbursementPayerCreate
) -> ReimbursementPayer:
    try:
        row = (
            await session.execute(
                text(
                    "insert into reimbursement_payers (household_id, payer_type, name) "
                    "values (:household_id, :payer_type, :name) "
                    "returning id, payer_type, name, is_active, created_at, updated_at"
                ),
                {
                    "household_id": str(household_id),
                    "payer_type": payload.payer_type,
                    "name": payload.name.strip(),
                },
            )
        ).one()
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="reimbursement_payer_already_exists") from exc
    return ReimbursementPayer(**row._mapping)


async def _require_case(
    session: AsyncSession, household_id: UUID, case_id: UUID, *, for_update: bool = False
) -> Row[Any]:
    suffix = " for update" if for_update else ""
    row = (
        await session.execute(
            text(
                f"select {_CASE_COLUMNS} from health_expense_cases c "
                "join health_insured_persons p on p.id = c.insured_person_id "
                "and p.household_id = c.household_id "
                "where c.id = :case_id and c.household_id = :household_id" + suffix
            ),
            {"case_id": str(case_id), "household_id": str(household_id)},
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="reimbursement_case_not_found")
    return row


async def list_cases(
    session: AsyncSession,
    household_id: UUID,
    *,
    year: int | None = None,
    person_id: UUID | None = None,
    overall_status: str | None = None,
    search: str | None = None,
) -> list[ReimbursementCase]:
    conditions = ["c.household_id = :household_id"]
    params: dict[str, object] = {"household_id": str(household_id)}
    if year is not None:
        conditions.append("extract(year from c.invoice_date) = :year")
        params["year"] = year
    if person_id is not None:
        conditions.append("c.insured_person_id = :person_id")
        params["person_id"] = str(person_id)
    if overall_status is not None:
        conditions.append("c.overall_status = :overall_status")
        params["overall_status"] = overall_status
    if search and search.strip():
        conditions.append(
            "(c.provider_name ilike :search or coalesce(c.invoice_number, '') ilike :search "
            "or p.display_name ilike :search)"
        )
        params["search"] = f"%{search.strip()}%"
    rows = (
        await session.execute(
            text(
                f"select {_CASE_COLUMNS} from health_expense_cases c "
                "join health_insured_persons p on p.id = c.insured_person_id "
                "and p.household_id = c.household_id where "
                + " and ".join(conditions)
                + " order by (c.overall_status = 'action_required') desc, "
                "c.next_action_due nulls last, c.invoice_date desc, c.created_at desc"
            ),
            params,
        )
    ).all()
    return [ReimbursementCase(**row._mapping) for row in rows]


async def get_case(session: AsyncSession, household_id: UUID, case_id: UUID) -> ReimbursementCase:
    return ReimbursementCase(**(await _require_case(session, household_id, case_id))._mapping)


async def create_case(
    session: AsyncSession,
    household_id: UUID,
    payload: ReimbursementCaseCreate,
) -> ReimbursementCase:
    values = payload.model_dump()
    values["overall_status"] = _derive_overall_status(values)
    values["household_id"] = str(household_id)
    values["insured_person_id"] = str(payload.insured_person_id)
    columns = ", ".join(values)
    placeholders = ", ".join(f":{key}" for key in values)
    try:
        case_id = (
            await session.execute(
                text(
                    f"insert into health_expense_cases ({columns}) values ({placeholders}) "
                    "returning id"
                ),
                values,
            )
        ).scalar_one()
    except IntegrityError as exc:
        raise HTTPException(status_code=400, detail="invalid_reimbursement_case") from exc
    await _event(
        session,
        UUID(str(case_id)),
        payload.insured_person_id,
        "created",
        "Erstattungsvorgang angelegt",
        {"billed_minor": payload.billed_minor, "currency": payload.currency},
    )
    return await get_case(session, household_id, UUID(str(case_id)))


async def update_case(
    session: AsyncSession,
    household_id: UUID,
    case_id: UUID,
    payload: ReimbursementCaseUpdate,
) -> ReimbursementCase:
    current = await _require_case(session, household_id, case_id, for_update=True)
    changes = payload.model_dump(exclude_unset=True, exclude={"clear_fields"})
    for field in payload.clear_fields:
        changes[field] = None
    if not changes:
        return ReimbursementCase(**current._mapping)
    if "reimbursed_minor" in changes:
        changes["cash_verification_required"] = False

    state = dict(current._mapping)
    state.update(changes)
    if state["reimbursed_minor"] > state["billed_minor"]:
        raise HTTPException(status_code=422, detail="reimbursed_exceeds_billed")
    if state["deductible_minor"] + state["noncovered_minor"] > state["billed_minor"]:
        raise HTTPException(status_code=422, detail="own_share_exceeds_billed")
    changes["overall_status"] = _derive_overall_status(state)
    changes["updated_at"] = datetime.now(UTC)
    set_sql = ", ".join(f"{field} = :{field}" for field in changes)
    await session.execute(
        text(
            f"update health_expense_cases set {set_sql}, version = version + 1 "
            "where id = :case_id and household_id = :household_id"
        ),
        {**changes, "case_id": str(case_id), "household_id": str(household_id)},
    )
    await _event(
        session,
        case_id,
        UUID(str(current.person_id)),
        "updated",
        "Erstattungsvorgang aktualisiert",
        {"fields": sorted(changes)},
    )
    return await get_case(session, household_id, case_id)


async def get_summary(session: AsyncSession, household_id: UUID, year: int) -> ReimbursementSummary:
    row = (
        await session.execute(
            text(
                "select "
                "count(*) filter (where overall_status = 'action_required') as actionable_count, "
                "count(*) filter (where overall_status = 'waiting') as processing_count, "
                "coalesce(sum(greatest(expected_minor - effective_received, 0)), 0) "
                "as expected_minor, "
                "coalesce(sum(effective_received), 0) as received_minor, "
                "coalesce(sum(deductible_minor + noncovered_minor), 0) as final_own_share_minor, "
                "coalesce(sum(case when overall_status = 'action_required' then "
                "  greatest(case when expected_minor > 0 then expected_minor else billed_minor end "
                "           - effective_received - deductible_minor - noncovered_minor, 0) "
                "else 0 end), 0) as unresolved_minor "
                "from (select c.*, case when c.cash_verification_required then "
                "coalesce((select sum(a.amount_minor) from health_transaction_allocations a "
                "where a.case_id = c.id and a.allocation_type = 'reimbursement'), 0) "
                "else c.reimbursed_minor end as effective_received "
                "from health_expense_cases c) c where household_id = :household_id "
                "and extract(year from invoice_date) = :year"
            ),
            {"household_id": str(household_id), "year": year},
        )
    ).one()
    return ReimbursementSummary(year=year, **row._mapping)


def _norm(value: str | None) -> str:
    return "".join(character for character in (value or "").casefold() if character.isalnum())


def _person_name_match(left: str | None, right: str | None) -> bool | None:
    """Anzeigenamen duerfen Kurzformen sein; zwei volle Namen bleiben unterscheidbar."""
    first = re.findall(r"[^\W\d_]+", (left or "").casefold())
    second = re.findall(r"[^\W\d_]+", (right or "").casefold())
    if not first or not second:
        return None
    if set(first) == set(second) or set(first).issubset(second) or set(second).issubset(first):
        return True
    return False if len(first) >= 2 and len(second) >= 2 else None


def _digits(value: str | None) -> str:
    return re.sub(r"\D", "", value or "")


def _invoice_key(value: str | None) -> str:
    """Trennzeichen vereinheitlichen, Buchstaben und fuehrende Nullen behalten."""
    return re.sub(r"[\W_]", "", (value or "").casefold())


def _reference_keys(number: str | None) -> set[str]:
    """Schreibweisen, unter denen eine Belegnummer im Verwendungszweck auftaucht.

    Private Krankenversicherer drucken die Abrechnungsnummer als `25 00 00001` (Jahr, Monat,
    laufende Nummer), im Verwendungszweck steht aber `0025 00001` - Monat und
    Jahr vertauscht. Beide Schreibweisen zaehlen deshalb als Treffer. Kuerzere
    Nummern bleiben aussen vor: fuenf Ziffern finden sich in fast jedem
    Verwendungszweck zufaellig wieder.
    """
    plain = _invoice_key(number)
    if len(plain) < 6:
        return set()
    keys = {plain}
    if plain.isdigit() and len(plain) >= 8:
        keys.add(plain[2:4] + plain[0:2] + plain[4:])
    return keys


async def _case_reference_keys(
    session: AsyncSession, household_id: UUID, case_id: UUID
) -> set[str]:
    """Belegnummern aller Dokumente, die an diesem Vorgang haengen."""
    rows = (
        await session.execute(
            text(
                "select distinct on (e.document_id) coalesce("
                "e.confirmed_data ->> 'statement_number', e.confirmed_data ->> 'invoice_number') "
                "as number from document_extractions e "
                "join reimbursement_documents d on d.id = e.document_id "
                "where e.household_id = :household_id and e.confirmed_data is not null "
                "and (d.case_id = :case_id or exists (select 1 from reimbursement_document_links l "
                "where l.document_id = d.id and l.case_id = :case_id)) "
                "order by e.document_id, e.created_at desc"
            ),
            {"household_id": str(household_id), "case_id": str(case_id)},
        )
    ).all()
    keys: set[str] = set()
    for row in rows:
        keys |= _reference_keys(row.number)
    return keys


def _amount_score(candidate: int, target: int) -> tuple[float, str] | None:
    """Betragsnaehe als Punkte - der belastbarste Hinweis, den es gibt."""
    if not target or not candidate:
        return None
    delta = abs(candidate - target)
    if delta <= 1:
        return 0.55, "Betrag stimmt überein"
    if delta / target <= 0.02:
        return 0.38, "Betrag weicht unter 2 % ab"
    if delta / target <= 0.10:
        return 0.20, "Betrag liegt im 10 %-Fenster"
    return None


async def case_candidates(
    session: AsyncSession,
    household_id: UUID,
    document_id: UUID,
    data: dict[str, Any],
) -> list[CaseCandidate]:
    """Vorgaenge, zu denen ein Dokument gehoeren koennte.

    Bewusst kein harter Filter auf die Person: eine Leistungsabrechnung laeuft
    unter dem Versicherungsnehmer, behandelt wurde aber oft ein Kind. Wer hier
    nach Person filtert, blendet genau den richtigen Vorgang aus. Die Person
    ist deshalb ein Signal unter mehreren.
    """
    rows = (
        await session.execute(
            text(
                f"select {_CASE_COLUMNS} from health_expense_cases c "
                "join health_insured_persons p on p.id = c.insured_person_id "
                "and p.household_id = c.household_id "
                "where c.household_id = :household_id "
                "and (cast(:person_id as uuid) is null or "
                "c.insured_person_id = cast(:person_id as uuid)) "
                "and not exists (select 1 from reimbursement_documents d "
                "  where d.case_id = c.id and d.id = :document_id) "
                "order by (regexp_replace(lower(coalesce(c.invoice_number, '')), "
                "'[^[:alnum:]]', '', 'g') = :invoice_key) desc, "
                "abs(c.billed_minor - :billed) asc, c.invoice_date desc limit 400"
            ),
            {"household_id": str(household_id), "document_id": str(document_id),
             "invoice_key": _invoice_key(str(data.get("invoice_number") or "")),
             "person_id": str(data["_insured_person_id"])
             if data.get("_insured_person_id") else None,
             "billed": int(data.get("billed_minor") or 0)},
        )
    ).all()

    billed = int(data.get("billed_minor") or 0)
    reimbursed = int(data.get("reimbursed_minor") or 0)
    number = _invoice_key(str(data.get("invoice_number") or ""))
    provider = _norm(str(data.get("provider_name") or ""))
    document_date = data.get("invoice_date")

    result: list[CaseCandidate] = []
    for row in rows:
        score = 0.0
        evidence: list[str] = []

        # Gleiche Belegnummer ist der staerkste Hinweis - aber nie eine
        # Bedingung: nicht jeder Beleg traegt eine, und nicht jede ist gleich
        # geschrieben.
        case_number = _invoice_key(row.invoice_number)
        number_hit = bool(
            number and case_number and number == case_number
        )
        if number_hit:
            score += 0.70
            evidence.append("Belegnummer stimmt überein")

        amount = _amount_score(billed, int(row.billed_minor))
        if data.get("document_type") != "benefit_statement" and amount is None:
            amount = _amount_score(reimbursed, int(row.billed_minor))
        # Bekannte Widersprueche sind keine positiven Treffer.
        if str(data.get("currency") or "EUR").upper() != row.currency:
            continue
        if number and case_number and number != case_number:
            continue
        person_match = _person_name_match(str(data.get("person_name") or ""), row.person_name)
        if person_match is False:
            continue
        if amount:
            score += amount[0]
            evidence.append(amount[1])

        # Person und Datum bestaetigen einen Treffer, sie begruenden keinen.
        # Ohne Betrag oder Nummer waere sonst jeder Vorgang desselben Monats
        # ein Vorschlag - und die Liste damit wertlos.
        if not number_hit and amount is None:
            continue

        if person_match is True:
            score += 0.18
            evidence.append("Behandelte Person stimmt überein")

        case_provider = _norm(row.provider_name)
        if provider and case_provider and (provider in case_provider or case_provider in provider):
            score += 0.16
            evidence.append("Anbieter stimmt überein")

        if document_date:
            try:
                parsed = date.fromisoformat(str(document_date)[:10])
            except ValueError:
                parsed = None
            if parsed is not None:
                days = (parsed - row.invoice_date).days
                # Die Abrechnung folgt der Rechnung, nie umgekehrt.
                if 0 <= days <= 180:
                    score += 0.14
                    evidence.append(f"Abrechnung {days} Tage nach der Rechnung")
                elif -14 <= days < 0:
                    score += 0.05
                    evidence.append("Datum liegt dicht beieinander")

        if score >= 0.25:
            result.append(
                CaseCandidate(
                    case_id=row.id,
                    person_id=row.person_id,
                    person_name=row.person_name,
                    provider_name=row.provider_name,
                    invoice_number=row.invoice_number,
                    invoice_date=row.invoice_date,
                    billed_minor=row.billed_minor,
                    reimbursed_minor=row.reimbursed_minor,
                    currency=row.currency,
                    overall_status=row.overall_status,
                    score=min(round(score, 3), 1.0),
                    evidence=evidence,
                    version=row.version,
                    expected_minor=row.expected_minor,
                )
            )
    return sorted(result, key=lambda item: (-item.score, -item.invoice_date.toordinal()))[:10]


async def transaction_candidates(
    session: AsyncSession,
    household_id: UUID,
    case_id: UUID,
    allocation_type: str,
) -> list[TransactionCandidate]:
    case = await _require_case(session, household_id, case_id)
    direction = (
        "t.amount_minor < 0" if allocation_type == "provider_payment" else "t.amount_minor > 0"
    )
    target_minor = (
        int(case.billed_minor)
        if allocation_type == "provider_payment"
        else max(int(case.expected_minor) - int(case.reimbursed_minor), 0)
    )
    rows = (
        await session.execute(
            text(
                "select t.id, t.booked_date, t.amount_minor, t.payee_label, t.note, "
                "greatest(abs(t.amount_minor) - coalesce((select sum(a.amount_minor) "
                "from health_transaction_allocations a where a.transaction_id = t.id), 0), 0) "
                "as remaining_minor from transactions t "
                "where t.household_id = :household_id and not t.is_archived and "
                + direction
                + " and t.booked_date between :start_date and :end_date "
                "order by abs(abs(t.amount_minor) - :target_minor), t.booked_date desc limit 400"
            ),
            {
                "household_id": str(household_id),
                "start_date": case.invoice_date - timedelta(days=370),
                "end_date": date.today(),
                "target_minor": target_minor,
            },
        )
    ).all()
    provider = _norm(case.provider_name)
    # Die Belegnummer im Verwendungszweck ist der einzige harte Hinweis - aber
    # sie fehlt je nach Bank und Format. Sie zaehlt deshalb als starkes Signal
    # neben Betrag und Datum, nicht als Bedingung.
    reference_keys = (
        await _case_reference_keys(session, household_id, case_id)
        if allocation_type == "reimbursement"
        else _reference_keys(case.invoice_number)
    )
    result: list[TransactionCandidate] = []
    for row in rows:
        if row.remaining_minor <= 0:
            continue
        evidence: list[str] = []
        score = 0.0
        if reference_keys:
            haystack = _invoice_key(f"{row.note or ''} {row.payee_label or ''}")
            if any(key in haystack for key in reference_keys):
                score += 0.55
                evidence.append("Belegnummer steht im Verwendungszweck")
        if abs(int(row.remaining_minor) - target_minor) <= 1:
            score += 0.62
            evidence.append("Betrag stimmt ueberein")
        elif target_minor and abs(int(row.remaining_minor) - target_minor) / target_minor <= 0.05:
            score += 0.35
            evidence.append("Betrag liegt im 5%-Fenster")
        payee = _norm(row.payee_label)
        if (
            allocation_type == "provider_payment"
            and provider
            and payee
            and (provider in payee or payee in provider)
        ):
            score += 0.28
            evidence.append("Anbieter stimmt ueberein")
        days = abs((row.booked_date - case.invoice_date).days)
        if days <= 45:
            score += 0.10
            evidence.append("Datum liegt nahe an der Rechnung")
        if score >= 0.25:
            result.append(
                TransactionCandidate(
                    transaction_id=row.id,
                    booked_date=row.booked_date,
                    amount_minor=row.amount_minor,
                    payee_label=row.payee_label,
                    note=row.note,
                    score=min(round(score, 3), 1.0),
                    evidence=evidence,
                    remaining_minor=row.remaining_minor,
                )
            )
    return sorted(result, key=lambda item: (-item.score, -item.booked_date.toordinal()))[:20]


async def create_allocation(
    session: AsyncSession,
    household_id: UUID,
    user_id: UUID,
    case_id: UUID,
    payload: TransactionAllocationCreate,
) -> TransactionAllocation:
    case = await _require_case(session, household_id, case_id, for_update=True)
    # Serialisiert auch Zuordnungen derselben Bankbuchung zu verschiedenen
    # Rechnungen, bevor der Datenbank-Trigger die verbleibende Summe prueft.
    transaction = (await session.execute(text(
        "select currency from transactions where id = :id "
        "and household_id = :household_id for update"
    ), {"id": str(payload.transaction_id), "household_id": str(household_id)})).first()
    if transaction is None:
        raise HTTPException(status_code=404, detail="transaction_not_found")
    if transaction.currency != case.currency:
        raise HTTPException(status_code=422, detail="transaction_allocation_currency_mismatch")
    try:
        row = (
            await session.execute(
                text(
                    "insert into health_transaction_allocations "
                    "(household_id, insured_person_id, case_id, submission_id, transaction_id, "
                    "allocation_type, amount_minor, source, confidence, confirmed_by) values "
                    "(:household_id, :person_id, :case_id, :submission_id, :transaction_id, "
                    ":allocation_type, :amount_minor, 'manual', 1.0, :user_id) "
                    "returning id, case_id, submission_id, transaction_id, allocation_type, "
                    "amount_minor, source, confidence, evidence, confirmed_by, created_at"
                ),
                {
                    "household_id": str(household_id),
                    "person_id": str(case.person_id),
                    "case_id": str(case_id),
                    "submission_id": str(payload.submission_id) if payload.submission_id else None,
                    "transaction_id": str(payload.transaction_id),
                    "allocation_type": payload.allocation_type,
                    "amount_minor": payload.amount_minor,
                    "user_id": str(user_id),
                },
            )
        ).one()
    except IntegrityError as exc:
        message = str(getattr(exc, "orig", exc))
        detail = "transaction_allocation_invalid"
        if "HEALTH_TRANSACTION_OVERALLOCATED" in message:
            detail = "transaction_overallocated"
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail) from exc

    await _reconcile_case_after_allocation(
        session, household_id, case_id, payload.allocation_type
    )
    await _event(
        session,
        case_id,
        UUID(str(case.person_id)),
        "transaction_allocated",
        "Bankbuchung zugeordnet",
        {"allocation_type": payload.allocation_type, "amount_minor": payload.amount_minor},
    )
    return TransactionAllocation(**row._mapping)


async def _reconcile_case_after_allocation(
    session: AsyncSession,
    household_id: UUID,
    case_id: UUID,
    allocation_type: str,
) -> None:
    """Rechnet Summe und Status eines Vorgangs aus den Zuordnungen neu.

    Bewusst aus `create_allocation` herausgezogen und nicht dupliziert:
    Anlegen und Entfernen muessen denselben Weg gehen, sonst driften die
    Statuswerte auseinander, sobald eine falsche Zuordnung korrigiert wird.
    Die Summe wird jedes Mal neu gebildet, nie fortgeschrieben - eine
    entfernte Zuordnung faellt damit von allein aus dem Ergebnis.
    """
    case = await _require_case(session, household_id, case_id, for_update=True)
    total = (
        await session.execute(
            text(
                "select coalesce(sum(amount_minor), 0) from health_transaction_allocations "
                "where case_id = :case_id and allocation_type = :allocation_type"
            ),
            {"case_id": str(case_id), "allocation_type": allocation_type},
        )
    ).scalar_one()

    if allocation_type == "provider_payment":
        # `unpaid` statt `partial`, sobald nichts mehr zugeordnet ist: `partial`
        # hiesse "teilweise bezahlt" und waere nach dem Entfernen der letzten
        # Zuordnung schlicht falsch.
        if total <= 0:
            payment_status = "unpaid"
        elif total >= case.billed_minor:
            payment_status = "paid"
        else:
            payment_status = "partial"
        await session.execute(
            text(
                "update health_expense_cases set payment_status = :status, updated_at = now(), "
                "version = version + 1 where id = :case_id"
            ),
            {"status": payment_status, "case_id": str(case_id)},
        )
    else:
        # Ohne Zuordnung faellt der Vorgang auf den Zustand zurueck, den er vor
        # dem Geldeingang hatte: erwartet, wenn eine Erwartung beziffert ist,
        # sonst gar keine Erwartung.
        if total <= 0:
            reimbursement_status = "expected" if case.expected_minor > 0 else "not_expected"
        elif total > case.expected_minor:
            reimbursement_status = "mismatch"
        elif total == case.expected_minor:
            reimbursement_status = "received"
        else:
            reimbursement_status = "partial"
        await session.execute(
            text(
                "update health_expense_cases set reimbursed_minor = :received, "
                "cash_verification_required = false, "
                "reimbursement_status = :status, updated_at = now(), version = version + 1 "
                "where id = :case_id"
            ),
            {"received": total, "status": reimbursement_status, "case_id": str(case_id)},
        )

    refreshed = await _require_case(session, household_id, case_id, for_update=True)
    derived = _derive_overall_status(dict(refreshed._mapping))
    await session.execute(
        text("update health_expense_cases set overall_status = :status where id = :case_id"),
        {"status": derived, "case_id": str(case_id)},
    )


async def list_allocations(
    session: AsyncSession,
    household_id: UUID,
    case_id: UUID,
) -> list[TransactionAllocationView]:
    """Die Zuordnungen eines Vorgangs, mit den Buchungsdaten zum Nachpruefen."""
    await _require_case(session, household_id, case_id)
    rows = (
        await session.execute(
            text(
                "select a.id, a.case_id, a.submission_id, a.transaction_id, a.allocation_type, "
                "a.amount_minor, a.source, a.confidence, a.evidence, a.confirmed_by, "
                "a.created_at, t.booked_date, t.payee_label, "
                "t.amount_minor as transaction_amount_minor "
                "from health_transaction_allocations a "
                "join transactions t on t.id = a.transaction_id "
                "where a.case_id = :case_id and a.household_id = :household_id "
                "order by t.booked_date desc, a.created_at desc"
            ),
            {"case_id": str(case_id), "household_id": str(household_id)},
        )
    ).all()
    return [TransactionAllocationView(**row._mapping) for row in rows]


async def delete_allocation(
    session: AsyncSession,
    household_id: UUID,
    case_id: UUID,
    allocation_id: UUID,
) -> None:
    """Nimmt eine Zuordnung zurueck und rechnet den Vorgang neu.

    Die Haushaltsbedingung steht in der DELETE-Bedingung selbst und nicht in
    einer vorgelagerten Pruefung: eine fremde Zuordnung darf nicht dadurch
    unterscheidbar werden, dass sie anders scheitert als eine nicht
    existierende.
    """
    case = await _require_case(session, household_id, case_id, for_update=True)
    row = (
        await session.execute(
            text(
                "delete from health_transaction_allocations "
                "where id = :allocation_id and case_id = :case_id "
                "and household_id = :household_id "
                "returning allocation_type, amount_minor"
            ),
            {
                "allocation_id": str(allocation_id),
                "case_id": str(case_id),
                "household_id": str(household_id),
            },
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="transaction_allocation_not_found")

    await _reconcile_case_after_allocation(session, household_id, case_id, row.allocation_type)
    await _event(
        session,
        case_id,
        UUID(str(case.person_id)),
        "transaction_allocation_removed",
        "Zuordnung entfernt",
        {"allocation_type": row.allocation_type, "amount_minor": int(row.amount_minor)},
    )
