"""Bestaetigte Bescheide pro Rechnung und Kostentraeger, ohne fingierte Zahlung."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.reimbursements import (
    DocumentCasePreview,
    ReimbursementCase,
    ReimbursementCaseUpdate,
)
from app.services import reimbursement_service


def decision_data(data: dict[str, Any], item_index: int | None) -> dict[str, Any]:
    items = data.get("items") or []
    if items:
        if item_index is None or item_index >= len(items):
            raise HTTPException(status_code=422, detail="statement_item_required")
        # Ein fehlender Positionsbetrag darf nie den Gesamtbetrag erben.
        return {**data, **items[item_index], "expected_minor": None, "reimbursed_minor": None}
    if item_index is not None:
        raise HTTPException(status_code=422, detail="statement_item_not_found")
    return data


def approved_amount(data: dict[str, Any]) -> int | None:
    # Legacy-PDFs nannten den Bescheidbetrag reimbursed_minor. Das ist
    # ausschliesslich ein Importalias, kein Nachweis eines Bankeingangs.
    for key in ("approved_minor", "expected_minor", "reimbursed_minor"):
        if data.get(key) is not None:
            return int(data[key])
    return None


def validate_decision(case: ReimbursementCase, data: dict[str, Any]) -> int:
    amount = approved_amount(data)
    if amount is None:
        raise HTTPException(status_code=422, detail="statement_approved_amount_missing")
    if str(data.get("currency") or "EUR").upper() != case.currency:
        raise HTTPException(status_code=422, detail="statement_case_currency_mismatch")
    if data.get("billed_minor") is not None and int(data["billed_minor"]) != case.billed_minor:
        raise HTTPException(status_code=422, detail="statement_case_amount_mismatch")
    number = data.get("invoice_number")
    if number and number != data.get("statement_number") and case.invoice_number \
            and reimbursement_service._invoice_key(str(number)) != \
            reimbursement_service._invoice_key(case.invoice_number):
        raise HTTPException(status_code=422, detail="statement_case_number_mismatch")
    if amount > case.billed_minor:
        raise HTTPException(status_code=422, detail="statement_approved_exceeds_billed")
    return amount


async def apply_decision(
    session: AsyncSession, household_id: UUID, user_id: UUID,
    document_id: UUID, case: ReimbursementCase, data: dict[str, Any],
    item_index: int | None, payer_id: UUID | None,
) -> ReimbursementCase:
    amount = validate_decision(case, data)
    await session.execute(text(
        "update health_expense_cases set document_baseline = "
        "coalesce(document_baseline, cast(:baseline as jsonb)) where id = :case_id "
        "and household_id = :household_id"
    ), {"case_id": str(case.id), "household_id": str(household_id), "baseline": json.dumps({
        "expected_minor": case.expected_minor, "deductible_minor": case.deductible_minor,
        "noncovered_minor": case.noncovered_minor, "decision_status": case.decision_status,
        "submission_status": case.submission_status,
    })})
    if payer_id is None:
        name = str(data.get("payer_name") or "").strip()
        if not name:
            raise HTTPException(status_code=422, detail="statement_payer_required")
        selected_payer = (
            await session.execute(text(
                "insert into reimbursement_payers (household_id, payer_type, name) "
                "values (:household_id, :payer_type, :name) "
                "on conflict (household_id, lower(name)) do update set name = excluded.name "
                "returning id"
            ), {"household_id": str(household_id), "name": name,
                "payer_type": data.get("payer_type") or "other"})
        ).one()
        payer_id = UUID(str(selected_payer.id))
    else:
        payer = (
            await session.execute(text(
                "select id from reimbursement_payers where id = :id "
                "and household_id = :household_id and is_active"
            ), {"id": str(payer_id), "household_id": str(household_id)})
        ).first()
        if payer is None:
            raise HTTPException(status_code=404, detail="reimbursement_payer_not_found")

    reference = str(data.get("statement_number") or data.get("external_reference")
                    or f"document:{document_id}")
    # Derselbe Bescheid erneut oder korrigiert: aktualisieren, niemals doppelt
    # addieren. Die alte Entscheidung bleibt als sanitierte Historie erhalten.
    await session.execute(text(
        "insert into reimbursement_submissions "
        "(household_id, insured_person_id, case_id, payer_id, external_reference, "
        "source_document_id, source_item_index, claimed_minor, approved_minor, "
        "deductible_minor, noncovered_minor, decided_at, status, decision_source) values "
        "(:household_id, :person_id, :case_id, :payer_id, :reference, :document_id, "
        ":item_index, :billed, :approved, :deductible, :noncovered, now(), 'decided', 'document') "
        "on conflict (household_id, case_id, payer_id, external_reference) "
        "where decision_source = 'document' do update set "
        "decision_history = reimbursement_submissions.decision_history || "
        "jsonb_build_array(jsonb_build_object('approved_minor', "
        "reimbursement_submissions.approved_minor, 'source_document_id', "
        "reimbursement_submissions.source_document_id, 'updated_at', "
        "reimbursement_submissions.updated_at)), "
        "source_document_id = excluded.source_document_id, "
        "source_item_index = excluded.source_item_index, "
        "claimed_minor = excluded.claimed_minor, approved_minor = excluded.approved_minor, "
        "deductible_minor = excluded.deductible_minor, "
        "noncovered_minor = excluded.noncovered_minor, "
        "decided_at = now(), status = 'decided', updated_at = now()"
    ), {
        "household_id": str(household_id), "person_id": str(case.person_id),
        "case_id": str(case.id), "payer_id": str(payer_id), "reference": reference,
        "document_id": str(document_id), "item_index": item_index,
        "billed": case.billed_minor, "approved": amount,
        "deductible": int(data.get("deductible_minor") or 0),
        "noncovered": int(data.get("noncovered_minor") or 0),
    })
    case = await refresh_case(session, household_id, case)
    await reimbursement_service._event(
        session, case.id, case.person_id, "decision_applied", "Bescheid uebernommen",
        {"document_id": str(document_id), "payer_id": str(payer_id),
         "approved_minor": amount, "item_index": item_index, "user_id": str(user_id)},
    )
    return case


async def refresh_case(session: AsyncSession, household_id: UUID,
                        case: ReimbursementCase) -> ReimbursementCase:
    totals = (
        await session.execute(text(
            "select coalesce(sum(approved_minor), 0) as approved, "
            "coalesce(sum(deductible_minor), 0) as deductible, "
            "coalesce(sum(noncovered_minor), 0) as noncovered, count(*) as decision_count "
            "from reimbursement_submissions where household_id = :household_id "
            "and case_id = :case_id and decision_source = 'document' and status = 'decided'"
        ), {"household_id": str(household_id), "case_id": str(case.id)})
    ).one()
    decision_status = "received"
    submission_status = "submitted"
    approved, deductible, noncovered = totals.approved, totals.deductible, totals.noncovered
    if totals.decision_count == 0:
        row = (await session.execute(text(
            "select document_baseline from health_expense_cases where id = :id "
            "and household_id = :household_id"
        ), {"id": str(case.id), "household_id": str(household_id)})).one()
        baseline = row.document_baseline or {}
        approved = int(baseline.get("expected_minor", 0))
        deductible = int(baseline.get("deductible_minor", 0))
        noncovered = int(baseline.get("noncovered_minor", 0))
        decision_status = baseline.get("decision_status", "none")
        submission_status = baseline.get("submission_status", "not_submitted")
    if totals.approved > case.billed_minor \
            or totals.deductible + totals.noncovered > case.billed_minor:
        raise HTTPException(status_code=422, detail="statement_totals_exceed_invoice")
    received = case.reimbursed_minor
    status = (
        "mismatch" if received > approved else
        "received" if received > 0 and received == approved else
        "partial" if received > 0 else "expected" if approved > 0 else "not_expected"
    )
    return await reimbursement_service.update_case(
        session, household_id, case.id,
        ReimbursementCaseUpdate.model_validate({
            "expected_minor": approved, "deductible_minor": deductible,
            "noncovered_minor": noncovered, "submission_status": submission_status,
            "decision_status": decision_status, "reimbursement_status": status,
        }),
    )


async def preview_decision(session: AsyncSession, household_id: UUID, document_id: UUID,
                            case: ReimbursementCase, data: dict[str, Any],
                            payer_id: UUID | None) -> DocumentCasePreview:
    amount = validate_decision(case, data)
    reference = str(data.get("statement_number") or data.get("external_reference")
                    or f"document:{document_id}")
    rows = (await session.execute(text(
        "select s.approved_minor, s.payer_id, s.external_reference, p.name "
        "from reimbursement_submissions s join reimbursement_payers p on p.id = s.payer_id "
        "where s.household_id = :household_id and s.case_id = :case_id "
        "and s.decision_source = 'document' and s.status = 'decided'"
    ), {"household_id": str(household_id), "case_id": str(case.id)})).all()
    existing = sum(int(row.approved_minor) for row in rows if not (
        row.external_reference == reference and (
            str(row.payer_id) == str(payer_id) if payer_id is not None else
            str(row.name).casefold() == str(data.get("payer_name") or "").strip().casefold()
        )
    ))
    after = existing + amount
    if after > case.billed_minor:
        raise HTTPException(status_code=422, detail="statement_totals_exceed_invoice")
    return DocumentCasePreview(
        case_id=case.id, case_version=case.version, expected_before_minor=case.expected_minor,
        expected_after_minor=after, received_minor=case.reimbursed_minor,
        outstanding_after_minor=max(after - case.reimbursed_minor, 0), approved_minor=amount,
        currency=case.currency,
    )


async def link_item(session: AsyncSession, household_id: UUID, document_id: UUID,
                    case: ReimbursementCase, item_index: int | None) -> None:
    existing = (
        await session.execute(text(
            "select case_id from reimbursement_document_links where household_id = :household_id "
            "and document_id = :document_id and item_index = :item_index for update"
        ), {"household_id": str(household_id), "document_id": str(document_id),
            "item_index": item_index if item_index is not None else -1})
    ).first()
    if existing is not None and str(existing.case_id) != str(case.id):
        raise HTTPException(status_code=409, detail="statement_item_already_linked")
    linked = await session.execute(text(
        "insert into reimbursement_document_links "
        "(household_id, document_id, case_id, insured_person_id, item_index) "
        "values (:household_id, :document_id, :case_id, :person_id, :item_index) "
        "on conflict (household_id, document_id, item_index) do update "
        "set case_id = excluded.case_id "
        "where reimbursement_document_links.case_id = excluded.case_id "
        "returning case_id"
    ), {"household_id": str(household_id), "document_id": str(document_id),
        "case_id": str(case.id), "person_id": str(case.person_id),
        "item_index": item_index if item_index is not None else -1})
    if linked.first() is None:
        raise HTTPException(status_code=409, detail="statement_item_already_linked")
